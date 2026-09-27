import pytest

from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
from nima_semantica.models import Record, canonical
from nima_semantica.providers import ModelManifest
from nima_semantica.storage import GraphStore


MANIFEST = ModelManifest(provider="fixture", model="embedding", revision="1", parameters={}, dimension=2)


def seed(store):
    from conftest import seed_region
    return seed_region(store, "A source with a theorem")


def test_embedding_service_validates_publishes_and_replays(tmp_path):
    store = GraphStore(tmp_path)
    try:
        region = seed(store)
        request = EmbeddingIndexRequest(
            corpus_id="papers", region_ids=(region.id,), manifest=MANIFEST,
            idempotency_key="embedding-replay",
        )
        service = EmbeddingIndexService(store)
        first = service.index(request, [[1.0, 0.0]])
        second = service.index(request, [[1.0, 0.0]])
        assert first.status == second.status == "completed"
        assert first.result == second.result
        assert len(store.records("EmbeddingBatch", corpus_id="papers")) == 1
        assert len(store.records("ExecutionReceipt", corpus_id="papers")) == 1
    finally:
        store.close()


def test_embedding_service_rejects_invalid_vectors_with_receipt(tmp_path):
    store = GraphStore(tmp_path)
    try:
        region = seed(store)
        result = EmbeddingIndexService(store).index(
            EmbeddingIndexRequest(corpus_id="papers", region_ids=(region.id,), manifest=MANIFEST),
            [[0.0, 0.0]],
        )
        assert result.status == "failed"
        assert not store.records("EmbeddingBatch", corpus_id="papers")
        assert len(store.records("ExecutionReceipt", corpus_id="papers")) == 1
    finally:
        store.close()
