"""Actual native Langflow graph execution, including operator-connected embeddings."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from build_research_retrieval import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured, execute as prepare, add_offline_embedding_fixture

CANVAS = ROOT / "examples/langflow_replacement/retrieve_research_context.json"


def execute(payload, *, vector=False):
    from lfx.graph import Graph
    flow = json.loads(CANVAS.read_text())
    if vector:
        node = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "RetrieveResearchContext")
        template = node["data"]["node"]["template"]
        template["allow_embeddings"]["value"] = True
        template["allow_attempt_writes"]["value"] = True
        template["manifest_json"]["value"] = json.dumps({"provider":"fixture", "model":"deterministic-fake", "revision":"1", "parameters":{}, "dimension":8})
        add_offline_embedding_fixture(flow)
        edge = flow["data"]["edges"][-1]
        edge["target"] = "RetrieveResearchContext-nima"
        edge["targetHandle"] = edge["targetHandle"].replace("EmbedSourceRegions-nima", "RetrieveResearchContext-nima")
        edge["data"]["targetHandle"]["id"] = "RetrieveResearchContext-nima"
    graph = Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}], outputs=["ChatOutput-context"]),30)
        component = graph.get_vertex("RetrieveResearchContext-nima").custom_component
        result, table, preview = await asyncio.gather(component.result_data(),component.table_data(),component.preview_message())
        assert json.loads(preview.text) == result.data
        assert len(table) == len(result.data.get("data",{}).get("regions",[]))
        return result.data
    return asyncio.run(run())


def test_canvas_snapshot_and_disabled_operator_controls():
    flow = json.loads(CANVAS.read_text())
    assert flow == build()
    validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name in ("ResearchRetrievalFields", "RetrieveResearchContext"):
            source = (ROOT / "deploy/langflow_components/nima_tools" / (name+".py")).read_text()
            assert template["code"]["value"] == source
            assert node["data"]["node"]["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        for field in ("corpus_id", "project_id", "actor", "allow_embeddings", "allow_attempt_writes", "manifest_json"):
            if field in template:
                assert not template[field].get("tool_mode") and not template[field].get("input_types")
        for flag in ("allow_embeddings", "allow_attempt_writes"):
            if flag in template:
                assert template[flag]["value"] is False


def test_unconfigured(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT", raising=False)
    assert execute({})["status"] == "unavailable"


def test_lexical_exact_source_and_empty(configured):
    from nima_semantica.storage import GraphStore
    prepare({"mode":"prepare_index", "operation_id":"source"}, writes=True)
    store = GraphStore(configured)
    before = (store.revision, store.records()); store.close()
    result = execute({"query":"fixture"})
    assert result["status"] == "complete", result
    assert result["data"]["regions"][0]["text"] == "# Inspection fixture\n\nFor x = 2, $x^2 = 4$.\n"
    assert not result["receipt_ids"]
    assert execute({"query":"no_matching_term"})["data"]["regions"] == []
    assert execute({"query":"fixture", "max_chars":1})["status"] == "partial"
    store = GraphStore(configured)
    assert (store.revision, store.records()) == before
    store.close()


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_native_embedding_connection_and_single_attempt(configured, mode):
    from nima_semantica.storage import GraphStore
    prepare({"mode":"prepare_index", "operation_id":"source", "index_mode":"vector"}, writes=True, vector=True)
    request = {"query":"fixture", "mode":mode, "operation_id":"query"}
    result = execute(request, vector=True)
    assert result["status"] == "complete", result
    assert result["receipt_ids"] and result["data"]["regions"]
    assert execute(request, vector=True) == result
    store = GraphStore(configured)
    assert len([r for _,r in store.records("ExecutionReceipt") if r.content["stage"] == "research_retrieval"]) == 1
    store.close()


def test_model_call_default_denial(configured):
    result = execute({"query":"fixture", "mode":"vector", "operation_id":"denied"})
    assert result["status"] == "failed" and not result["receipt_ids"]


@pytest.mark.parametrize("payload", [{"corpus_id":"other"}, {"project_id":"other"}, {"allow_embeddings":True},
    {"manifest_json":"{}"}, {"query_vector":[1,0]}, {"query":" "}])
def test_public_authority_and_invalid_input_rejected(configured, payload):
    with pytest.raises(Exception):
        execute(payload)


def test_native_data_preserves_latex_query():
    from lfx.schema import Data
    query = r"When is x \neq 0?"
    component = load_component("ResearchRetrievalFields")().set(mcp_request=Data(data={"query":query}))
    assert asyncio.run(component.result_data()).data["query"] == query
