import hashlib

from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.document_ingestion import DocumentIngestionService
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision
from nima_semantica.source_corpus import SourceCorpusService, SourceDescriptor
from nima_semantica.storage import GraphStore
from nima_semantica.corpus import normalize


def test_document_ingestion_persists_normalized_artifact_regions_and_receipt(tmp_path):
    data = b"<html><body><p>A source statement.</p></body></html>"
    normalized_text, _ = normalize(data, "paper.html")
    source_digest = hashlib.sha256(data).hexdigest()
    normalized_digest = hashlib.sha256(normalized_text.encode()).hexdigest()
    store = GraphStore(tmp_path)
    try:
        registry = CorpusRegistry(store)
        registry.register_corpus(CorpusDescriptor(
            corpus_id="papers", name="Papers", corpus_revision="corpus:1",
        ))
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=1,
            changed_resource_ids=(source_digest, normalized_digest),
        ))
        artifacts = ArtifactService(store, registry)
        service = DocumentIngestionService(
            store, SourceCorpusService(store, artifacts), artifacts=artifacts,
        )
        descriptor = SourceDescriptor(
            source_id="source-1", corpus_id="papers", artifact_id=source_digest,
            source_revision="source:1", name="paper.html", media_type="text/html",
        )
        envelope = ArtifactEnvelope(
            artifact_id=source_digest, artifact_kind="source_text", media_type="text/html",
            content_hash=source_digest, corpus_id="papers", source_revision="source:1",
        )
        result = service.ingest(data, descriptor, envelope, registry_revision="registry:1")
        assert result.normalized_artifact_id == normalized_digest
        assert result.region_ids
        assert service.receipts.get(result.receipt_id, corpus_id="papers") is not None
        assert store.records("Document", corpus_id="papers")
        assert store.records("NormalizedDocument", corpus_id="papers")
        from nima_semantica.evidence_provenance import EvidenceProvenanceService
        from nima_semantica.okf_contracts import EvidenceReference
        region = service.sources.get_region(result.region_ids[0], corpus_id="papers")
        reference = EvidenceReference(corpus_id="papers", region_id=result.region_ids[0],
            artifact_id=normalized_digest, content_hash=normalized_digest,
            source_revision=descriptor.source_revision, quotation=region.text)
        assert EvidenceProvenanceService(store).validate_reference(reference, corpus_id="papers", project_id=None, target_id="claim").valid
    finally:
        store.close()


def test_dense_math_diagnostics_are_linked_completely_not_truncated(tmp_path):
    from test_source_pipeline import pipeline, request
    store = GraphStore(tmp_path)
    try:
        CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Dense math", corpus_revision="1"))
        text = "\n\n".join(f"Equation {i}: $x_{i}=1$." for i in range(1030))
        prepared, _, indexed = pipeline(store, request(sources=[{"name":"dense.md","text":text}]))
        assert indexed.status == "complete"
        assert prepared.data["sources"][0]["diagnostic_count"] == 1030
        _, normalized = store.records("NormalizedDocument", corpus_id="papers")[0]
        assert len(normalized.content["diagnostics"]) == 1030
        covered = set()
        for key in prepared.data["region_ids"]:
            region = store.get(key)
            meta = region.content["metadata"]
            assert meta["normalization_record_id"] == normalized.id
            assert len(meta["normalization_diagnostics"]) < 1024
            covered.update(d["start"] for d in meta["normalization_diagnostics"])
        assert len(covered) == 1030
        receipt = next(r for _,r in store.records("ExecutionReceipt") if r.content["stage"] == "document_ingestion")
        assert receipt.content["diagnostics"][0]["record_id"] == normalized.id
    finally:
        store.close()
