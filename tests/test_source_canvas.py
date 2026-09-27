"""Actual Langflow pipeline execution with isolated stores and offline providers."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import importlib.util

import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from build_source_tools import build
from build_tool_guide import load_component
from flow_io import validate_edges

CANVAS = ROOT / "examples/langflow_replacement/prepare_and_index_sources.json"
STAGES = ("PrepareSources", "EmbedSourceRegions", "BuildSourceProjection")


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from nima_semantica.storage import GraphStore
    from nima_semantica.corpus_registry import CorpusRegistry
    from nima_semantica.registry_contracts import CorpusDescriptor
    path = tmp_path / "source-store"
    store = GraphStore(path)
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
    store.close()
    monkeypatch.setenv("NIMA_STORE_ROOT", str(path))
    return path


def add_offline_embedding_fixture(flow):
    path = ROOT / "tests/fixtures/source_embeddings_component.py"
    code = path.read_text()
    spec = importlib.util.spec_from_file_location("offline_embedding_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    node = module.OfflineEmbeddingFixture().to_frontend_node()
    node.update(id="OfflineEmbeddingFixture-test", type="genericNode", position={"x": 0, "y": 800})
    node["data"].update(id=node["id"], type="OfflineEmbeddingFixture")
    node["data"]["node"]["template"]["code"]["value"] = code
    flow["data"]["nodes"].append(node)
    sh = {"dataType": "OfflineEmbeddingFixture", "id": node["id"], "name": "embeddings", "output_types": ["Embeddings"]}
    th = {"fieldName": "embeddings", "id": "EmbedSourceRegions-nima", "inputTypes": ["Embeddings"], "type": "other"}
    flow["data"]["edges"].append({"id": "offline-embedding", "source": node["id"], "target": "EmbedSourceRegions-nima",
        "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"),
        "data": {"sourceHandle": sh, "targetHandle": th}})


def execute(payload, *, writes=False, vector=False, pdf=False):
    from lfx.graph import Graph
    flow = json.loads(CANVAS.read_text())
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name in STAGES:
            template["allow_corpus_writes"]["value"] = writes
        if name == "PrepareSources":
            template["allow_pdf"]["value"] = pdf
        if name == "EmbedSourceRegions" and vector:
            template["allow_embeddings"]["value"] = True
            template["manifest_json"]["value"] = json.dumps({"provider": "fixture", "model": "deterministic-fake",
                "revision": "1", "dimension": 8, "parameters": {}})
    if vector:
        add_offline_embedding_fixture(flow)
    graph = Graph.from_payload(flow["data"])
    async def run():
        outputs = await asyncio.wait_for(graph.arun(inputs=[{"input_value": json.dumps(payload)}],
            outputs=["ChatOutput-sources"]), 30)
        assert outputs and outputs[0].outputs
        vertex = graph.get_vertex("BuildSourceProjection-nima")
        assert vertex.built
        return (await vertex.custom_component.result_data()).data
    return asyncio.run(run())


def test_canvas_snapshots_edges_and_safe_defaults():
    flow = json.loads(CANVAS.read_text())
    assert flow == build()
    validate_edges(flow)
    assert len(flow["data"]["nodes"]) == 6
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name in (*STAGES, "SourceFields"):
            source = (ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")).read_text()
            assert template["code"]["value"] == source
            assert node["data"]["node"]["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        for field in ("corpus_id", "project_id", "actor", "allow_corpus_writes", "allow_pdf", "allow_embeddings", "pdf_url", "pdf_token_file", "manifest_json"):
            if field in template:
                assert not template[field].get("tool_mode", False)
                assert not template[field].get("input_types")
        for flag in ("allow_corpus_writes", "allow_pdf", "allow_embeddings"):
            if flag in template:
                assert template[flag]["value"] is False


def test_default_preview_works_without_a_store(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT", raising=False)
    result = execute({})
    assert result["status"] == "complete" and result["data"]["mode"] == "preview"
    assert not result["data"]["index_ready"] and not result["receipt_ids"]


@pytest.mark.parametrize("vector", [False, True])
def test_full_canvas_preparation_index_and_replay(configured, vector):
    from nima_semantica.storage import GraphStore
    request = {"mode": "prepare_index", "operation_id": "canvas", "index_mode": "vector" if vector else "lexical"}
    result = execute(request, writes=True, vector=vector)
    assert result["status"] == "complete", result
    assert result["data"]["index_ready"]
    assert bool(result["data"]["embedding_manifest"]) == vector
    assert execute(request, writes=True, vector=vector) == result
    store = GraphStore(configured)
    try:
        assert len(store.records("SourceDescriptor")) == 1
        assert len(store.records("GraphProjection")) == 1
        assert bool(store.records("EmbeddingBatch")) == vector
        assert store.graph_revision("papers", "research").project_revision == 0
    finally:
        store.close()


def test_default_permission_denial(configured):
    result = execute({"mode": "prepare_index", "operation_id": "denied"})
    assert result["status"] == "failed" and not result["receipt_ids"]


def test_missing_embedding_model_reports_partial_not_ready(configured):
    result = execute({"mode": "prepare_index", "operation_id": "vector", "index_mode": "vector"}, writes=True)
    assert result["status"] == "partial" and not result["data"]["index_ready"]
    assert result["data"]["failed_stage"] == "source_embeddings"


@pytest.mark.parametrize("field", ["corpus_id", "project_id", "actor", "allow_corpus_writes", "allow_embeddings", "pdf_url", "store_path"])
def test_public_authority_rejected(configured, field):
    with pytest.raises(Exception):
        execute({field: "forged"}, writes=True)


def test_pdf_connection_is_operator_owned_and_invoked_once(configured, monkeypatch):
    import base64
    from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
    calls = []
    monkeypatch.setattr(PdfNormalizerClient, "__init__", lambda *args: None)
    def normalize(self, data):
        calls.append(data)
        return {"text": "PDF fixture $x=x$.", "diagnostics": [{"provenance": [{"page": 1}]}]}
    monkeypatch.setattr(PdfNormalizerClient, "normalize", normalize)
    result = execute({"mode": "prepare_index", "operation_id": "pdf", "sources": [
        {"name": "paper.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}]}, writes=True, pdf=True)
    assert result["status"] == "complete", result
    assert len(calls) == 1


def test_concurrent_preparation_outputs_share_one_execution(configured):
    from lfx.schema import Data
    from nima_semantica.storage import GraphStore
    field = load_component("SourceFields")().set(mode="prepare_index", operation_id="once")
    request = asyncio.run(field.result_data())
    component = load_component("PrepareSources")().set(payload=request, allow_corpus_writes=True)
    async def run():
        return await asyncio.gather(component.result_data(), component.preview_message(), component.table_data())
    result, _, _ = asyncio.run(run())
    assert result.data["status"] == "complete"
    store = GraphStore(configured)
    try:
        assert len(store.records("SourceDescriptor")) == 1
        assert len([r for _, r in store.records("ExecutionReceipt") if r.content["stage"] == "source_preparation"]) == 1
    finally:
        store.close()


def test_base64_canvas_transport_preserves_latex_and_newlines(configured):
    import base64
    from nima_semantica.storage import GraphStore
    source = "\\newcommand{\\norm}[1]{|#1|}\nThe statement $x \\neq 0$ is not omitted.\n"
    result = execute({"mode": "prepare_index", "operation_id": "latex", "sources": [
        {"name": "source.tex", "data_base64": base64.b64encode(source.encode()).decode()}]}, writes=True)
    assert result["status"] == "complete", result
    store = GraphStore(configured)
    try:
        assert store.read_artifact(result["data"]["sources"][0]["normalized_artifact_id"]) == source.encode()
    finally:
        store.close()


def test_native_data_handle_preserves_text_without_string_unescaping():
    from lfx.schema import Data
    source = "\\newcommand{\\norm}[1]{|#1|}\n$x \\neq 0$"
    component = load_component("SourceFields")().set(mcp_request=Data(data={"sources": [{"name": "x.tex", "text": source}]}))
    result = asyncio.run(component.result_data())
    assert result.data["sources"][0]["text"] == source
