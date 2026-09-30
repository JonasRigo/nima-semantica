"""Research Run scope, atomicity, history, and non-orchestration acceptance."""
import asyncio

import pytest

from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import ConflictError
from nima_semantica.receipts import ExecutionReceipt
from nima_semantica.research_run_service import ResearchRunService
from nima_semantica.research_run_tool import ResearchRunContext, ResearchRunRequest, research_run


def context(**updates):
    return ResearchRunContext(corpus_id="papers", project_id="p", actor="operator", allow_writes=True, **updates)


def create(**updates):
    payload = {"mode":"create", "run_id":"run", "operation_id":"create",
        "creation":{"objective":"Investigate", "skill_id":"manual", "skill_revision":"1"}}
    payload.update(updates)
    return ResearchRunRequest.model_validate(payload)


def transition(operation="pause", **updates):
    value = {"transition_id":operation, "from_state":"running", "to_state":"paused", "from_run_revision":0, "reason":"Review"}
    value.update(updates)
    return ResearchRunRequest(mode="transition", run_id="run", operation_id=operation, transition=value)


def test_create_transition_inspect_and_replay_are_append_only(store):
    ctx = context()
    graph = store.graph_revision("papers", "p")
    first = research_run(store, create(), ctx)
    assert first.status == "complete"
    assert research_run(store, create(), ctx) == first
    paused = research_run(store, transition(), ctx)
    before = store.revision
    assert paused.data["run"]["status"] == "paused"
    assert research_run(store, transition(), ctx) == paused
    assert research_run(store, create(), ctx) == first
    assert store.revision == before
    inspected = research_run(store, ResearchRunRequest(run_id="run"), ctx)
    assert inspected.data["run"]["run_revision"] == 1
    assert len(inspected.data["attempts"]) == 2
    assert inspected.data["transitions"][0]["actor"] == "operator"
    assert inspected.data["transitions"][0]["approved"] is False
    assert store.revision == before and inspected.receipt_ids == ()
    assert store.graph_revision("papers", "p") == graph


def test_service_creation_replay_after_transition(store):
    ctx = context()
    first = research_run(store, create(), ctx)
    service = ResearchRunService(store)
    initial = service.get_run("run", corpus_id="papers", project_id="p")
    research_run(store, transition(), ctx)
    before = store.revision
    assert service.create_run(initial) == first.data["record_id"]
    assert store.revision == before


def test_permission_denial_has_no_side_effects(store):
    before = store.revision
    ctx = context().model_copy(update={"allow_writes":False})
    result = research_run(store, create(), ctx)
    assert result.diagnostics[0]["code"] == "research_run.write_not_authorized"
    assert store.revision == before and result.receipt_ids == ()


@pytest.mark.parametrize("field", ["corpus_id", "project_id", "actor", "allow_writes", "store_path", "approved", "authorization_scope"])
def test_public_request_cannot_override_authority(field):
    with pytest.raises(ValueError):
        ResearchRunRequest.model_validate({"run_id":"run", field:"forged"})


@pytest.mark.parametrize("payload", [
    {"mode":"create", "run_id":"r"}, {"mode":"transition", "run_id":"r"},
    {"mode":"inspect", "run_id":"r", "operation_id":"write"},
    {"run_id":"r", "limit":0}, {"run_id":"r", "limit":True}, {"run_id":"r", "limit":201},
])
def test_mode_fields_and_history_bounds(payload):
    with pytest.raises(ValueError):
        ResearchRunRequest.model_validate(payload)


def test_cross_project_run_and_receipts_are_not_visible(store):
    research_run(store, create(), context())
    other = context().model_copy(update={"project_id":"other"})
    assert research_run(store, ResearchRunRequest(run_id="run"), other).status == "unavailable"
    research_run(store, create(operation_id="create-other"), other)
    receipt_id = store.records("ExecutionReceipt", corpus_id="papers", project_id="other")[0][1].content["receipt_id"]
    result = research_run(store, transition(receipt_ids=(receipt_id,)), context())
    assert result.status == "failed"
    history = research_run(store, ResearchRunRequest(run_id="run"), context()).data
    assert all(r["project_id"] == "p" for r in history["attempts"])
    assert history["run"]["status"] == "running"


def test_stale_state_terminal_state_and_changed_replay(store):
    ctx = context()
    research_run(store, create(), ctx)
    research_run(store, transition(to_state="completed"), ctx)
    assert research_run(store, transition("stale"), ctx).status == "failed"
    assert research_run(store, transition("terminal", from_state="completed", from_run_revision=1, to_state="running"), ctx).status == "failed"
    with pytest.raises(ConflictError):
        research_run(store, create(creation={"objective":"changed", "skill_id":"manual", "skill_revision":"1"}), ctx)
    assert research_run(store, ResearchRunRequest(run_id="run"), ctx).data["run"]["status"] == "completed"


def test_stale_store_and_foreign_graph_revision_fail_before_creation(store):
    ctx = context()
    result = research_run(store, create(expected_store_revision="sqlite:99999"), ctx)
    assert result.status == "failed" and not store.records("ResearchRun")
    req = create(operation_id="foreign", creation={"objective":"test", "skill_id":"manual", "skill_revision":"1",
        "graph_revision":store.graph_revision("papers", "other").model_dump(mode="json")})
    assert research_run(store, req, ctx).status == "failed"
    assert not store.records("ResearchRun")


def test_invalid_registry_revision_is_receipted(store):
    req = create(creation={"objective":"test", "skill_id":"manual", "skill_revision":"1", "registry_revision":"missing"})
    assert research_run(store, req, context()).status == "failed"
    assert not store.records("ResearchRun")


def test_history_retains_failed_interrupted_and_partial_attempts_and_pages(store):
    ctx = context()
    research_run(store, create(), ctx)
    service = ExecutionReceiptService(store)
    for status in ("failed", "interrupted", "partial"):
        service.record(ExecutionReceipt(receipt_id=status, operation_id=status, stage="fixture", corpus_id="papers",
            project_id="p", run_id="run", status=status))
    first = research_run(store, ResearchRunRequest(run_id="run", limit=2), ctx).data
    second = research_run(store, ResearchRunRequest.model_validate(first["next_request"]), ctx).data
    assert first["more_attempts"] and not second["more_attempts"]
    assert {r["status"] for r in first["attempts"] + second["attempts"]} == {"completed", "failed", "interrupted", "partial"}


def test_successful_write_rolls_back_if_receipt_cannot_be_saved(store, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("no persistence")
    monkeypatch.setattr(ExecutionReceiptService, "record", unavailable)
    with pytest.raises(OSError):
        research_run(store, create(), context())
    assert not store.records("ResearchRun") and not store.records("TaskTransition")


def test_cancelled_transition_rolls_back_and_records_interruption(store, monkeypatch):
    research_run(store, create(), context())
    original = ResearchRunService.append_transition
    def cancel(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(ResearchRunService, "append_transition", cancel)
    with pytest.raises(asyncio.CancelledError):
        research_run(store, transition(), context())
    result = research_run(store, ResearchRunRequest(run_id="run"), context()).data
    assert result["run"]["run_revision"] == 0 and not result["transitions"]
    assert "interrupted" in {r["status"] for r in result["attempts"]}
