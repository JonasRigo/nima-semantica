"""Scoped artifact-backed source preparation without an inline PDF-size loophole."""
import base64
import pytest

from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.models import Record
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.source_tools import (AcquiredSourceInput, PrepareSourcesRequest,
    SourceInput, SourceToolContext, embed_sources, prepare_sources, project_sources)
from nima_semantica.storage import GraphStore


def archived(store, data, *, project_id="research", name="paper.pdf"):
    artifact_id = store.artifact(data)
    document = Record(kind="Document", corpus_id="papers", project_id=project_id,
        content={"artifact_id": artifact_id})
    attempt = Record(kind="AcquisitionAttempt", corpus_id="papers", project_id=project_id,
        parents=(document.id,), content={"artifact_id": artifact_id, "name": name,
            "provenance_artifact_id": store.artifact(b"provenance"), "status": "completed"})
    store.put(document)
    store.put(attempt)
    return AcquiredSourceInput(name="source.pdf", acquired_name=name, artifact_id=artifact_id,
        document_id=document.id, acquisition_id=attempt.id)


@pytest.mark.parametrize("payload_size", (2_100_000, 5_780_000))
def test_acquired_pdf_over_inline_limit_uses_exact_archived_bytes(tmp_path, payload_size):
    store = GraphStore(tmp_path / "store")
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="1"))
    original = b"%PDF-" + b"x" * payload_size
    source = archived(store, original)
    graph_before = store.graph_revision("papers", "research")
    context = SourceToolContext(corpus_id="papers", project_id="research", allow_corpus_writes=True, allow_pdf=True)
    request = PrepareSourcesRequest(mode="prepare_index", operation_id="large-pdf", index_mode="lexical", sources=(source,))
    calls = []
    def normalize(data):
        calls.append(data)
        return "Page one contains an exact research statement.", [{"kind": "parser_manifest", "provenance": [{"page": 1}]}]
    prepared = prepare_sources(store, request, context, pdf_normalizer=normalize)
    assert prepared.status == "complete", prepared
    projected = project_sources(store, embed_sources(store, prepared, context), context)
    assert projected.status == "complete" and projected.data["index_ready"]
    assert calls == [original]
    assert prepared.data["sources"][0]["original_artifact_id"] == source.artifact_id
    assert store.graph_revision("papers", "research") == graph_before
    store.close()


def test_acquired_source_rejects_cross_project_and_mismatched_identity(tmp_path):
    store = GraphStore(tmp_path / "store")
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="1"))
    source = archived(store, b"%PDF-one")
    before = store.revision
    request = PrepareSourcesRequest(mode="prepare_index", operation_id="foreign", sources=(source,))
    result = prepare_sources(store, request, SourceToolContext(corpus_id="papers", project_id="other",
        allow_corpus_writes=True, allow_pdf=True), pdf_normalizer=lambda _: ("text", []))
    assert result.status == "failed" and not store.records("SourceDescriptor")
    assert store.revision != before  # Failed attempt receipt only.
    wrong = source.model_copy(update={"acquired_name": "different.pdf"})
    result = prepare_sources(store, PrepareSourcesRequest(mode="prepare_index", operation_id="mismatch", sources=(wrong,)),
        SourceToolContext(corpus_id="papers", project_id="research", allow_corpus_writes=True, allow_pdf=True),
        pdf_normalizer=lambda _: ("text", []))
    assert result.status == "failed" and not store.records("SourceDescriptor")
    store.close()


def test_inline_limit_and_acquired_preview_remain_closed(tmp_path):
    assert SourceInput(name="small.pdf", data_base64=base64.b64encode(b"%PDF-x").decode()).bytes() == b"%PDF-x"
    try:
        SourceInput(name="large.pdf", data_base64=base64.b64encode(b"%PDF-" + b"x" * 2_000_000).decode()).bytes()
    except ValueError:
        pass
    else:
        raise AssertionError("inline transport accepted oversized PDF")
    store = GraphStore(tmp_path / "store")
    source = archived(store, b"%PDF-one")
    result = prepare_sources(store, PrepareSourcesRequest(mode="preview", sources=(source,)),
        SourceToolContext(corpus_id="papers", project_id="research"))
    assert result.status == "unavailable" and result.diagnostics[0]["code"] == "sources.acquired_requires_receipted_preparation"
    store.close()
