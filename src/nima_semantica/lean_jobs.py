"""Durable Lean deliveries with immutable request identities and late-result audit."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time

from .execution_policy import LeanExecutionPolicy
from .models import identity, canonical
from .private_service import database_owner


class LeanJobs:
    def __init__(self, path, verifier, *, executor=None):
        from .lean_transport import execute_packet
        self.path, self.verifier = Path(path), verifier
        self.policy = getattr(verifier, "policy", LeanExecutionPolicy())
        self.executor = executor or execute_packet
        self._stop, self._wake = threading.Event(), threading.Event()
        self._cancel = {}
        self._last_heartbeat = {}
        self._owner = database_owner(path)
        self._owner.__enter__()
        try:
            with self.connect() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS lean_jobs (
                    id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, phase TEXT NOT NULL, result TEXT, failure_layer TEXT,
                    created REAL NOT NULL, started REAL, finished REAL, heartbeat REAL)""")
                db.execute("UPDATE lean_jobs SET state='interrupted',failure_layer='owner_interrupted',finished=? "
                           "WHERE state IN ('running','cancel_requested')", (time.time(),))
            self.thread = threading.Thread(target=self._work, daemon=True)
            self.thread.start()
        except BaseException:
            self._owner.__exit__(None, None, None)
            raise

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def close(self):
        self._stop.set(); self._wake.set()
        for event in list(self._cancel.values()):
            event.set()
        self.thread.join(timeout=self.policy.transport_allowance_seconds)
        if self.thread.is_alive():
            raise RuntimeError("worker is still active; owner must remain held until shutdown")
        self._owner.__exit__(None, None, None)

    def submit(self, operation_id, payload):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 256:
            raise ValueError("bounded operation identity required")
        fingerprint = identity(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT request_hash FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
            if old and old[0] != fingerprint:
                raise ValueError("Lean operation identity is bound to a different request/environment")
            if not old:
                db.execute("INSERT INTO lean_jobs VALUES (?,?,?,'queued','queue',NULL,NULL,?,NULL,NULL,NULL)",
                           (operation_id, fingerprint, canonical(payload).decode(), time.time()))
        self._wake.set()
        return self.status(operation_id)

    def status(self, operation_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE lean_jobs SET state='failed',failure_layer='queue_timeout',finished=? "
                "WHERE id=? AND state='queued' AND created<?", (time.time(), operation_id, time.time() - self.policy.queue_seconds))
            row = db.execute("SELECT * FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise ValueError("unknown Lean operation")
            return {k: row[k] for k in ("id", "request_hash", "state", "phase", "failure_layer", "created", "started", "finished", "heartbeat")} | {
                "operation_id": row["id"], "execution_policy": self.policy.model_dump(mode="json"),
                "elapsed_seconds": (row["finished"] or time.time()) - (row["started"] or row["created"])}

    def result(self, operation_id):
        status = self.status(operation_id)
        with self.connect() as db:
            row = db.execute("SELECT result FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
        packet = json.loads(row[0]) if row[0] else None
        if status["state"] == "completed":
            return {"status": status, "packet": packet}
        return {"status": status, "packet": None, "late_result_available": bool(packet)}

    def cancel(self, operation_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise ValueError("unknown Lean operation")
            if row[0] == "queued":
                db.execute("UPDATE lean_jobs SET state='cancelled',failure_layer='cancelled',finished=? WHERE id=?", (time.time(), operation_id))
            elif row[0] == "running":
                db.execute("UPDATE lean_jobs SET state='cancel_requested' WHERE id=?", (operation_id,))
        event = self._cancel.get(operation_id)
        if event:
            event.set()
        return self.status(operation_id)

    def _work(self):
        while not self._stop.is_set():
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE lean_jobs SET state='failed',failure_layer='queue_timeout',finished=? "
                           "WHERE state='queued' AND created<?", (time.time(), time.time() - self.policy.queue_seconds))
                row = db.execute("SELECT * FROM lean_jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE lean_jobs SET state='running',phase='preparation',started=?,heartbeat=? WHERE id=?",
                               (time.time(), time.time(), row["id"]))
            if row is None:
                self._wake.wait(.2); self._wake.clear()
                continue
            operation_id = row["id"]
            event = self._cancel[operation_id] = threading.Event()
            with self.connect() as db:
                if db.execute("SELECT state FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()[0] == "cancel_requested":
                    event.set()
            last_phase = [None]
            def progress(phase):
                now = time.time()
                if phase == last_phase[0] and now - self._last_heartbeat.get(operation_id, 0) < min(1, self.policy.heartbeat_seconds / 3):
                    return
                last_phase[0] = phase
                self._last_heartbeat[operation_id] = now
                with self.connect() as db:
                    db.execute("UPDATE lean_jobs SET phase=?,heartbeat=? WHERE id=?", (phase, now, operation_id))
            watchdog_stop = threading.Event()
            def watchdog():
                while not watchdog_stop.wait(.1):
                    with self.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        active = db.execute("SELECT state,started,heartbeat FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
                        failure = ("job_timeout" if time.time() - active["started"] > self.policy.job_seconds else
                            "heartbeat_exhaustion" if time.time() - active["heartbeat"] > self.policy.heartbeat_seconds else None)
                        if failure and active["state"] == "running":
                            db.execute("UPDATE lean_jobs SET state='cancel_requested',failure_layer=? WHERE id=?", (failure, operation_id))
                            event.set()
                            return
            guard = threading.Thread(target=watchdog, daemon=True); guard.start()
            try:
                # The native verifier reports phases; controlled test executors may omit hooks.
                import inspect
                options = {"progress": progress, "cancel_event": event}
                supported = inspect.signature(self.executor).parameters
                result = self.executor(self.verifier, json.loads(row["payload"]), **{k: v for k, v in options.items() if k in supported})
                failure = result.get("result", {}).get("failure_layer")
                state = "completed"
            except Exception as exc:
                result, failure, state = {"error": type(exc).__name__}, "worker_failure", "failed"
            finally:
                watchdog_stop.set(); guard.join(1)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT state,failure_layer FROM lean_jobs WHERE id=?", (operation_id,)).fetchone()
                if current["state"] == "cancel_requested" or event.is_set():
                    failure = current["failure_layer"] or "cancelled"
                    state = "cancelled" if failure == "cancelled" else "failed"
                db.execute("UPDATE lean_jobs SET state=?,result=?,failure_layer=?,finished=?,heartbeat=? WHERE id=?",
                           (state, canonical(result).decode(), failure, time.time(), time.time(), operation_id))
            self._cancel.pop(operation_id, None)
            self._last_heartbeat.pop(operation_id, None)
