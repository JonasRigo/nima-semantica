"""Single-writer Semantica persistence with immutable artifact publication."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import ConflictError, NimaError, Record, canonical, identity
from .graph_persistence import GRAPH_SCHEMA, GraphPersistence


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class GraphStore(GraphPersistence):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "graph.sqlite3"
        if (self.root / "graph.json").exists():
            raise NimaError("incompatible store; use a fresh development directory (migration is unsupported)")
        if self.path.exists():
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as existing:
                if existing.execute("PRAGMA user_version").fetchone()[0] != 2:
                    raise NimaError("incompatible store schema; use a fresh development directory")
        self._mutex = threading.RLock()
        self._lease = open(self.root / "writer.lock", "a+b")
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lease.close()
            raise ConflictError("another service owns this corpus") from None
        try:
            self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, corpus_id TEXT NOT NULL,
                    project_id TEXT, document_id TEXT, ordinal INTEGER, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_scope ON records(corpus_id,kind,project_id);
                CREATE INDEX IF NOT EXISTS records_kind ON records(kind,project_id);
                CREATE INDEX IF NOT EXISTS records_document ON records(corpus_id,document_id,ordinal);
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
                    receipt_id TEXT NOT NULL, PRIMARY KEY(source,target,relation,receipt_id));
                CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
                CREATE TABLE IF NOT EXISTS generations (
                    corpus_id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO state VALUES ('revision',0);
            """)
            self._db.executescript(GRAPH_SCHEMA)
            self._db.execute("PRAGMA user_version=2")
            self._in_transaction = False
            self._transaction_artifacts = set()
            self._vector_cache = {}
            self._preparation_cache = {}
        except BaseException:
            self.close()
            raise

    def close(self):
        if hasattr(self, "_vector_cache"):
            self._vector_cache.clear()
        if hasattr(self, "_preparation_cache"):
            self._preparation_cache.clear()
        if hasattr(self, "_db"):
            self._db.close()
        self._lease.close()

    @property
    def revision(self) -> str:
        with self._mutex:
            value = self._db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
            return "sqlite:" + str(value) if value else "empty"

    @contextmanager
    def transaction(self, expected_revision: str | None = None):
        with self._mutex:
            if expected_revision is not None and self.revision != expected_revision:
                raise ConflictError("stale graph revision")
            if self._in_transaction:
                raise NimaError("nested store transaction")
            self._db.execute("BEGIN IMMEDIATE")
            self._in_transaction = True
            self._transaction_artifacts = set()
            try:
                yield self
                self._db.execute("COMMIT")
            except BaseException:
                try:
                    self._db.execute("ROLLBACK")
                finally:
                    self._rollback_new_artifacts()
                    self._vector_cache.clear()
                    self._preparation_cache.clear()
                raise
            finally:
                self._transaction_artifacts.clear()
                self._in_transaction = False

    def _rollback_new_artifacts(self):
        """Discard only blobs created by this failed transaction and still unregistered."""
        for digest in self._transaction_artifacts:
            registered = self._db.execute("""SELECT 1 FROM records WHERE kind='SystemRegistryEntry'
                AND json_extract(payload,'$.content.artifact_id')=? LIMIT 1""", (digest,)).fetchone()
            if registered is None:
                (self.root / "artifacts" / digest).unlink(missing_ok=True)

    @contextmanager
    def joined_transaction(self, expected_revision: str | None = None):
        """Join a transaction on this thread, or create an atomic outer boundary."""
        with self._mutex:
            if expected_revision is not None and self.revision != expected_revision:
                raise ConflictError("stale store revision")
            if self._in_transaction:
                yield self
            else:
                with self.transaction(expected_revision):
                    yield self

    def put(self, record: Record) -> str:
        with self._mutex:
            if not self._in_transaction:
                with self.transaction():
                    return self.put(record)
            inserted = self._db.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,?)",
                (record.id, record.kind, record.corpus_id, record.project_id,
                 record.content.get("source_id", record.content.get("document_id")) if record.kind == "SourceRegion" else None,
                 record.content.get("ordinal") if record.kind == "SourceRegion" else None, canonical(record).decode())).rowcount
            if inserted:
                self._db.execute("UPDATE state SET value=value+1 WHERE key='revision'")
                if record.kind in ("EmbeddingBatch", "SourceRegion", "SourcePreparation"):
                    generation = "embedding:" + identity([self.embedding_revision(record.corpus_id), record.id])
                    self._db.execute("INSERT INTO generations VALUES (?,?) ON CONFLICT(corpus_id) DO UPDATE SET value=excluded.value", (record.corpus_id,generation))
        return record.id

    def link(self, source: str, target: str, relation: str, receipt_id: str):
        with self._mutex:
            if not self._in_transaction:
                with self.transaction():
                    return self.link(source, target, relation, receipt_id)
            left, right = self.get(source), self.get(target)
            if left is None or right is None:
                raise NimaError("relationship endpoint absent")
            if self._db.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?,?)", (source,target,relation,receipt_id)).rowcount:
                self._db.execute("UPDATE state SET value=value+1 WHERE key='revision'")

    def records(self, kind: str | None = None, project_id: str | None = None, corpus_id: str | None = None) -> list[tuple[str, Record]]:
        with self._mutex:
            clauses, args = [], []
            for key, value in (("kind",kind),("corpus_id",corpus_id)):
                if value is not None:
                    clauses.append(key + "=?")
                    args.append(value)
            if project_id is not None:
                clauses.append("(project_id IS NULL OR project_id=?)")
                args.append(project_id)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            return [(i,self._decode(i,p)) for i,p in self._db.execute("SELECT id,payload FROM records" + where + " ORDER BY rowid",args)]

    @staticmethod
    def _decode(record_id, payload):
        record = Record.model_validate_json(payload)
        if record.id != record_id:
            raise NimaError("canonical record integrity failure")
        return record

    def normalized_documents(self, document_id, artifact_id, *, corpus_id, project_id=None):
        """Return normalization metadata only for the selected document/artifact."""
        with self._mutex:
            rows = self._db.execute("""SELECT id,payload FROM records
                WHERE kind='NormalizedDocument' AND corpus_id=?
                AND (? IS NULL OR project_id IS NULL OR project_id=?)
                AND json_extract(payload,'$.content.artifact_id')=?
                AND EXISTS (SELECT 1 FROM json_each(records.payload,'$.parents') WHERE value=?)
                ORDER BY rowid""", (corpus_id, project_id, project_id, artifact_id, document_id))
            return [self._decode(record_id, payload) for record_id, payload in rows]

    def inspect_project(self, *, corpus_id, project_id, offset=0, limit=25):
        """Read a bounded metadata page and exact-project counts, not source text."""
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            raise ValueError("invalid inspection page")
        with self._mutex:
            scope = (corpus_id, project_id)
            counts = dict(self._db.execute(
                "SELECT kind,count(*) FROM records WHERE corpus_id=? AND project_id=? GROUP BY kind", scope
            ).fetchall())
            rows = self._db.execute(
                "SELECT id,payload FROM records WHERE corpus_id=? AND project_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                (*scope, limit, offset),
            ).fetchall()
            records = [self._decode(i, p) for i, p in rows]
            return {"record_kinds": counts, "record_count": sum(counts.values()),
                    "offset": offset, "has_more": offset + len(records) < sum(counts.values()),
                    "findings": [{"record_id": r.id, "kind": r.kind,
                                  "status": r.content.get("status", "unspecified"),
                                  "name": str(r.content.get("name", ""))[:1024],
                                  "parent_count": len(r.parents)} for r in records]}

    def get(self, record_id, *, corpus_id=None, project_id=None):
        """Fetch one canonical record, optionally constrained to a scope."""
        with self._mutex:
            row = self._db.execute("SELECT payload,corpus_id,project_id FROM records WHERE id=?", (record_id,)).fetchone()
            if row is None or (corpus_id is not None and row[1] != corpus_id) or (project_id is not None and row[2] not in (None,project_id)):
                return None
            return self._decode(record_id,row[0])

    def embedding_revision(self, corpus_id):
        with self._mutex:
            row = self._db.execute("SELECT value FROM generations WHERE corpus_id=?",(corpus_id,)).fetchone()
            return row[0] if row else 0

    def get_selected(self, record_ids, *, kind=None, corpus_id=None, project_id=None):
        """Resolve only explicit IDs, in request order; absent/out-of-scope IDs are omitted."""
        with self._mutex:
            result = []
            for record_id in record_ids:
                record = self.get(record_id, corpus_id=corpus_id, project_id=project_id)
                if record is not None and (kind is None or record.kind == kind):
                    result.append((record_id, record))
            return result

    def prepared_region_window(self, *, corpus_id, project_id, limit=512):
        """Bounded exact-project source window for transparent lexical retrieval.

        No legacy/global-record fallback. Preparation membership is checked in
        SQL so neither corpus size nor an unscoped record can inflate the window.
        """
        if not 1 <= limit <= 2048:
            raise ValueError("source search window exceeds budget")
        with self._mutex:
            rows = self._db.execute("""SELECT r.id,r.payload FROM records r
                WHERE r.kind='SourceRegion' AND r.corpus_id=? AND (r.project_id IS NULL OR r.project_id=?)
                AND EXISTS (SELECT 1 FROM records p, json_each(p.payload,'$.content.region_ids') ids
                    WHERE p.kind='SourcePreparation' AND p.corpus_id=r.corpus_id
                    AND p.project_id=?
                    AND json_extract(p.payload,'$.content.status')='prepared' AND ids.value=r.id)
                ORDER BY r.rowid LIMIT ?""", (corpus_id, project_id, project_id, limit + 1)).fetchall()
            return [self._decode(i, p) for i, p in rows[:limit]], len(rows) > limit

    def preparation_membership(self, corpus_id, project_id):
        """Successful exact-project preparation IDs, or None for legacy corpora.

        Once a corpus uses preparation receipts, projects without successful
        receipts have empty membership. Warm calls never scan preparation records.
        """
        if project_id is None:
            return None
        with self._mutex:
            key = (corpus_id, project_id, self.embedding_revision(corpus_id))
            if key in self._preparation_cache:
                return self._preparation_cache[key]
            exists = self._db.execute("SELECT 1 FROM records WHERE corpus_id=? AND kind='SourcePreparation' LIMIT 1", (corpus_id,)).fetchone()
            allowed = None
            if exists:
                allowed = frozenset(region_id for _, record in self.records("SourcePreparation", corpus_id=corpus_id, project_id=project_id)
                    if record.project_id == project_id and record.content.get("status") == "prepared"
                    for region_id in record.content.get("region_ids", ()))
            for previous in list(self._preparation_cache):
                if previous[:2] == key[:2]:
                    del self._preparation_cache[previous]
            while len(self._preparation_cache) >= 2:
                del self._preparation_cache[next(iter(self._preparation_cache))]
            self._preparation_cache[key] = allowed
            return allowed

    def neighbors(self, record_id, *, corpus_id, project_id=None, limit=64):
        """Bounded undirected adjacency with typed, receipt-backed edge evidence."""
        if limit < 0:
            raise ValueError("negative adjacency limit")
        with self._mutex:
            if self.get(record_id,corpus_id=corpus_id,project_id=project_id) is None:
                return []
            return [dict(zip(("id","relation","receipt_id","source","target"),row)) for row in self._db.execute("""
                SELECT r.id,e.relation,e.receipt_id,e.source,e.target FROM (
                    SELECT target AS neighbor,relation,receipt_id,source,target FROM edges WHERE source=?
                    UNION ALL SELECT source AS neighbor,relation,receipt_id,source,target FROM edges WHERE target=?
                ) e JOIN records r ON r.id=e.neighbor
                WHERE r.corpus_id=? AND (? IS NULL OR r.project_id IS NULL OR r.project_id=?)
                ORDER BY CASE WHEN e.relation='same_normalized_document' THEN 1 ELSE 0 END,
                    r.id,e.relation,e.receipt_id,e.source,e.target LIMIT ?
                """,(record_id,record_id,corpus_id,project_id,project_id,limit))]

    def region_neighbors(self, record_id, *, corpus_id, project_id=None):
        with self._mutex:
            record = self.get(record_id,corpus_id=corpus_id,project_id=project_id)
            if record is None or record.kind != "SourceRegion":
                return []
            return [row[0] for row in self._db.execute("""SELECT id FROM records WHERE corpus_id=?
                AND kind='SourceRegion' AND document_id=? AND ordinal IN (?,?)
                AND (? IS NULL OR project_id IS NULL OR project_id=?) ORDER BY ordinal LIMIT 2""",
                (corpus_id,record.content.get("source_id", record.content.get("document_id")),record.content["ordinal"]-1,record.content["ordinal"]+1,project_id,project_id))]

    def artifact(self, data: bytes) -> str:
        with self._mutex:
            digest = hashlib.sha256(data).hexdigest()
            path = self.root / "artifacts" / digest
            if path.is_symlink() or path.resolve().parent != (self.root / "artifacts").resolve():
                raise NimaError("artifact path rejected")
            if not path.exists():
                atomic_bytes(path, data)
                if self._in_transaction:
                    self._transaction_artifacts.add(digest)
            elif path.read_bytes() != data:
                raise NimaError("artifact integrity failure")
            return digest

    def read_artifact(self, artifact_id: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", artifact_id):
            raise NimaError("invalid artifact identifier")
        path = self.root / "artifacts" / artifact_id
        if path.is_symlink() or path.resolve().parent != (self.root / "artifacts").resolve():
            raise NimaError("artifact path rejected")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise NimaError("artifact not found") from None
        if hashlib.sha256(data).hexdigest() != artifact_id:
            raise NimaError("artifact integrity failure")
        return data
