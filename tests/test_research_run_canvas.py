"""Full native Research Run canvas acceptance in the installed Langflow runtime."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import build_research_run as builder
from build_tool_guide import load_component

CANVAS = ROOT / "examples/langflow_replacement/research_run.json"


@pytest.fixture
def run_store(tmp_path, monkeypatch):
    from nima_semantica.storage import GraphStore
    path = tmp_path / "run-store"
    value = GraphStore(path)
    value.close()
    monkeypatch.setenv("NIMA_STORE_ROOT", str(path))
    return path


def execute(payload, *, writes=False):
    from lfx.graph import Graph
    flow = json.loads(CANVAS.read_text())
    node = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "ResearchRunTool")
    node["data"]["node"]["template"]["allow_writes"]["value"] = writes
    graph = Graph.from_payload(flow["data"])
    async def run():
        outputs = await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],
            outputs=["ChatOutput-research-run"]), timeout=30)
        assert outputs and outputs[0].outputs
        vertex = graph.get_vertex("ResearchRunTool-nima")
        assert vertex.built
        return (await vertex.custom_component.result_data()).data
    return asyncio.run(run())


def test_canvas_snapshots_and_safe_operator_defaults():
    flow = json.loads(CANVAS.read_text())
    from flow_io import validate_edges
    validate_edges(flow)
    assert flow == builder.build()
    assert flow["nima_tool_manifest"]["visual_approval"] == "approved"
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name in ("ResearchRunFields", "ResearchRunTool"):
            code = (ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")).read_text()
            assert code == template["code"]["value"]
            assert hashlib.sha256(code.encode()).hexdigest() == node["data"]["node"]["metadata"]["source_sha256"]
        if name == "ResearchRunTool":
            assert not template["allow_writes"]["value"]
            for field in ("corpus_id", "project_id", "actor", "allow_writes"):
                assert not template[field].get("tool_mode", False)
                assert not template[field].get("input_types")


def creation():
    return {"mode":"create", "run_id":"inspection-run", "operation_id":"create-1", "creation":{
        "objective":"Inspect the workflow", "skill_id":"manual", "skill_revision":"1"}}


def test_full_canvas_creation_transition_history_and_exact_replay(run_store):
    from nima_semantica.storage import GraphStore
    created = execute(creation(), writes=True)
    assert created["status"] == "complete"
    assert execute(creation(), writes=True) == created
    paused = execute({"mode":"transition", "run_id":"inspection-run", "operation_id":"pause-1",
        "transition":{"transition_id":"pause-1", "from_state":"running", "to_state":"paused", "from_run_revision":0,
                      "reason":"Review with user"}}, writes=True)
    assert paused["data"]["run"]["run_revision"] == 1
    history = execute({"run_id":"inspection-run"})
    assert history["data"]["run"]["status"] == "paused"
    assert len(history["data"]["attempts"]) == 2
    assert execute(creation(), writes=True) == created
    store = GraphStore(run_store)
    try:
        assert len(store.records("ResearchRun")) == 2
        assert len(store.records("TaskTransition")) == 1
        assert store.graph_revision("papers", "research").project_revision == 0
    finally:
        store.close()


def test_canvas_default_write_denial_has_no_records(run_store):
    from nima_semantica.storage import GraphStore
    result = execute(creation())
    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "research_run.write_not_authorized"
    store = GraphStore(run_store)
    try:
        assert store.records() == []
    finally:
        store.close()


def test_unconfigured_live_canvas_is_explicitly_unavailable(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT", raising=False)
    result = execute({})
    assert result["status"] == "unavailable"
    assert result["diagnostics"][0]["code"] == "research_run.store_not_configured"


@pytest.mark.parametrize("field", ["project_id", "corpus_id", "actor", "allow_writes", "store_path", "approved"])
def test_full_canvas_rejects_public_authority_override(run_store, field):
    with pytest.raises(Exception):
        execute({"run_id":"inspection-run", field:"forged"}, writes=True)


def test_concurrent_preview_ports_record_once(run_store):
    from lfx.schema import Data
    from nima_semantica.storage import GraphStore
    component = load_component("ResearchRunTool")().set(payload=Data(data=creation()), allow_writes=True)
    async def run():
        return await asyncio.gather(component.result_data(), component.preview_message(), component.table_data())
    result, preview, table = asyncio.run(run())
    assert result.data["status"] == "complete" and "inspection-run" in preview.text
    store = GraphStore(run_store)
    try:
        assert len(store.records("ResearchRun")) == len(store.records("ExecutionReceipt")) == 1
    finally:
        store.close()
