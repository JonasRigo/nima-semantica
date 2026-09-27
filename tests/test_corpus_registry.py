import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.artifact_contracts import ArtifactEnvelope, GraphArtifact
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.models import ConflictError
from nima_semantica.okf_contracts import OKFSnapshot
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryEntry, RegistryResourceKind, RegistryRevision
from nima_semantica.storage import GraphStore
from nima_semantica.models import canonical


def envelope(corpus="papers", project="project-a"):
    return ArtifactEnvelope(
        artifact_id="a" * 64, artifact_kind="source_text",
        media_type="application/octet-stream", content_hash="a" * 64,
        corpus_id=corpus, project_id=project,
    )


def test_graph_artifact_binds_exactly_one_okf_payload():
    snapshot = OKFSnapshot(snapshot_id="snapshot-1", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
                           corpus_id="papers", project_id="project-a")
    artifact = GraphArtifact(envelope=envelope(), snapshot=snapshot)
    assert artifact.snapshot.snapshot_id == "snapshot-1"
    with pytest.raises(ValueError, match="exactly one"):
        GraphArtifact(envelope=envelope())


def test_corpus_registry_is_persistent_scoped_and_idempotent(tmp_path):
    store = GraphStore(tmp_path)
    try:
        registry = CorpusRegistry(store)
        registry.register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="corpus:1"))
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=1,
            changed_resource_ids=("artifact-1",),
        ))
        entry = RegistryEntry(
            resource_id="artifact-1", resource_kind=RegistryResourceKind.ARTIFACT,
            corpus_id="papers", project_id="project-a", content_hash="c" * 64,
            registry_revision="registry:1", artifact_id="a" * 64,
        )
        first = registry.register(entry)
        assert registry.register(entry) == first
        assert registry.resolve("artifact-1", corpus_id="papers", project_id="project-a") == entry
        assert registry.list(corpus_id="papers", project_id="project-a") == (entry,)
        with pytest.raises(ConflictError):
            registry.register(entry.model_copy(update={"content_hash": "d" * 64}))
    finally:
        store.close()


def test_registry_rejects_divergent_corpus_revision_and_entry_parent(tmp_path):
    store = GraphStore(tmp_path)
    try:
        registry = CorpusRegistry(store)
        descriptor = CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="corpus:1")
        registry.register_corpus(descriptor)
        with pytest.raises(ConflictError):
            registry.register_corpus(descriptor.model_copy(update={"name": "Other Papers"}))
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=1,
            changed_resource_ids=("artifact-1",),
        ))
        with pytest.raises(ConflictError, match="parent"):
            registry.register_revision(RegistryRevision(
                revision_id="registry:2", corpus_id="papers", sequence=2,
                parent_revision="missing", changed_resource_ids=("artifact-2",),
            ))
        with pytest.raises(ConflictError, match="does not declare"):
            registry.register(RegistryEntry(
                resource_id="artifact-2", resource_kind=RegistryResourceKind.ARTIFACT,
                corpus_id="papers", content_hash="c" * 64,
                registry_revision="registry:1", artifact_id="a" * 64,
            ))
    finally:
        store.close()


def test_registry_revision_parent_must_advance_sequence(tmp_path):
    store = GraphStore(tmp_path)
    try:
        registry = CorpusRegistry(store)
        registry.register_corpus(CorpusDescriptor(
            corpus_id="papers", name="Papers", corpus_revision="corpus:1"
        ))
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=1,
            changed_resource_ids=(),
        ))
        with pytest.raises(ConflictError, match="does not follow"):
            registry.register_revision(RegistryRevision(
                revision_id="registry:3", corpus_id="papers", sequence=3,
                parent_revision="registry:1", changed_resource_ids=(),
            ))
    finally:
        store.close()


def test_artifact_service_binds_bytes_envelope_and_registry_entry(tmp_path):
    store = GraphStore(tmp_path)
    try:
        registry = CorpusRegistry(store)
        registry.register_corpus(CorpusDescriptor(
            corpus_id="papers", name="Papers", corpus_revision="corpus:1"
        ))
        data = b"immutable source bytes"
        digest = __import__("hashlib").sha256(data).hexdigest()
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=1,
            changed_resource_ids=(digest,),
        ))
        artifact = ArtifactEnvelope(
            artifact_id=digest, artifact_kind="source_text",
            media_type="text/plain", content_hash=digest, corpus_id="papers",
            project_id="project-a",
        )
        service = ArtifactService(store, registry)
        service.publish(data, artifact, registry_revision="registry:1")
        envelope, restored = service.read(digest, corpus_id="papers", project_id="project-a")
        assert envelope == artifact and restored == data
        # Replaying the exact publication remains idempotent.
        service.publish(data, artifact, registry_revision="registry:1")
    finally:
        store.close()


def test_artifact_service_rejects_hash_mismatch_and_unbound_resolution(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ArtifactService(store)
        data = b"immutable source bytes"
        with pytest.raises(ConflictError, match="hashes"):
            service.publish(data, envelope(), registry_revision="registry:1")
        with pytest.raises(Exception, match="not registered"):
            service.resolve("a" * 64, corpus_id="papers")
    finally:
        store.close()
