"""Standalone regressions for the maintained graph implementation."""
import hashlib
import json
import runpy
from pathlib import Path

import pytest

from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
from nima_semantica.math_mcp_contracts import invoke
from nima_semantica.math_repair_feedback import repair_feedback
from nima_semantica.math_single_graph_state import SingleCalculationGraph

ROOT = Path(__file__).resolve().parents[1]


class Worker:
    def run(self, source, timeout):
        return dict(outcome="executed", exit_code=0, stdout='{"answer":2}', stderr="")


def setup(tmp_path, worker=None, allowed=True):
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"),
        project_id="p", run_id="r", allow_execution=allowed), worker=worker or Worker())
    sid = service.open("Compute one plus one", ["answer"])["session_id"]
    result = service.mutate(sid, "premise", 0, "record_step", dict(kind="assumption",
        statement="Unproved proposed value", value=2, depends_on=["task"]))
    return service, sid, result["result"]["node"]["id"]


def experiment(service, sid, target, rid="test", revision=1):
    return invoke(service, "run_experiment", dict(session_id=sid, request_id=rid,
        expected_revision=revision, source='print(1+1)', purpose="Investigate proposed value",
        repair_target=target, depends_on=[]))


def test_attempt_is_not_resolution_and_replay_is_immutable(tmp_path):
    service, sid, target = setup(tmp_path)
    assert service.read(sid)["result"]["repair_task"]["state"] == "recorded"
    response = experiment(service, sid, target)
    assert service.read(sid)["result"]["repair_task"]["state"] == "attempted"
    assert experiment(service, sid, target) == response
    assert service.read(sid)["result"]["repair_progress"][0]["attempt_request_ids"] == ["test"]
    graph = service.read(sid, "export")["result"]
    assert all(target not in node["depends_on"] for node in graph["nodes"] if node["id"] != target)
    reopened = MathSessionService(service.config)
    assert reopened.read(sid)["result"] == service.read(sid)["result"]
    closed = service.mutate(sid, "close", response["revision"], "close", dict(outcome="partial",
        repair_blocker="Compiler cannot represent the required independent operation"))
    assert service.read(sid)["result"]["repair_task"]["state"] == "attempted"
    assert "harness-reported" in closed["result"]["repair_blocker"]["authority"]


@pytest.mark.parametrize("failure", ["denied", "unknown", "worker_error", "malformed"])
def test_nonperformed_actions_do_not_count(tmp_path, failure):
    class BrokenWorker:
        def run(self, *args):
            raise RuntimeError("unavailable")
    service, sid, target = setup(tmp_path, BrokenWorker(), allowed=failure != "denied")
    before = service.read(sid, "export")
    if failure == "malformed":
        result = service.mutate(sid, "bad", 1, "run_calculation_graph",
            dict(steps=[], outputs={"answer": "absent"}, repair_target=target))
    else:
        result = experiment(service, sid, "foreign" if failure == "unknown" else target)
    assert service.read(sid)["result"]["repair_task"]["state"] == "recorded"
    if failure != "worker_error":
        assert service.read(sid, "export") == before


def test_resolved_uses_existing_substantiation_not_execution_label():
    graph = SingleCalculationGraph("Calculate", (), ("answer",))
    premise = graph.add_step(kind="assumption", statement="Proposed value", value=2, depends_on=[])
    evidence = graph.record_execution("print(1+1)", dict(outcome="executed", exit_code=0,
        stdout='{"answer":2}', stderr=""), "independent", evidence_eligible=True)[0]
    graph.substantiate(premise["id"], evidence["id"], "Independent computation")
    feedback = repair_feedback(graph)
    assert feedback["repair_task"] is None
    assert feedback["repair_progress"][0]["state"] == "resolved"
    assert feedback["repair_progress"][0]["evidence_status"] == "agent_supported_not_verified"


