"""Durable worker delivery, failure layers and request replay on disposable SQLite."""
import json
from pathlib import Path
import threading
import time

import pytest

from nima_semantica.execution_policy import LeanExecutionPolicy
from nima_semantica.lean_jobs import LeanJobs
from nima_semantica.lean_transport import RemoteLeanVerifier, UncertainLeanDelivery
from nima_semantica.models import identity


def policy(**overrides):
    return LeanExecutionPolicy(**({"compilation_seconds": .2, "replay_seconds": .2, "job_seconds": 2,
        "queue_seconds": 1, "transport_allowance_seconds": .2, "transport_seconds": 3.2,
        "heartbeat_seconds": 1} | overrides))


def wait(jobs, identifier, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = jobs.status(identifier)
        if result["state"] not in {"queued", "running", "cancel_requested"}: return result
        time.sleep(.02)
    pytest.fail("controlled job did not finish")


def test_policy_supports_authorized_limits_and_separate_transport_budget():
    selected = LeanExecutionPolicy()
    assert selected.compilation_seconds == selected.replay_seconds == 1200
    assert selected.memory_mb == 32768
    extended = LeanExecutionPolicy(output_bytes=256 * 1024 * 1024, artifact_bytes=512 * 1024 * 1024,
        transport_artifact_bytes=1024 * 1024 * 1024)
    from nima_semantica.lean_transport import CollectedArtifacts, response_limit
    assert response_limit(extended) > 1536 * 1024 * 1024
    buffer = CollectedArtifacts(limit=10)
    buffer.artifact(b"0123456789")
    with pytest.raises(ValueError): buffer.artifact(b"extra")
    assert selected.transport_seconds >= selected.queue_seconds + selected.job_seconds + selected.transport_allowance_seconds
    with pytest.raises(ValueError): LeanExecutionPolicy(job_seconds=1200)
    with pytest.raises(ValueError): LeanExecutionPolicy(transport_seconds=60)


def test_durable_identity_reconnect_cancellation_and_late_result(tmp_path):
    started, finish = threading.Event(), threading.Event()
    calls = []
    class Verifier: pass
    verifier = Verifier(); verifier.policy = policy()
    def execute(verifier, payload):
        calls.append(payload); started.set(); assert finish.wait(2)
        return {"result": {"certification_verified": True}, "request": payload}
    jobs = LeanJobs(tmp_path / "jobs.sqlite3", verifier, executor=execute)
    try:
        original = {"sources": {"Submission": "exact source"}, "environment": "pin"}
        jobs.submit("job", original); assert started.wait(1)
        assert jobs.submit("job", original)["state"] == "running"
        with pytest.raises(ValueError): jobs.submit("job", {**original, "environment": "changed"})
        assert jobs.result("job")["packet"] is None
        assert jobs.cancel("job")["state"] == "cancel_requested"
        finish.set()
        assert wait(jobs, "job")["state"] == "cancelled"
        result = jobs.result("job")
        assert result["packet"] is None and result["late_result_available"]
        assert len(calls) == 1
        jobs.submit("new-attempt", original)
        assert wait(jobs, "new-attempt")["state"] == "completed"
        assert len(calls) == 2
    finally: finish.set(); jobs.close()


def test_queue_timeout_and_heartbeat_failure_are_distinct(tmp_path):
    class Verifier: pass
    verifier = Verifier(); verifier.policy = policy(queue_seconds=.1, heartbeat_seconds=.2)
    def execute(verifier, payload):
        time.sleep(.35)
        return {"result": {"certification_verified": True}}
    jobs = LeanJobs(tmp_path / "jobs.sqlite3", verifier, executor=execute)
    try:
        jobs.submit("blocking", {"a": 1})
        deadline = time.monotonic() + 1
        while jobs.status("blocking")["state"] == "queued" and time.monotonic() < deadline: time.sleep(.01)
        jobs.submit("queued", {"b": 1})
        time.sleep(.15)
        assert jobs.status("queued")["failure_layer"] == "queue_timeout"
        assert wait(jobs, "blocking")["failure_layer"] == "heartbeat_exhaustion"
        assert jobs.result("blocking")["packet"] is None
    finally: jobs.close()


def test_disconnect_reconciles_same_worker_identity_without_duplicate_execution(store, tmp_path, monkeypatch):
    from test_verify_lean_tool import Verifier
    from test_lean_transport import native
    from nima_semantica.lean_verification_service import LeanVerificationService
    worker = Verifier(); worker.policy = LeanExecutionPolicy.legacy(worker.timeout, worker.memory_mb, worker.output_bytes, worker.artifact_bytes)
    worker.durable_operations = True
    jobs = LeanJobs(tmp_path / "jobs.sqlite3", worker)
    lost = []
    def call(self, path, payload=None):
        if path == "/manifest": return LeanVerificationService(None, worker).environment_manifest()
        if path == "/operations/submit":
            result = jobs.submit(payload["operation_id"], payload["payload"])
            if not lost:
                from nima_semantica.models import NimaError
                lost.append(True); raise NimaError("lost response after accepted submission")
            return result
        identifier = path.split("?id=", 1)[1]
        if path.startswith("/operations/status"): return jobs.status(identifier)
        return jobs.result(identifier)
    monkeypatch.setattr(RemoteLeanVerifier, "_request", call)
    client = RemoteLeanVerifier("http://worker", "private-token")
    try:
        with pytest.raises(UncertainLeanDelivery) as error: client.verify(store, native(), operation_id="retained")
        assert error.value.operation_id == "retained"
        result = client.verify(store, native(), operation_id="retained")
        assert result.certification_verified and len(worker.calls) == 1
    finally: jobs.close()


def test_uncertain_delivery_is_not_cached_as_definitive_failure(store):
    from test_verify_lean_tool import Verifier
    from nima_semantica.lean_verification_service import LeanVerificationService, LeanVerificationRequest
    class Disconnect(Verifier):
        deliveries = 0
        def verify_operation(self, store, request, operation_id):
            self.deliveries += 1
            if self.deliveries == 1: raise UncertainLeanDelivery(operation_id)
            return self.verify(store, request)
    worker = Disconnect()
    service = LeanVerificationService(store, worker)
    request = LeanVerificationRequest(corpus_id="papers", project_id="p", operation_id="same", sources={"Submission": "theorem target : True := True.intro"}, targets=("target",), imports=("Submission",))
    uncertain = service.execute(request)
    assert uncertain.status == "uncertain_delivery"
    completed = service.execute(request)
    assert completed.status == "verified" and worker.deliveries == 2
    assert service.execute(request) == completed and worker.deliveries == 2
