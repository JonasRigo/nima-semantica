import pytest

from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
from nima_semantica.graph_projection import GraphProjectionRequest, GraphProjectionService
from nima_semantica.models import ConflictError, Record
from nima_semantica.providers import ModelManifest
from nima_semantica.storage import GraphStore


MANIFEST = ModelManifest(provider="fixture", model="embedding", revision="1", parameters={}, dimension=2)


def seed(store):
    from conftest import seed_region
    return seed_region(store, "A source with a theorem")


def test_implicit_refresh_tracks_source_changes(tmp_path):
    from conftest import seed_region
    store = GraphStore(tmp_path)
    try:
        seed(store)
        service = GraphProjectionService(store)
        request = GraphProjectionRequest(corpus_id="papers", project_id="research")
        first = service.rebuild(request)
        seed_region(store, "Another newly indexed theorem")
        second = service.rebuild(request)
        assert first.projection_id != second.projection_id
        assert service.get_current(second.projection_id, corpus_id="papers", project_id="research") == second.manifest
        assert service.rebuild(request) == second
    finally:
        store.close()


def test_projection_rebuilds_all_views_and_replays(tmp_path):
    store = GraphStore(tmp_path)
    try:
        region = seed(store)
        EmbeddingIndexService(store).index(
            EmbeddingIndexRequest(corpus_id="papers", region_ids=(region.id,), manifest=MANIFEST,
                                  idempotency_key="index-1"),
            [[1.0, 0.0]],
        )
        request = GraphProjectionRequest(corpus_id="papers", idempotency_key="projection-1")
        service = GraphProjectionService(store)
        first = service.rebuild(request)
        second = service.rebuild(request)

        assert first.status == second.status == "completed"
        assert first.manifest == second.manifest
        assert set(first.manifest.artifact_ids) == {"lexical", "vector", "structural", "summary"}
        lexical = store.read_artifact(first.manifest.artifact_ids["lexical"])
        assert b"theorem" in lexical
        assert first.manifest.vector_manifest.model == "embedding"
        assert service.get_current(first.projection_id, corpus_id="papers") == first.manifest
        assert len(store.records("GraphProjection", corpus_id="papers")) == 1
    finally:
        store.close()


def test_projection_rejects_stale_projection_reads(tmp_path):
    store = GraphStore(tmp_path)
    try:
        seed(store)
        service = GraphProjectionService(store)
        result = service.rebuild(GraphProjectionRequest(corpus_id="papers"))
        with store.transaction():
            store.put(Record(kind="Unrelated", corpus_id="papers", content={"value": "changed"}))
        assert service.get_current(result.projection_id, corpus_id="papers") == result.manifest
        from test_rewrite_audit_regressions import commit, node
        commit(store, "change", upsert_nodes=(node("new"),))
        with pytest.raises(ConflictError, match="stale"):
            service.get_current(result.projection_id, corpus_id="papers")
    finally:
        store.close()
def test_lexical_hashes_each_region_once():
    from nima_semantica.graph_projection import GraphProjectionService
    class Region:
        content = {"text":"alpha beta alpha " * 500}
        hashes = 0
        @property
        def id(self):
            self.hashes += 1
            return "exact-region"
    region = Region()
    value = GraphProjectionService(None)._lexical([region], 2000)
    assert value["token_count"] == 1500
    assert value["terms"] == {"alpha":["exact-region"],"beta":["exact-region"]}
    assert region.hashes == 1
