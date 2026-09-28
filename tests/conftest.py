import pytest
import os
import subprocess

from nima_semantica.storage import GraphStore


@pytest.fixture(scope="session")
def symbolic_runtime():
    if os.environ.get("NIMA_SYMBOLIC_WORKER_URL"):
        return
    try:
        available = subprocess.run(["docker", "image", "inspect", "nima-sympy:1.14.0-pilot"],
                                   capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        available = False
    if not available:
        message = "requires nima-sympy:1.14.0-pilot or NIMA_SYMBOLIC_WORKER_URL"
        if os.environ.get("NIMA_LIVE_SYMBOLIC") == "1":
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture(autouse=True)
def require_marked_symbolic_runtime(request):
    if request.node.get_closest_marker("symbolic"):
        request.getfixturevalue("symbolic_runtime")


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
