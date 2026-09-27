"""Native exact-source and index correspondence acceptance, without paid providers."""
import base64
import hashlib
import json
import builtins
from asyncio import CancelledError

import pytest

from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import ConflictError
from nima_semantica.providers import ModelManifest
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.source_tools import (
    PrepareSourcesRequest, SourceInput, SourceToolContext, prepare_sources, embed_sources, project_sources,
)
from nima_semantica.storage import GraphStore


MANIFEST = ModelManifest(provider="fixture", model="exact-text-spy", revision="1", parameters={}, dimension=2)


@pytest.fixture
def store(tmp_path):
    result = GraphStore(tmp_path / "store")
    CorpusRegistry(result).register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
    yield result
    result.close()


def context(**kwargs):
    return SourceToolContext(**({"corpus_id": "papers", "project_id": "research",
        "allow_corpus_writes": True, "allow_embeddings": True} | kwargs))


def request(**kwargs):
    return PrepareSourcesRequest(**({"mode": "prepare_index", "operation_id": "prepare-1",
        "sources": [{"name": "source.md", "text": "# Claim\n\nFor α = 2, $α^2 = 4$.\n\nEvidence matters.\n"}]} | kwargs))


class Provider:
    def __init__(self):
        self.texts = []
    def embed(self, profile, texts):
        self.texts.extend(texts)
        return [[float(len(t)), 1.] for t in texts], MANIFEST


def pipeline(store, req=None, ctx=None, provider=None):
    ctx = ctx or context()
    prepared = prepare_sources(store, req or request(), ctx)
    assert prepared.status == "complete", prepared
    embedded = embed_sources(store, prepared, ctx, provider=provider, manifest=MANIFEST if provider else None)
    return prepared, embedded, project_sources(store, embedded, ctx)


def test_lexical_pipeline_preserves_exact_unicode_regions_and_graph(store):
    graph = store.graph_revision("papers", "research")
    prepared, embedded, result = pipeline(store)
    assert result.status == "complete" and result.data["index_ready"]
    assert result.data["embedding_manifest"] is None and not store.records("EmbeddingBatch")
    source = prepared.data["sources"][0]
    data = store.read_artifact(source["normalized_artifact_id"]).decode()
    records = [store.get(i).content for i in prepared.data["region_ids"]]
    assert "".join(r["text"] for r in records) == data == request().sources[0].text
    assert all(data[r["start"]:r["end"]] == r["text"] for r in records)
    lexical = json.loads(store.read_artifact(result.data["projection"]["artifact_ids"]["lexical"]))
    assert set(prepared.data["region_ids"]) <= set(lexical["terms"]["claim"] + lexical["terms"]["evidence"])
    assert store.graph_revision("papers", "research") == graph
    assert len([r for _, r in store.records("ExecutionReceipt") if r.content["stage"].startswith("source_")]) == 3


def test_vector_provider_receives_exact_ordered_regions_and_replay_never_calls_twice(store):
    provider = Provider()
    req = request(index_mode="vector")
    prepared, embedded, result = pipeline(store, req, provider=provider)
    assert result.status == "complete", result
    assert provider.texts == [store.get(i).content["text"] for i in prepared.data["region_ids"]]
    batch = store.get(embedded.data["embedding_batch_ids"][0])
    matrix = json.loads(store.read_artifact(batch.content["matrix_artifact"]))
    assert matrix == [[float(len(text)), 1.] for text in provider.texts]
    before = store.revision
    assert pipeline(store, req, provider=provider)[2] == result
    assert len(provider.texts) == len(prepared.data["region_ids"])
    assert store.revision == before


def test_new_operation_reuses_immutable_sources_without_duplicate_regions(store):
    a = pipeline(store)[2]
    b = pipeline(store, request(operation_id="retry"))[2]
    assert b.status == "complete"
    assert a.data["region_ids"] == b.data["region_ids"]
    assert len(store.records("SourceDescriptor")) == 1


