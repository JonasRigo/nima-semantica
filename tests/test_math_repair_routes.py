"""Standalone regressions for the maintained graph implementation."""
import hashlib
import json
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError
from nima_semantica.math_interface_helpers import normalize_support_nodes
from nima_semantica.math_mcp_contracts import Submit, invoke
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService

ROOT = Path(__file__).resolve().parents[1]


def test_unwrap_only_reference_fields_without_changing_caller():
    original = {"answer": {"vector": [1]}, "support_nodes": {"vector": [{"request_id": "a", "output_path": "$"}]}}
    normalized, repairs = normalize_support_nodes(original)
    assert normalized["answer"] == {"vector": [1]}
    assert normalized["support_nodes"]["vector"] == {"request_id": "a", "output_path": "$"}
    assert isinstance(original["support_nodes"]["vector"], list)
    assert repairs == [{"output_path": "vector", "basis": "singleton_support_reference"}]


@pytest.mark.parametrize("value", [[], ["n1", "n2"], [["n1"]], [None]])
def test_ambiguous_or_invalid_lists_still_fail(value):
    with pytest.raises(ValidationError):
        Submit.model_validate(dict(session_id="s", request_id="a", expected_revision=0,
            answer=2, support_nodes={"answer": value}))
    with pytest.raises(ValueError):
        normalize_support_nodes({"support_nodes": {"answer": value}})


def test_named_singleton_reference_preserves_replay_and_provisional_status(tmp_path):
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"), project_id="p", run_id="r"))
    sid = service.open("Propose a scalar", ["answer"])["session_id"]
    service.mutate(sid, "claim", 0, "record_step", dict(kind="assumption", statement="Unproved value", value=2, depends_on=[]))
    args = dict(session_id=sid, request_id="submit", expected_revision=1, answer={"answer": 2},
        support_nodes={"answer": [{"request_id": "claim", "output_path": "$"}]})
    result = invoke(service, "submit", args)
    assert not result["result"]["admitted"]
    assert result["result"]["repair_task"]["state"] == "recorded"
    assert result["result"]["resolved_references"]
    assert result == invoke(service, "submit", args)
    saved = service.read(sid, "inspect_action", {"request_id": "submit"})["result"]
    assert json.loads(saved["attempt"]["arguments_json"])["support_nodes"] == args["support_nodes"]
    forged = {**args, "request_id": "forged", "support_nodes": {"answer": ["foreign"]}}
    assert invoke(service, "submit", forged)["status"] == "rejected"
