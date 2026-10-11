"""Operator-scoped, consistent SQLite reads alongside the live corpus owner."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

from .models import ConflictError, NimaError
from .storage import GraphStore


class CorpusSnapshot(GraphStore):
    """Reuse authenticated readers; no writer lease, schema creation or writes."""
    read_only = True

    def __init__(self, root, *, corpus_id, project_id):
        self.root = Path(root).resolve()
        self.path = self.root / "graph.sqlite3"
        self.corpus_id, self.project_id = corpus_id, project_id
        self._mutex = threading.RLock()
        self._vector_cache, self._preparation_cache = {}, {}
        self._in_transaction = False
        self._db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                   isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA query_only=ON")
        self._db.execute("BEGIN")
        if self._db.execute("PRAGMA user_version").fetchone()[0] != 2:
            self.close()
            raise NimaError("incompatible corpus snapshot schema")
        self.snapshot_revision = self.revision

    def close(self):
        self._db.close()

    def refresh(self):
        with self._mutex:
            self._db.execute("ROLLBACK")
            self._db.execute("BEGIN")
            self._vector_cache.clear(); self._preparation_cache.clear()
            self.snapshot_revision = self.revision

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _scope(self, corpus_id, project_id):
        if corpus_id not in (None, self.corpus_id) or project_id not in (None, self.project_id):
            raise ConflictError("foreign corpus snapshot scope")
        return self.corpus_id, self.project_id

    def get(self, record_id, *, corpus_id=None, project_id=None):
        corpus_id, project_id = self._scope(corpus_id, project_id)
        return super().get(record_id, corpus_id=corpus_id, project_id=project_id)

    def records(self, kind=None, project_id=None, corpus_id=None):
        corpus_id, project_id = self._scope(corpus_id, project_id)
        return super().records(kind, project_id, corpus_id)

    def iter_records(self, kind=None, project_id=None, corpus_id=None):
        corpus_id, project_id = self._scope(corpus_id, project_id)
        return super().iter_records(kind, project_id, corpus_id)

    def receipt_record(self, receipt_id, *, corpus_id, project_id=None):
        self._scope(corpus_id, project_id)
        return super().receipt_record(receipt_id, corpus_id=corpus_id, project_id=project_id)

    def records_matching(self, kind, field, value, *, corpus_id, project_id=None):
        corpus_id, project_id = self._scope(corpus_id, project_id)
        return super().records_matching(kind, field, value, corpus_id=corpus_id, project_id=project_id)

    def graph_revision(self, corpus_id, project_id=None):
        self._scope(corpus_id, project_id)
        return super().graph_revision(corpus_id, project_id)

    def read_okf_snapshot(self, *, corpus_id, project_id=None, revision=None):
        self._scope(corpus_id, project_id)
        return super().read_okf_snapshot(corpus_id=corpus_id, project_id=project_id, revision=revision)

    def enumerate_records(self, *, kind, after=None, limit=25, expected_revision=None):
        """Complete deterministic enumeration, distinct from ranked retrieval."""
        if not 1 <= limit <= 100:
            raise ValueError("record page limit must be in [1, 100]")
        if expected_revision is not None and expected_revision != self.revision:
            raise ConflictError("record enumeration snapshot changed; restart enumeration")
        rows = self._db.execute("SELECT id FROM records WHERE kind=? AND corpus_id=? "
            "AND (project_id IS NULL OR project_id=?) AND id>? ORDER BY id LIMIT ?",
            (kind, self.corpus_id, self.project_id, after or "", limit + 1)).fetchall()
        ids = [row[0] for row in rows[:limit]]
        return {"record_ids": ids, "next_cursor": ids[-1] if len(rows) > limit else None,
                "has_more": len(rows) > limit, "store_revision": self.revision,
                "scope": {"corpus_id": self.corpus_id, "project_id": self.project_id}}

    @staticmethod
    def _deny(*args, **kwargs):
        raise ConflictError("read-only corpus snapshot: writes must go through the corpus owner")

    put = link = artifact = persist_graph_commit = _deny

    @contextmanager
    def transaction(self, expected_revision=None):
        self._deny()
        yield

    @contextmanager
    def joined_transaction(self, expected_revision=None):
        with self._mutex:
            if expected_revision is not None and expected_revision != self.revision:
                raise ConflictError("stale corpus snapshot revision")
            yield self