def test_repeated_assumption_and_partial_closure_do_not_repair(tmp_path):
    service, sid, target = setup(tmp_path)
    repeated = service.mutate(sid, "repeat", 1, "record_step", dict(kind="assumption",
        statement="Same proposed value", value=2, depends_on=[target]))
    assert all(item["state"] == "recorded" for item in service.read(sid)["result"]["repair_progress"])
    closed = service.mutate(sid, "stop", repeated["revision"], "close", {"outcome": "partial"})
    assert service.read(sid)["result"]["repair_task"]["state"] == "recorded"
    assert "repair_blocker" not in closed["result"]


def test_executed_error_is_an_attempt_not_resolution(tmp_path):
    class FailedWorker:
        def run(self, *args):
            return dict(outcome="executed", exit_code=1, stdout="", stderr="ZeroDivisionError")
    service, sid, target = setup(tmp_path, FailedWorker())
    result = experiment(service, sid, target)
    assert result["status"] == "failed"
    assert service.read(sid)["result"]["repair_task"]["state"] == "attempted"


def test_completed_empty_search_is_attempt_not_support(tmp_path, monkeypatch):
    service, sid, target = setup(tmp_path)
    original = MathSessionService._dispatch
    def dispatch(graph, binding, name, args, **kwargs):
        if name == "retrieve_context":
            assert "repair_target" not in args
            return {"source_nodes": [], "retrieval_receipt": {"passages": [], "projection_revision": 1}}, True
        return original(graph, binding, name, args, **kwargs)
    monkeypatch.setattr(MathSessionService, "_dispatch", staticmethod(dispatch))
    result = invoke(service, "retrieve_context", dict(session_id=sid, request_id="search",
        expected_revision=1, query="Independent method", purpose="Investigate premise", repair_target=target))
    assert service.read(sid)["result"]["repair_task"]["state"] == "attempted"
    assert "retrieval_receipt" not in result["result"]  # compact adapter preserves feedback


def test_actual_cancelled_targeted_execution_does_not_count(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    started, release = threading.Event(), threading.Event()
    class WaitingWorker:
        def run(self, *args):
            started.set()
            assert release.wait(5)
            return Worker().run(*args)
    service, sid, target = setup(tmp_path, WaitingWorker())
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(experiment, service, sid, target)
        assert started.wait(5)
        try:
            service.mutate(sid, "cancel", 1, "cancel", {})
        finally:
            release.set()
        assert pending.result()["status"] == "cancelled"
    assert service.read(sid)["result"]["repair_task"]["state"] == "recorded"


@pytest.mark.integration
def test_compiled_repair_attempt_then_existing_substantiation(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service, sid, target = setup(tmp_path, configured_symbolic_worker())
    result = invoke(service, "run_calculation_graph", dict(session_id=sid, request_id="calculate",
        expected_revision=1, repair_target={"request_id": "premise", "output_path": "$"},
        purpose="Independently add units", steps=[
            dict(id="one", op="integer", value=1, meaning="Unit"),
            dict(id="two", op="add", args=["one", "one"], meaning="Sum")], outputs={"answer": "two"}))
    assert result["status"] == "completed", result
    assert service.read(sid)["result"]["repair_task"] is None
    assert service.read(sid)["result"]["repair_progress"][0]["state"] == "attempted"
    assert service.read(sid)["result"]["repair_progress"][0]["scope"] == "audit_only"
    resolved = invoke(service, "substantiate", dict(session_id=sid, request_id="support",
        expected_revision=result["revision"], hypothesis_id=target,
        evidence_id={"request_id": "calculate", "output_path": "answer"}, rationale="Independent sum"))
    assert service.read(sid)["result"]["repair_task"] is None
    assert service.read(sid)["result"]["repair_progress"][0]["state"] == "resolved"


@pytest.mark.parametrize("status", ["cancelled", "interrupted", "rejected"])
def test_cancelled_or_rejected_receipts_cannot_count(status):
    graph = SingleCalculationGraph("Calculate", (), ("answer",))
    premise = graph.add_step(kind="assumption", statement="Proposed", value=2, depends_on=[])
    result = repair_feedback(graph, [("cancelled", {"status": status, "result": {
        "repair_attempt": {"target_id": premise["id"], "performed": True}}})])
    assert result["repair_task"]["state"] == "recorded"
