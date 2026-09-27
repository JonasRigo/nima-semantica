import json

import pytest

from nima_semantica.models import Record, NimaError, ConflictError, ConfigurationError, canonical
from nima_semantica.providers import ModelManifest
from nima_semantica.storage import GraphStore
from nima_semantica.retrieval import retrieve


class Provider:
    manifest = ModelManifest(provider="fixture", model="precomputed", revision="1", parameters={}, dimension=2)

    def embed_query(self, profile, question):
        return [[1., 0.]], self.manifest


def seed(store, corpus="a", project=None, count=4):
    records = [Record(kind="SourceRegion", corpus_id=corpus, project_id=project,
                      content={"document_id": str(i), "ordinal": 0, "text": str(i)}) for i in range(count)]
    matrix = [[1., 0.]] + [[0., 1.]] * (count - 1)
    batch = Record(kind="EmbeddingBatch", corpus_id=corpus, project_id=project, content={
        "region_ids": [r.id for r in records], "matrix_artifact": store.artifact(canonical(matrix)),
        "manifest": Provider.manifest.model_dump(mode="json")})
    with store.transaction():
        for record in records + [batch]:
            store.put(record)
    return records


def test_warm_query_no_record_scan_or_artifact_reload(store, monkeypatch):
    seed(store)
    retrieve(store, Provider(), "", "q", None, "a", limit=1)
    with store.transaction():
        store.put(Record(kind="Receipt", corpus_id="a", content={}))
    def forbidden(*args, **kwargs):
        pytest.fail("warm query reloaded corpus")
    monkeypatch.setattr(store, "records", forbidden)
    monkeypatch.setattr(store, "read_artifact", forbidden)
    result, receipt = retrieve(store, Provider(), "", "q", None, "a", limit=1, max_omitted=1)
    assert len(result) == 1
    assert receipt.content["omitted_count"] == 3
    assert receipt.content["omitted_complete"] is False


def test_persistent_cache_no_vectors_after_reopen(tmp_path, monkeypatch):
    store = GraphStore(tmp_path)
    seed(store)
    retrieve(store, Provider(), "", "q", None, "a", limit=1)
    store.close()
    store = GraphStore(tmp_path)
    monkeypatch.setattr(store, "records", lambda *a, **k: pytest.fail("scanned records"))
    monkeypatch.setattr(store, "read_artifact", lambda *a: pytest.fail("loaded vectors"))
    try:
        assert retrieve(store, Provider(), "", "q", None, "a", limit=1)[0]
    finally:
        store.close()


def test_scoped_semantic_citation_and_budgets(store):
    regions = seed(store)
    foreign = seed(store, "b")
    assertion = Record(kind="SemanticCitation", corpus_id="a", content={})
    with store.transaction():
        store.put(assertion)
        store.link(regions[0].id, assertion.id, "cites", "receipt1")
        store.link(assertion.id, regions[1].id, "supported_by", "receipt2")
    store.link(regions[0].id, foreign[0].id, "cites", "r")
    assert foreign[0].id not in {e["id"] for e in store.neighbors(regions[0].id, corpus_id="a")}
    results, selection = retrieve(store, Provider(), "", "q", None, "a", limit=1)
    assert {r["id"] for r in results} == {r.id for r in regions[:2]}
    assert selection.content["selected"][1]["graph_paths"] == [[regions[0].id, assertion.id, regions[1].id]]
    assert [e["receipt_id"] for e in selection.content["selected"][1]["graph_edges"]] == ["receipt1", "receipt2"]
    results, selection = retrieve(store, Provider(), "", "q", None, "a", limit=1, max_nodes=2)
    assert len(results) == 1
    assert selection.content["traversal"]["budget_exhausted"]


def test_project_isolation_and_generation(store):
    seed(store, project="one")
    two = seed(store, project="two")
    result, _ = retrieve(store, Provider(), "", "q", "two", "a", limit=1)
    assert result[0]["id"] == two[0].id
    with pytest.raises(NimaError, match="nested"):
        with store.transaction():
            seed(store, "rollback")  # nested transactions fail without damaging outer state
            raise RuntimeError()


def test_rollback_generation_and_stale_revision(store):
    before = store.revision
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put(Record(kind="SourceRegion", corpus_id="a", content={}))
            raise RuntimeError()
    assert store.embedding_revision("a") == 0
    assert store.revision == before
    store.put(Record(kind="Receipt", content={}))
    with pytest.raises(ConflictError):
        with store.transaction(before):
            pass


def test_manifest_mismatch_and_cache_corruption(store):
    seed(store)
    retrieve(store, Provider(), "", "q", None, "a")
    store._vector_cache.clear()
    next((store.root / "indexes").rglob("index.faiss")).write_bytes(b"broken")
    with pytest.raises(ConfigurationError, match="corrupted"):
        retrieve(store, Provider(), "", "q", None, "a")


def test_new_generation_and_manifest_identity(store):
    seed(store, count=2)
    first = retrieve(store, Provider(), "", "q", None, "a")[1]
    seed(store, count=5)
    second = retrieve(store, Provider(), "", "q", None, "a")[1]
    assert first.content["index_manifest"]["vector_count"] == 2
    assert second.content["index_manifest"]["vector_count"] == 5
    provider = Provider()
    provider.manifest = provider.manifest.model_copy(update={"revision":"other"})
    with pytest.raises(ConfigurationError, match="manifest mismatch"):
        retrieve(store, provider, "", "q", None, "a")


def test_rolled_back_index_cannot_alias_future_generation(store):
    region = seed(store,count=1)[0]
    before = store.embedding_revision("a")
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put(Record(kind="SourceRegion",corpus_id="a",content={"text":"rolled back"}))
            retrieve(store,Provider(),"","q",None,"a")
            abandoned = store.embedding_revision("a")
            raise RuntimeError()
    assert store.embedding_revision("a") == before
    store.put(Record(kind="SourceRegion",corpus_id="a",content={"text":"different"}))
    assert store.embedding_revision("a") != abandoned
