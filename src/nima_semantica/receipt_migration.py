"""Resumable authenticated representation migration and separate SQLite maintenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from .execution_receipts import ExecutionReceiptService
from .models import canonical
from .storage import atomic_bytes


def verified_backup(database, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("backup destination already exists")
    with sqlite3.connect(database) as source, sqlite3.connect(destination) as backup:
        source.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite backup integrity check failed")
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(destination), "sha256": digest,
            "bytes": destination.stat().st_size}


def migrate_proof_receipts(service, session_id, expected_revision, directory, *, checkpoint=None):
    """Caller holds the database owner. Each checkpoint is independently durable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "migration.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest["database"], manifest["session_id"], manifest["expected_revision"]) != (
                str(service.path.resolve()), session_id, expected_revision):
            raise ValueError("migration resume binding mismatch")
        backup = Path(manifest["backup"]["path"])
        with backup.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["backup"]["sha256"]:
                raise ValueError("migration backup digest mismatch")
    else:
        manifest = {"format_version": 1, "database": str(service.path.resolve()), "session_id": session_id,
                    "expected_revision": expected_revision, "status": "authenticating", "references": {},
                    "backup": verified_backup(service.path, directory / "before.sqlite3")}
        atomic_bytes(manifest_path, canonical(manifest))
    with service._connect() as db:
        row, state = service._load(db, session_id)
        if row["revision"] != expected_revision or row["active"] is not None:
            raise ValueError("migration requires the expected revision and an idle session")
        before = row["state"]
        if manifest["status"] == "complete":
            if hashlib.sha256(before.encode()).hexdigest() != manifest.get("after_state_sha256"):
                raise ValueError("completed migration state changed")
            return manifest
        semantics = {k: v for k, v in state.items() if k != "nodes"}
        semantics["nodes"] = {k: {f: v for f, v in n.items() if f not in {"receipt", "receipt_reference"}}
                              for k, n in state["nodes"].items()}
    with service._research_store() as store:
        receipts = ExecutionReceiptService(store)
        for node_id, node in state["nodes"].items():
            if "receipt" in node:
                old = node["receipt"]
                reference = receipts.reference(old["receipt_id"], corpus_id=service.config.corpus_id,
                                               project_id=service.config.project_id)
                resolved = receipts.resolve(reference, corpus_id=service.config.corpus_id, project_id=service.config.project_id)
                if canonical(old) != canonical(resolved.model_dump(mode="json")):
                    raise ValueError("embedded receipt differs from authoritative payload")
                node["receipt_reference"] = reference.model_dump(mode="json")
                del node["receipt"]
                manifest["references"][node_id] = node["receipt_reference"]
                atomic_bytes(manifest_path, canonical(manifest))
                if checkpoint:
                    checkpoint(node_id)
            elif "receipt_reference" in node:
                receipts.resolve(node["receipt_reference"], corpus_id=service.config.corpus_id,
                                 project_id=service.config.project_id)
    after_semantics = {k: v for k, v in state.items() if k != "nodes"}
    after_semantics["nodes"] = {k: {f: v for f, v in n.items() if f not in {"receipt", "receipt_reference"}}
                               for k, n in state["nodes"].items()}
    if canonical(semantics) != canonical(after_semantics):
        raise ValueError("migration changed scientific/session semantics")
    with service._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current, _ = service._load(db, session_id)
        if (current["revision"], current["active"], current["state"]) != (expected_revision, None, before):
            raise ValueError("session changed during receipt authentication")
        # Action identities/results and lifecycle are preserved byte-for-byte.
        db.execute("UPDATE proof_sessions SET state=? WHERE id=?", (canonical(state).decode(), session_id))
    manifest.update(status="complete", semantic_sha256=hashlib.sha256(canonical(semantics)).hexdigest(),
                    before_bytes=len(before.encode()), after_bytes=len(canonical(state)),
                    after_state_sha256=hashlib.sha256(canonical(state)).hexdigest())
    atomic_bytes(manifest_path, canonical(manifest))
    return manifest


def migrate_math_payloads(service, session_id, expected_revision, directory, *, checkpoint=None):
    """Compact existing mathematical material without changing its restored graph."""
    from .math_graph_session import PrivateGraphSessionStore
    from .private_payloads import expand
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "migration.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest["database"], manifest["session_id"], manifest["expected_revision"]) != (
                str(service.path.resolve()), session_id, expected_revision):
            raise ValueError("mathematics migration resume binding mismatch")
        with Path(manifest["backup"]["path"]).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["backup"]["sha256"]:
                raise ValueError("migration backup digest mismatch")
    else:
        manifest = {"format_version": 1, "kind": "math", "database": str(service.path.resolve()),
            "session_id": session_id, "expected_revision": expected_revision, "status": "prepared",
            "backup": verified_backup(service.path, directory / "before.sqlite3")}
        atomic_bytes(manifest_path, canonical(manifest))
    with service._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        binding, row, life = service._load(db, session_id)
        if row["revision"] != expected_revision or life["active"] is not None:
            raise ValueError("migration requires an idle expected mathematics revision")
        original = row["state_json"]
        if manifest["status"] == "complete":
            if hashlib.sha256(original.encode()).hexdigest() != manifest["after_state_sha256"]:
                raise ValueError("completed mathematics migration state changed")
            return manifest
        graph = service._restore(binding, json.loads(original))
        before = canonical(PrivateGraphSessionStore._state(graph))
        compacted = service._state(graph, db=db)
        restored = expand(db, service._scope(), compacted)
        if canonical(restored) != before:
            raise ValueError("mathematics migration semantic comparison failed")
        if checkpoint:
            checkpoint(session_id)
        db.execute("UPDATE sessions SET state_json=? WHERE session_id=?", (canonical(compacted).decode(), session_id))
    manifest.update(status="complete", semantic_sha256=hashlib.sha256(before).hexdigest(),
        before_bytes=len(original.encode()), after_bytes=len(canonical(compacted)),
        after_state_sha256=hashlib.sha256(canonical(compacted)).hexdigest())
    atomic_bytes(manifest_path, canonical(manifest))
    return manifest


def reclaim_sqlite(database, backup_path):
    """Caller pauses the owner; VACUUM preserves all history and the exact backup."""
    database = Path(database)
    if shutil.disk_usage(database.parent).free < database.stat().st_size * 3:
        raise ValueError("SQLite reclamation requires temporary free space of three database sizes")
    # Corpus databases use writer.lock rather than a private session owner lock.
    from contextlib import ExitStack
    import fcntl
    with ExitStack() as stack:
        writer = database.parent / "writer.lock"
        if database.name == "graph.sqlite3" and writer.exists():
            lock = stack.enter_context(writer.open("r"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("corpus owner must be paused before SQLite reclamation") from None
        backup = verified_backup(database, backup_path)
        with sqlite3.connect(database) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite reclamation integrity check failed; restore verified backup")
    return {"backup": backup, "database_bytes": database.stat().st_size, "history_preserved": True}
