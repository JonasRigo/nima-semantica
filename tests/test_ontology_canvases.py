"""Native Langflow graph executions, not just imported JSON snapshots."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from build_ontology_tools import build
from build_tool_guide import load_component
from flow_io import validate_edges


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from nima_semantica.corpus_registry import CorpusRegistry
    from nima_semantica.registry_contracts import CorpusDescriptor
    from nima_semantica.storage import GraphStore
    path = tmp_path / "ontology-store"
    store = GraphStore(path)
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
    store.close()
    monkeypatch.setenv("NIMA_STORE_ROOT", str(path))
    return path


def execute(tool, request, *, writes=False, project="research"):
    from lfx.graph import Graph
    flow = json.loads((ROOT / "examples/langflow_replacement" / (tool + ".json")).read_text())
    name = "OntologySave" if tool == "save_ontology" else "OntologyLoad"
    node = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == name)
    template = node["data"]["node"]["template"]
    template["project_id"]["value"] = project
    if tool == "save_ontology":
        template["allow_writes"]["value"] = writes
    graph = Graph.from_payload(flow["data"])
    async def run():
        results = await asyncio.wait_for(graph.arun(inputs=[{"input_value": json.dumps(request)}],
            outputs=["ChatOutput-" + tool]), 30)
        assert results and results[0].outputs
        vertex = graph.get_vertex(name + "-nima")
        assert vertex.built
        return (await vertex.custom_component.result_data()).data
    return asyncio.run(run())


@pytest.mark.parametrize("tool", ["load_ontology", "save_ontology"])
def test_snapshots_edges_and_operator_controls(tool):
    flow = json.loads((ROOT / "examples/langflow_replacement" / (tool + ".json")).read_text())
    assert flow == build(tool)
    validate_edges(flow)
    assert flow["nima_tool_manifest"]["visual_approval"] == "approved"
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name.startswith("Chat"):
            assert not template["should_store_message"]["value"]
            continue
        source = (ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")).read_text()
        assert template["code"]["value"] == source
        assert node["data"]["node"]["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        for field in ("corpus_id", "project_id", "actor", "allow_writes"):
            if field in template:
                assert not template[field].get("tool_mode", False)
                assert not template[field].get("input_types")
        if "allow_writes" in template:
            assert template["allow_writes"]["value"] is False


def test_full_graph_validate_save_exact_load_and_retry(configured):
    validated = execute("save_ontology", {})
    assert validated["status"] == "complete" and validated["data"]["valid"]
    payload = {"mode": "save", "operation_id": "canvas-save"}
    assert execute("save_ontology", payload)["status"] == "failed"
    saved = execute("save_ontology", payload, writes=True)
    assert saved["status"] == "complete", saved
    assert execute("save_ontology", payload, writes=True) == saved
    loaded = execute("load_ontology", {"mode": "load", "digest": saved["data"]["digest"]})
    assert loaded["data"]["profile"] == validated["data"]["profile"]
    assert loaded["data"]["digest"] == saved["data"]["digest"]
    assert execute("load_ontology", {})["data"]["total"] == 5
    assert execute("load_ontology", {"mode": "load", "digest": saved["data"]["digest"]}, project="foreign")["status"] == "unavailable"


def test_without_store_packaged_load_and_validation_still_work(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT", raising=False)
    assert execute("load_ontology", {})["data"]["total"] == 4
    assert execute("save_ontology", {})["data"]["valid"]
    assert execute("save_ontology", {"mode": "save", "operation_id": "save"}, writes=True)["status"] == "unavailable"


@pytest.mark.parametrize("tool", ["load_ontology", "save_ontology"])
@pytest.mark.parametrize("field", ["corpus_id", "project_id", "allow_writes", "actor", "store_path", "approved"])
def test_canvas_rejects_authority_in_public_json(configured, tool, field):
    with pytest.raises(Exception):
        execute(tool, {field: "forged"}, writes=True)


def test_invalid_profile_reaches_receipted_rejection_without_publication(configured):
    from nima_semantica.storage import GraphStore
    result = execute("save_ontology", {"mode": "save", "operation_id": "bad", "profile": {"name": "invalid"}}, writes=True)
    assert result["status"] == "failed" and result["receipt_ids"]
    store = GraphStore(configured)
    try:
        assert not store.records("ArtifactEnvelope")
        assert len(store.records("ExecutionReceipt")) == 1
    finally:
        store.close()


def test_forged_preview_cannot_bypass_revalidation(configured):
    from lfx.schema import Data
    component = load_component("OntologySave")().set(allow_writes=True, payload=Data(data={
        "request": {"mode": "save", "operation_id": "forged", "profile": {}},
        "validation": {"valid": True, "status": "complete"}}))
    result = asyncio.run(component.result_data()).data
    assert result["status"] == "failed" and result["receipt_ids"]


def test_shared_preview_outputs_publish_once(configured):
    from lfx.schema import Data
    from nima_semantica.storage import GraphStore
    fields = load_component("SaveOntologyFields")().set(mode="save", operation_id="once")
    req = asyncio.run(fields.result_data()).data
    component = load_component("OntologySave")().set(allow_writes=True, payload=Data(data={"request": req}))
    async def run():
        return await asyncio.gather(component.result_data(), component.preview_message(), component.table_data())
    result, preview, _ = asyncio.run(run())
    assert result.data["status"] == "complete" and "inspection_ontology" in preview.text
    store = GraphStore(configured)
    try:
        assert len(store.records("ArtifactEnvelope")) == 1
        assert len([r for _, r in store.records("ExecutionReceipt") if r.content["stage"] == "save_ontology"]) == 1
    finally:
        store.close()
