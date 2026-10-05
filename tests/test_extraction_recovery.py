"""Failure recovery preserves completed scope, immutable attempts and delivery state."""
import json
import pytest

from nima_semantica.extraction_recovery import failed_plan_regions, recovery_plan_request, failure_status


def batch(operation, regions):
    return {"mode": "regional", "operation_id": operation, "source_id": "source",
            "source_region_ids": regions, "ontology_profile": "literature_evidence@1.0.0"}


def test_only_saved_failed_uncovered_regions_are_selected(tmp_path):
    plan = {"data": {"batches": [batch("done", ["r1"]), batch("failed", ["r2", "r3"]), batch("unknown", ["r4"])]}}
    (tmp_path / "done-result.json").write_text(json.dumps({"status": "partial", "artifacts": {"graph_proposal": "saved"}}))
    (tmp_path / "failed-result.json").write_text(json.dumps({"status": "failed", "artifacts": {}}))
    assert failed_plan_regions(plan, tmp_path, ["r1", "r3", "r4"]) == ["r3"]
    assert not (tmp_path / "unknown-result.json").exists()


def test_retry_plan_preserves_question_source_ontology_and_revision():
    original = {"mode": "plan", "source_id": "source", "source_region_ids": ["r1", "r2"],
                "question": "Extract exact conditions.", "plan_attempt_id": "first", "ontology_profile": "literature_evidence@1.0.0"}
    new = recovery_plan_request(original, ["r2"], attempt_id="second")
    assert new["source_region_ids"] == ["r2"] and new["plan_attempt_id"] == "second"
    assert all(new[key] == original[key] for key in ("source_id", "question", "ontology_profile"))
    assert original["source_region_ids"] == ["r1", "r2"]
    with pytest.raises(ValueError, match="fresh"):
        recovery_plan_request(original, ["r2"], attempt_id="first")
    with pytest.raises(ValueError, match="subset"):
        recovery_plan_request(original, ["foreign"], attempt_id="second")


@pytest.mark.parametrize("call_state,uncertain", [("returned", False), ("in_flight", True), (None, False)])
def test_delivery_state_survives_failure_wrapper(call_state, uncertain):
    result = failure_status(RuntimeError("batch failed"), previous={"source": "paper"},
        pending={"status": call_state, "result_status": "failed"}, pending_call="pending-call.json")
    assert result["delivery_uncertain"] is uncertain
    assert result["last_result_status"] == "failed" and result["last_status"]["source"] == "paper"


def test_recovery_rejects_filesystem_escape(tmp_path):
    plan = {"data": {"batches": [batch("../foreign", ["r1"])]}}
    with pytest.raises(ValueError):
        failed_plan_regions(plan, tmp_path, ["r1"])
