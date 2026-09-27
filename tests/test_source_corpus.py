import pytest

from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.models import ConflictError
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.source_corpus import SourceCorpusService, SourceDescriptor, SourceRegion
from nima_semantica.storage import GraphStore


def setup_service(store, data):
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    registry = CorpusRegistry(store)
    registry.register_corpus(CorpusDescriptor(
        corpus_id="papers", name="Papers", corpus_revision="corpus:1",
    ))
    registry.register_revision(RegistryRevision(
        revision_id="registry:1", corpus_id="papers", sequence=1,
        changed_resource_ids=(digest,),
    ))
    artifacts = ArtifactService(store, registry)
    service = SourceCorpusService(store, artifacts)
    envelope = ArtifactEnvelope(
        artifact_id=digest, artifact_kind="source_text", media_type="text/plain",
        content_hash=digest, corpus_id="papers", source_revision="source:1",
    )
    descriptor = SourceDescriptor(
        source_id="source-1", corpus_id="papers", artifact_id=digest,
        source_revision="source:1", name="paper.txt", media_type="text/plain",
    )
    return service, descriptor, envelope, digest


def test_source_registration_is_idempotent_and_regions_are_exact(tmp_path):
    data = b"first paragraph\nsecond paragraph"
    store = GraphStore(tmp_path)
    try:
        service, descriptor, envelope, digest = setup_service(store, data)
        first = service.register_source(data, descriptor, envelope, registry_revision="registry:1", expected_store_revision=store.revision)
        assert service.register_source(data, descriptor, envelope, registry_revision="registry:1") == first
        region_id = service.register_region(SourceRegion(
            source_id="source-1", corpus_id="papers", artifact_id=digest,
            source_revision="source:1", start=0, end=15, ordinal=0,
            text="first paragraph",
        ))
        assert service.get_region(region_id, corpus_id="papers").text == "first paragraph"
    finally:
        store.close()


def test_region_registration_uses_direct_identity_lookup(store, monkeypatch):
    data = b"first paragraph\nsecond paragraph"
    service, descriptor, envelope, digest = setup_service(store, data)
    service.register_source(data, descriptor, envelope, registry_revision="registry:1")
    region = SourceRegion(source_id="source-1", corpus_id="papers", artifact_id=digest,
        source_revision="source:1", start=0, end=15, ordinal=0, text="first paragraph")
    records = store.records
    def guard(kind=None, *args, **kwargs):
        if kind == "SourceRegion":
            raise AssertionError("region registration scanned the full corpus")
        return records(kind, *args, **kwargs)
    monkeypatch.setattr(store, "records", guard)
    first = service.register_region(region)
    assert service.register_region(region) == first


def test_source_registration_rolls_back_all_metadata_on_descriptor_failure(store, monkeypatch):
    data = b"atomic source"
    service, descriptor, envelope, _ = setup_service(store, data)
    before = store.revision
    put = store.put
    def fail(record):
        if record.kind == "SourceDescriptor":
            raise RuntimeError("injected descriptor failure")
        return put(record)
    monkeypatch.setattr(store, "put", fail)
    with pytest.raises(RuntimeError):
        service.register_source(data, descriptor, envelope, registry_revision="registry:1", expected_store_revision=before)
    assert store.revision == before
    assert not store.records("ArtifactEnvelope")
    assert not store.records("ExecutionReceipt")


def test_source_service_rejects_stale_or_fabricated_regions(tmp_path):
    data = b"first paragraph\nsecond paragraph"
    store = GraphStore(tmp_path)
    try:
        service, descriptor, envelope, digest = setup_service(store, data)
        service.register_source(data, descriptor, envelope, registry_revision="registry:1")
        with pytest.raises(ConflictError, match="offsets or text"):
            service.register_region(SourceRegion(
                source_id="source-1", corpus_id="papers", artifact_id=digest,
                source_revision="source:1", start=0, end=15, ordinal=0,
                text="fabricated text",
            ))
        with pytest.raises(ConflictError, match="different metadata"):
            service.register_source(
                data, descriptor.model_copy(update={"name": "other.txt"}),
                envelope, registry_revision="registry:1",
            )
    finally:
        store.close()
