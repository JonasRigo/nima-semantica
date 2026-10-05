"""Standalone regressions for the maintained graph implementation."""
import json
import asyncio
import copy
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from nima_semantica.math_graph_session import GraphSessionBinding, PrivateGraphSessionStore


def binding(project="p"):
    return GraphSessionBinding(project_id=project, run_id="r", session_id="s",
        task="Compute beta from the stated definitions", required_paths=("beta",))


def test_private_session_reopens_and_never_admits_model_only_claim(tmp_path):
    path = tmp_path / "private.sqlite3"
    store = PrivateGraphSessionStore(path)
    first = store.act(binding(), "record_step", {"kind": "claim",
        "statement": "I conjecture beta equals two", "value": 2,
        "depends_on": ["task"]})
    assert first["status"] == "completed" and first["revision"] == 1
    node_id = first["result"]["node"]["id"]

    reopened = PrivateGraphSessionStore(path)
    view = reopened.act(binding(), "inspect_node", {"node_id": node_id})
    assert view["result"]["node"]["value"] == 2
    assert view["revision"] == 1
    submitted = reopened.act(binding(), "submit", {"answer": {"beta": 2},
        "support_nodes": {"beta": node_id}})
    assert submitted["status"] == "completed"
    assert submitted["result"]["admitted"] is False
    assert submitted["result"]["proposal"]["unresolved_root_ids"] == [node_id]
    assert submitted["result"]["mathematically_verified"] is False
    assert len(reopened.audit(binding())) == 3


def test_scope_binding_and_rejected_action_receipt(tmp_path):
    store = PrivateGraphSessionStore(tmp_path / "private.sqlite3")
    store.act(binding(), "frontier")
    with pytest.raises(ValueError, match="scope mismatch"):
        store.act(binding("other-project"), "frontier")
    with pytest.raises(ValueError, match="scope mismatch"):
        store.audit(binding("other-project"))
    bad = store.act(binding(), "record_step", {"kind": "claim", "statement": "x",
        "value": 3, "depends_on": ["foreign-node"]})
    assert bad["status"] == "rejected" and bad["revision"] == 0
    assert store.act(binding(), "frontier")["result"]["node_count"] == 1
    assert json.loads(store.audit(binding())[1]["result_json"])["error"] == "ValueError"


def test_stale_revision_fails_without_changing_graph(tmp_path):
    store = PrivateGraphSessionStore(tmp_path / "private.sqlite3")
    store.act(binding(), "record_step", {"kind": "claim", "statement": "x",
        "value": 3, "depends_on": ["task"]})
    with pytest.raises(ValueError, match="stale session revision"):
        store.act(binding(), "frontier", expected_revision=0)
    assert store.act(binding(), "frontier")["revision"] == 1


def test_retrieval_action_records_exact_source_without_certifying_it(tmp_path, monkeypatch, lexical_installation):
    import nima_semantica.math_graph_session as module

    observed = []
    def retrieve(store, context, action):
        observed.append((store, context.project_id, context.corpus_id, action.query))
        return {"passages": [{"region_id": "region-1", "text": "A general method, not a result.",
            "evidence": {"source_revision": "revision-1"}}],
            "projection_revision": "projection-1", "truncated": False}
    monkeypatch.setattr(module, "retrieve_math_context", retrieve)
    session = PrivateGraphSessionStore(tmp_path / "retrieval.sqlite3")
    bound = GraphSessionBinding(project_id="p", run_id="r", session_id="s", task="Compute beta",
        required_paths=("beta",), allow_retrieval=True)
    result = session.act(bound, "retrieve_context", {"query": "method", "purpose": "Check a step", "mode": "lexical"},
        research_store=object())
    assert result["status"] == "completed"
    assert observed[0][1:] == ("p", "papers", "method")
    source = result["result"]["source_nodes"][0]
    assert source["text"] == "A general method, not a result."
    action_receipt = session.act(bound, "inspect_action", {"action_id": result["action_id"]})
    assert action_receipt["result"]["action"]["name"] == "retrieve_context"
    assert "A general method" in action_receipt["result"]["action"]["result_json"]
    assert session.act(bound, "inspect_node", {"node_id": source["id"]})["result"]["node"]["status"] == "observed_text"
    assert session.act(bound, "submit", {"answer": {"beta": "2"},
        "support_nodes": {"beta": source["id"]}})["status"] == "rejected"


@pytest.mark.skipif(not os.environ.get("NIMA_SYMBOLIC_WORKER_URL"),
    reason="requires the configured isolated symbolic worker")
def test_compiled_calculation_and_exact_receipt_through_session(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker

    bound = GraphSessionBinding(project_id="p", run_id="r", session_id="s", task="Compute two",
        required_paths=("beta",), allow_execution=True)
    session = PrivateGraphSessionStore(tmp_path / "calculation.sqlite3")
    steps = [
        {"id": "one", "op": "integer", "args": [], "value": 1,
            "provenance": [], "meaning": "Arithmetic unit"},
        {"id": "two", "op": "add", "args": ["one", "one"],
            "provenance": [], "meaning": "Arithmetic sum"},
    ]
    result = session.act(bound, "run_calculation_graph", {"steps": steps,
        "outputs": {"beta": "two"}, "purpose": "Compute two atomically"},
        worker=configured_symbolic_worker())
    assert result["status"] == "completed", result
    assert result["result"]["output_nodes"]["beta"]["value"] == "2"
    receipt = session.act(bound, "inspect_receipt", {
        "receipt_id": result["result"]["receipt_id"]})
    assert receipt["result"]["receipt"]["kind"] == "compiled_execution"
    assert receipt["result"]["receipt"]["output"]["outcome"] == "executed"
    reopened = PrivateGraphSessionStore(tmp_path / "calculation.sqlite3")
    submitted = reopened.act(bound, "submit", {"answer": {"beta": "2"},
        "support_nodes": {"beta": result["result"]["output_nodes"]["beta"]["id"]}})
    assert submitted["result"]["admitted"] is True, submitted
    assert submitted["result"]["mathematically_verified"] is False
