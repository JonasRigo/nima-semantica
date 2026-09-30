"""Execute the actual native read-only Langflow graph."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from build_inspect_corpus import build
from flow_io import validate_edges
from test_source_canvas import configured, execute as prepare

CANVAS = ROOT / "examples/langflow_replacement/inspect_corpus.json"


def execute(payload):
    from lfx.graph import Graph
    graph = Graph.from_payload(json.loads(CANVAS.read_text())["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value": json.dumps(payload)}], outputs=["ChatOutput-corpus"]), 30)
        component = graph.get_vertex("InspectCorpusMetadata-nima").custom_component
        result, table, preview = await asyncio.gather(component.result_data(), component.table_data(), component.preview_message())
        assert json.loads(preview.text) == result.data
        assert len(table) == len(result.data.get("data", {}).get("sources", []))
        return result.data
    return asyncio.run(run())


def test_snapshot_and_scope_controls():
    flow = json.loads(CANVAS.read_text())
    assert flow == build()
    validate_edges(flow)
    assert len(flow["data"]["nodes"]) == 4
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name.startswith("InspectCorpus"):
            source = (ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")).read_text()
            assert template["code"]["value"] == source
            assert node["data"]["node"]["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        for field in ("corpus_id", "project_id"):
            if field in template:
                assert not template[field].get("tool_mode") and not template[field].get("input_types")


def test_no_store(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT", raising=False)
    assert execute({})["status"] == "unavailable"


def test_inventory_and_read_only(configured):
    from nima_semantica.storage import GraphStore
    prepare({"mode": "prepare_index", "operation_id": "inspect-test"}, writes=True)
    store = GraphStore(configured)
    before = (store.revision, store.records())
    store.close()
    result = execute({"limit": 1})
    assert result["status"] == "complete" and result["data"]["total_sources"] == 1
    assert result["data"]["readiness"]["lexical_metadata_ready"]
    assert not result["receipt_ids"]
    store = GraphStore(configured)
    assert (store.revision, store.records()) == before
    store.close()


@pytest.mark.parametrize("payload", [{"project_id": "secret"}, {"corpus_id": "secret"}, {"allow_writes": True}, {"limit": 101}])
def test_invalid_public_inputs(configured, payload):
    result = execute(payload)
    assert result["status"] == "failed" and result["data"]["executed"] is False
