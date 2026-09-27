import pytest

from nima_semantica.storage import GraphStore


def seed_region(store, text="Exact source text.", *, corpus_id="papers", project_id=None):
    from nima_semantica.models import Record
    from nima_semantica.source_corpus import SourceDescriptor, SourceRegion, SourceCorpusService
    artifact = store.artifact(text.encode())
    descriptor = SourceDescriptor(source_id=artifact, corpus_id=corpus_id, artifact_id=artifact,
                                  source_revision=artifact, name="Fixture", media_type="text/plain")
    store.put(Record(kind="SourceDescriptor", corpus_id=corpus_id, content=descriptor.model_dump(mode="json")))
    region = SourceRegion(source_id=artifact, corpus_id=corpus_id, project_id=project_id,
                          artifact_id=artifact, source_revision=artifact,
                          start=0, end=len(text), ordinal=0, text=text)
    region_id = SourceCorpusService(store).register_region(region)
    return store.get(region_id)


@pytest.fixture
def store(tmp_path):
    value = GraphStore(tmp_path / "corpus")
    yield value
    value.close()
