"""Regression checks for the shared authenticated artifact reader."""
import hashlib
import pytest
from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.models import NimaError, Record
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision


def publish(store, data=b"private", project="private"):
    digest = hashlib.sha256(data).hexdigest()
    registry = CorpusRegistry(store)
    registry.register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
    registry.register_revision(RegistryRevision(revision_id="r1", corpus_id="papers", sequence=0, changed_resource_ids=(digest,)))
    envelope = ArtifactEnvelope(artifact_id=digest, content_hash=digest, artifact_kind="evidence",
        media_type="text/plain", corpus_id="papers", project_id=project)
    ArtifactService(store).publish(data,envelope,registry_revision="r1")
    return digest


def test_corpus_only_cannot_resolve_or_list_private_artifacts(store):
    digest = publish(store)
    registry = CorpusRegistry(store)
    assert registry.resolve(digest, corpus_id="papers") is None
    assert registry.list(corpus_id="papers") == ()
    with pytest.raises(NimaError):
        ArtifactService(store).read(digest, corpus_id="papers")
    assert ArtifactService(store).list(corpus_id="papers").total == 0
    assert ArtifactService(store).read(digest, corpus_id="papers", project_id="private")[1] == b"private"


@pytest.mark.parametrize("field,value", [("corpus_id","other"),("project_id","private"),("content_hash","0"*64),
    ("source_revision","forged"),("registry_revision","absent"),("status","revoked")])
def test_registry_binding_tampering_rejected(store, monkeypatch, field, value):
    digest = publish(store, project=None)
    registry = CorpusRegistry(store)
    entry = registry.resolve(digest,corpus_id="papers")
    monkeypatch.setattr(registry,"resolve",lambda *args,**kwargs: entry.model_copy(update={field:value}))
    with pytest.raises(NimaError):
        ArtifactService(store,registry).read(digest,corpus_id="papers")


def test_envelope_row_payload_scope_disagreement_rejected(store, monkeypatch):
    digest = publish(store,project=None)
    original = store.get
    def changed(key, **kwargs):
        record = original(key, **kwargs)
        if record and record.kind == "ArtifactEnvelope":
            return record.model_copy(update={"content": record.content | {"project_id":"private"}})
        return record
    monkeypatch.setattr(store,"get",changed)
    with pytest.raises(NimaError):
        ArtifactService(store).read(digest,corpus_id="papers")


def test_foreign_malformed_registry_entries_are_not_parsed(store):
    store.put(Record(kind="SystemRegistryEntry",corpus_id="papers",project_id="private",content={"bad":"private"}))
    assert CorpusRegistry(store).list(corpus_id="papers") == ()


def test_revision_payload_scope_must_match(store,monkeypatch):
    digest=publish(store,project=None)
    registry=CorpusRegistry(store)
    revision=registry.revision("r1",corpus_id="papers")
    monkeypatch.setattr(registry,"revision",lambda *args,**kwargs: revision.model_copy(update={"corpus_id":"other"}))
    with pytest.raises(NimaError):
        ArtifactService(store,registry).read(digest,corpus_id="papers")