def test_preview_is_pure_and_pdf_never_calls_worker(store):
    before = store.revision
    req = request(mode="preview", operation_id=None)
    assert prepare_sources(store, req, context()).data["mode"] == "preview"
    pdf = request(mode="preview", operation_id=None, sources=[{"name": "x.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}])
    assert prepare_sources(store, pdf, context(), pdf_normalizer=lambda _: pytest.fail("worker called")).status == "unavailable"
    assert store.revision == before


@pytest.mark.parametrize("field", ["corpus_id", "project_id", "actor", "allow_corpus_writes", "allow_embeddings", "pdf_url", "store_path", "manifest"])
def test_public_json_cannot_override_authority(field):
    with pytest.raises(ValueError):
        PrepareSourcesRequest.model_validate({**request().model_dump(), field: "forged"})


@pytest.mark.parametrize("payload", [{"name": "../paper.txt", "text": "x"}, {"name": "https://site/paper.txt", "text": "x"},
    {"name": "x.exe", "text": "x"}, {"name": "x.txt"}, {"name": "x.txt", "text": "a", "data_base64": "YQ=="}])
def test_paths_unsupported_formats_and_ambiguous_sources_rejected(payload):
    with pytest.raises(ValueError):
        SourceInput(**payload)


def test_denied_write_has_no_receipt_or_worker_calls(store):
    before = store.revision
    result = prepare_sources(store, request(), context(allow_corpus_writes=False))
    assert result.status == "failed" and not result.receipt_ids
    assert store.revision == before


def test_missing_semantica_dependency_is_a_configuration_failure(store, monkeypatch):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "semantica.split":
            error = ModuleNotFoundError("No module named 'semantica'")
            error.name = "semantica"
            raise error
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    result = prepare_sources(store, request(), context())
    assert result.status == "failed"
    assert result.diagnostics[0]["code"] == "sources.source_preparation.ConfigurationError"
    assert not store.records("SourceDescriptor")


def test_stale_request_has_receipt_but_no_prepared_sources(store):
    result = prepare_sources(store, request(expected_store_revision="stale"), context())
    assert result.status == "failed" and result.receipt_ids
    assert not store.records("SourceDescriptor")
    with pytest.raises(ConflictError):
        prepare_sources(store, request(), context())


def test_invalid_source_rolls_back_whole_preparation_batch(store):
    req = request(sources=[{"name": "good.txt", "text": "good source"}, {"name": "bad.txt", "data_base64": "not base64"}])
    result = prepare_sources(store, req, context())
    assert result.status == "failed"
    assert not store.records("SourceDescriptor") and not store.records("ArtifactEnvelope")
    assert len(store.records("ExecutionReceipt")) == 1


@pytest.mark.parametrize("fault", ["missing", "wrong_manifest", "zero", "count", "nan", "exception", "cancel"])
def test_embedding_failure_keeps_sources_but_never_publishes_ready_index(store, fault):
    prepared = prepare_sources(store, request(index_mode="vector"), context())
    class BadProvider:
        def embed(self, profile, texts):
            if fault == "exception": raise RuntimeError("secret provider detail")
            if fault == "cancel": raise CancelledError()
            manifest = MANIFEST.model_copy(update={"revision": "other"}) if fault == "wrong_manifest" else MANIFEST
            return ([] if fault == "count" else [[0., 0.] if fault == "zero" else [float("nan"), 1.] if fault == "nan" else [1., 1.] for _ in texts]), manifest
    if fault == "cancel":
        with pytest.raises(CancelledError):
            embed_sources(store, prepared, context(), provider=BadProvider(), manifest=MANIFEST)
    else:
        result = embed_sources(store, prepared, context(), provider=None if fault == "missing" else BadProvider(), manifest=MANIFEST)
        assert result.status == "partial" and not result.data["index_ready"]
        assert project_sources(store, result, context()) == result
        assert "secret provider detail" not in result.model_dump_json()
    assert store.records("SourceRegion")
    assert not store.records("EmbeddingBatch") and not store.records("GraphProjection")
    attempts = [r.content for _, r in store.records("ExecutionReceipt") if r.content["stage"] == "source_embeddings"]
    assert len(attempts) == 1 and attempts[0]["status"] == ("interrupted" if fault == "cancel" else "failed")


def test_upstream_forgery_and_foreign_scope_are_rejected_without_calls(store):
    prepared = prepare_sources(store, request(index_mode="vector"), context())
    provider = Provider()
    for value, ctx in ((prepared, context(project_id="foreign")),
        (prepared.model_copy(update={"data": {**prepared.data, "region_ids": ["0" * 64]}}), context())):
        result = embed_sources(store, value, ctx, provider=provider, manifest=MANIFEST)
        assert result.status == "failed"
    assert not provider.texts


@pytest.mark.parametrize("corrupt", [False, True])
def test_pdf_worker_provenance_and_crops_remain_owned_evidence(store, corrupt):
    calls = []
    def parser(data):
        calls.append(data)
        return "A PDF formula: $x^2$.\n", [{"kind": "parser_manifest", "provenance": [{"page": 1}],
            "artifact_bundle": [{"sha256": "0" * 64 if corrupt else hashlib.sha256(b"crop").hexdigest(),
                "data_base64": base64.b64encode(b"crop").decode()}]}]
    req = request(sources=[{"name": "paper.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}])
    result = prepare_sources(store, req, context(allow_pdf=True), pdf_normalizer=parser)
    assert len(calls) == 1
    if corrupt:
        assert result.status == "failed" and not store.records("SourceRegion")
    else:
        assert result.status == "complete", result
        normalized = store.records("NormalizedDocument")[0][1]
        diagnostic = normalized.content["diagnostics"][0]
        assert diagnostic["provenance"] == [{"page": 1}]
        assert "artifact_bundle" not in diagnostic
        assert store.read_artifact(diagnostic["artifact_ids"][0]) == b"crop"


@pytest.mark.parametrize("index_mode", ["lexical", "vector"])
def test_unresolved_pdf_is_indexed_but_remains_partial(store, index_mode):
    def parser(_):
        return "An equation is [unresolved formula].", [
            {"kind": "formula", "status": "unresolved", "provenance": [{"page": 1}]},
            {"kind": "parser_manifest", "provenance": [{"page": 1}],
                "mathematics": {"unresolved_formula_count": 1}},
        ]
    req = request(index_mode=index_mode, sources=[{"name": "paper.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}])
    prepared = prepare_sources(store, req, context(allow_pdf=True), pdf_normalizer=parser)
    assert prepared.status == "complete"
    assert prepared.data["normalization_complete"] is False
    assert prepared.data["unresolved_region_count"] == 1
    embedded = embed_sources(store, prepared, context(), provider=Provider() if index_mode == "vector" else None,
        manifest=MANIFEST if index_mode == "vector" else None)
    result = project_sources(store, embedded, context())
    assert result.status == "partial"
    assert result.data["index_ready"] is True
    assert result.data["sources"][0]["normalization_complete"] is False
    assert project_sources(store, embedded, context()) == result


@pytest.mark.parametrize("stage", ["source_preparation", "source_embeddings", "source_projection"])
def test_success_receipt_failure_rolls_back_only_current_stage(store, monkeypatch, stage):
    original = ExecutionReceiptService.record
    def record(self, receipt, **kwargs):
        if receipt.stage == stage and receipt.status == "completed":
            raise RuntimeError("injected receipt failure")
        return original(self, receipt, **kwargs)
    monkeypatch.setattr(ExecutionReceiptService, "record", record)
    prepared = prepare_sources(store, request(index_mode="vector"), context())
    embedded = embed_sources(store, prepared, context(), provider=Provider(), manifest=MANIFEST)
    result = project_sources(store, embedded, context())
    assert result.status in ("failed", "partial") and not result.data["index_ready"]
    assert not store.records("GraphProjection")
    if stage != "source_projection": assert not store.records("EmbeddingBatch")
    if stage == "source_preparation": assert not store.records("SourceRegion")


def test_multiple_sources_and_multichunk_whitespace_remain_exact(store):
    text = "\n\n".join("Claim %d: α = β. " % i + "The argument preserves its original whitespace. " * 12 for i in range(12)) + "\n  "
    prepared, _, result = pipeline(store, request(sources=[{"name": "long.md", "text": text}, {"name": "short.tex", "text": "$x=x$"}]))
    assert result.status == "complete", result
    assert len(prepared.data["sources"]) == 2
    records = [store.get(i).content for i in prepared.data["region_ids"]]
    long = [r for r in records if r["source_id"] == prepared.data["sources"][0]["source_id"]]
    assert len(long) > 1
    assert "".join(r["text"] for r in long) == text


def test_missing_pdf_permission_is_receipted_without_worker_call(store):
    req = request(sources=[{"name": "paper.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}])
    result = prepare_sources(store, req, context(), pdf_normalizer=lambda _: pytest.fail("worker called"))
    assert result.status == "failed" and result.receipt_ids


def test_revoked_index_permission_retains_preparation_and_reports_partial(store):
    prepared = prepare_sources(store, request(), context())
    before = store.revision
    result = embed_sources(store, prepared, context(allow_corpus_writes=False))
    assert result.status == "partial" and not result.data["index_ready"]
    assert result.receipt_ids == prepared.receipt_ids
    assert store.revision == before


def test_pdf_cancellation_is_recorded_and_rolls_back(store):
    def parser(_):
        raise CancelledError()
    req = request(sources=[{"name": "paper.pdf", "data_base64": base64.b64encode(b"%PDF-test").decode()}])
    with pytest.raises(CancelledError):
        prepare_sources(store, req, context(allow_pdf=True), pdf_normalizer=parser)
    assert not store.records("SourceDescriptor")
    assert store.records("ExecutionReceipt")[0][1].content["status"] == "interrupted"
