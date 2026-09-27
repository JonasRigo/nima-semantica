"""Native retrieval acceptance with real prepared projections and deterministic vectors."""
from asyncio import CancelledError
import pytest

from nima_semantica.research_retrieval import ResearchRetrievalRequest, ResearchRetrievalContext, ResearchContextPacket, retrieve_research_context
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import Record
from nima_semantica.source_tools import prepare_sources
from test_source_pipeline import store, pipeline, request, context, Provider, MANIFEST


class QueryProvider(Provider):
    def __init__(self, error=None):
        super().__init__()
        self.calls = []
        self.error = error
    def embed_query(self, profile, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return [[1., 0.]], MANIFEST


def retrieve(store, *, provider=None, manifest=MANIFEST, project="research", authorized=True, **kwargs):
    return retrieve_research_context(store, ResearchRetrievalRequest(**({"query": "Claim"} | kwargs)),
        ResearchRetrievalContext(corpus_id="papers", project_id=project,
            allow_embeddings=authorized, allow_attempt_writes=authorized), provider=provider, manifest=manifest)


@pytest.mark.parametrize("kwargs", [{"query": " "}, {"mode": "vector"}, {"limit": 0}, {"limit": True},
    {"max_chars": 100001}, {"max_hops": 5}, {"max_results": 65}, {"query": 12},
    {"operation_id": "lexical-write"}, {"project_id": "secret"}, {"allow_embeddings": True}, {"query_vector": [1,0]}])
def test_invalid_public_request(kwargs):
    with pytest.raises(ValueError):
        ResearchRetrievalRequest.model_validate({"query": "Claim"} | kwargs)


def test_empty_and_unconfigured_fail_closed(store):
    assert retrieve(None).status == "unavailable"
    assert retrieve(store).status == "failed"
    assert not store.records("ExecutionReceipt")


def test_lexical_exact_passages_no_writes(store, monkeypatch):
    prepared, _, result = pipeline(store)
    before = (store.revision, store.records(), store.graph_revision("papers", "research"))
    def forbidden(*args, **kwargs):
        raise AssertionError("lexical retrieval cannot write")
    monkeypatch.setattr(store, "put", forbidden)
    monkeypatch.setattr(store, "artifact", forbidden)
    found = retrieve(store)
    assert found.status == "complete", found
    packet = ResearchContextPacket.model_validate(found.data)
    assert packet.embedding_identity is None
    assert len(packet.regions) == 1
    assert packet.regions[0]["text"] == request().sources[0].text
    assert packet.regions[0]["region_id"] == prepared.data["region_ids"][0]
    assert packet.projection_id == result.data["projection"]["projection_id"]
    assert packet.regions[0]["evidence"]["project_id"] is None
    assert not found.receipt_ids
    assert (store.revision, store.records(), store.graph_revision("papers", "research")) == before


def test_empty_match_and_whole_region_character_omission(store):
    pipeline(store)
    assert retrieve(store, query="zzzzzz").data["regions"] == []
    result = retrieve(store, max_chars=1)
    assert result.status == "partial" and result.data["regions"] == []
    assert result.data["traversal"]["omitted_for_character_limit"] == 1


def test_stale_revision_and_projection_rejected(store):
    _, _, result = pipeline(store)
    assert retrieve(store, expected_store_revision="sqlite:0").status == "failed"
    assert retrieve(store, projection_id="absent").status == "failed"
    prepare_sources(store, request(operation_id="new", sources=[{"name":"b.md", "text":"another"}]), context())
    assert retrieve(store, projection_id=result.data["projection"]["projection_id"]).status == "failed"


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_model_query_receipt_and_replay(store, mode):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    graph = store.graph_revision("papers", "research")
    provider = QueryProvider()
    result = retrieve(store, provider=provider, mode=mode, operation_id="query-1")
    assert result.status == "complete", result
    assert len(provider.calls) == 1 and result.receipt_ids
    assert result.data["embedding_identity"]["model"] == MANIFEST.model
    assert retrieve(store, provider=provider, mode=mode, operation_id="query-1") == result
    assert len(provider.calls) == 1
    assert retrieve(store, provider=provider, mode=mode, operation_id="query-1", query="changed").status == "failed"
    assert len(provider.calls) == 1
    assert store.graph_revision("papers", "research") == graph
    receipt = ExecutionReceiptService(store).get(result.receipt_ids[0], corpus_id="papers", project_id="research")
    assert receipt.status == "completed"


def test_permission_and_manifest_gate_before_provider(store):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    provider = QueryProvider()
    before = store.revision
    result = retrieve(store, provider=provider, mode="vector", operation_id="denied", authorized=False)
    assert result.status == "failed" and not result.receipt_ids
    assert store.revision == before and not provider.calls
    result = retrieve(store, provider=provider, manifest=MANIFEST.model_copy(update={"revision":"wrong"}), mode="vector", operation_id="wrong")
    assert result.status == "failed" and result.receipt_ids and not provider.calls


@pytest.mark.parametrize("error", [RuntimeError("secret provider token"), CancelledError(), KeyboardInterrupt()])
def test_failed_cancelled_attempts(store, error):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    provider = QueryProvider(error)
    if isinstance(error, (CancelledError, KeyboardInterrupt)):
        with pytest.raises(type(error)):
            retrieve(store, provider=provider, mode="vector", operation_id="failed")
    else:
        result = retrieve(store, provider=provider, mode="vector", operation_id="failed")
        assert result.status == "failed" and "secret provider token" not in result.model_dump_json()
    receipts = [r for _,r in store.records("ExecutionReceipt") if r.content["stage"] == "research_retrieval"]
    assert len(receipts) == 1
    assert receipts[0].content["status"] == ("failed" if isinstance(error, Exception) else "interrupted")


@pytest.mark.parametrize("vector", [[[0.,0.]], [[float("nan"),0.]], [[1.]], [[1.,0.],[1.,0.]]])
def test_bad_query_vectors_fail(store, vector):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    provider = QueryProvider()
    provider.embed_query = lambda *args: (vector, MANIFEST)
    assert retrieve(store, provider=provider, mode="vector", operation_id="bad-vector").status == "failed"


def test_scope_and_foreign_malformed_records(store):
    for kind in ("SourceRegion", "GraphProjection", "EmbeddingBatch"):
        store.put(Record(kind=kind, corpus_id="papers", project_id="secret", content={"secret":"NEVER RETURN"}))
        store.put(Record(kind=kind, corpus_id="other", content={"secret":"NEVER RETURN"}))
    pipeline(store)
    result = retrieve(store)
    assert result.status == "complete" and "NEVER RETURN" not in result.model_dump_json()
    assert retrieve(store, project=None).status == "failed"
    assert retrieve(store, project="other").status == "failed"


def test_corrupt_source_bytes_fail_exact_resolution(store, monkeypatch):
    prepared, _, _ = pipeline(store)
    artifact = prepared.data["sources"][0]["normalized_artifact_id"]
    original = store.read_artifact
    monkeypatch.setattr(store, "read_artifact", lambda key: b"corrupt" if key == artifact else original(key))
    result = retrieve(store)
    assert result.status == "failed" and not result.data


def test_partial_vector_coverage_explicit(store):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    pipeline(store, request(operation_id="new", sources=[{"name":"b.md", "text":"No vector yet"}]))
    result = retrieve(store, provider=QueryProvider(), mode="vector", operation_id="partial")
    assert result.status == "partial" and result.data["traversal"]["partial_vector_coverage"]


def test_graph_expansion_paths_and_limits(store):
    from conftest import seed_region
    from test_rewrite_audit_regressions import commit, node, edge
    from nima_semantica.okf_contracts import EvidenceReference
    from nima_semantica.graph_projection import GraphProjectionRequest, GraphProjectionService
    a = seed_region(store, "UniqueAlpha claim")
    b = seed_region(store, "SeparateBeta supporting premise")
    def ref(record):
        return EvidenceReference(corpus_id="papers", region_id=record.id, artifact_id=record.content["artifact_id"],
            content_hash=record.content["artifact_id"], source_revision=record.content["source_revision"])
    commit(store, "graph", upsert_nodes=(node("a", evidence=(ref(a),), properties={"statement":"Claim A"}),
        node("b", evidence=(ref(b),), properties={"statement":"Premise B"})), add_edges=(edge("supports", "b", "a"),))
    projected = GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id="papers", project_id="research"))
    assert projected.status == "completed"
    result = retrieve(store, query="UniqueAlpha", max_hops=3)
    assert result.status == "complete", result
    assert {r["region_id"] for r in result.data["regions"]} == {a.id,b.id}
    assert len(result.data["graph_nodes"]) == 2
    assert any(p["relation"] == "supports" and p["traversal_direction"] == "reverse" for p in result.data["paths"])
    for limits in ({"max_hops":0}, {"max_nodes":1}, {"max_edges":1}, {"max_results":1}, {"max_neighbors":1}):
        limited = retrieve(store, query="UniqueAlpha", **limits)
        assert limited.status == "partial" and limited.data["truncated"], limits
        assert limited.data["traversal"]["visited_nodes"] <= limits.get("max_nodes",64)
        assert limited.data["traversal"]["examined_edges"] <= limits.get("max_edges",128)


def test_math_source_exact_bytes_and_lexical_fixture_ranking(store):
    text = "The assumption is $x \\neq 0$.\nThen $x/x=1$. Ω is not zero."
    pipeline(store, request(sources=[{"name":"math.tex", "text":text}, {"name":"other.md", "text":"Unrelated biology"}]))
    result = retrieve(store, query="assumption neq", limit=1, max_hops=0)
    assert result.status == "complete", result
    assert [p["text"] for p in result.data["regions"]] == [text]


def test_missing_backend_and_foreign_run_receipted_without_call(store):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    assert retrieve(store, mode="vector", operation_id="missing").status == "failed"
    provider = QueryProvider()
    assert retrieve(store, provider=provider, mode="vector", operation_id="bad-run", run_id="absent").status == "failed"
    assert not provider.calls


def test_returned_manifest_mismatch_and_revoked_replay_permission(store):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    provider = QueryProvider()
    provider.embed_query = lambda *args: ([[1.,0.]], MANIFEST.model_copy(update={"model":"different"}))
    assert retrieve(store, provider=provider, mode="vector", operation_id="mismatch").status == "failed"
    ok = retrieve(store, provider=QueryProvider(), mode="vector", operation_id="ok")
    assert ok.status == "complete"
    assert retrieve(store, provider=QueryProvider(), mode="vector", operation_id="ok", authorized=False).status == "failed"


def test_packet_rejects_foreign_evidence_scope(store):
    pipeline(store)
    payload = retrieve(store).data
    payload["regions"][0]["evidence"]["project_id"] = "foreign"
    with pytest.raises(ValueError):
        ResearchContextPacket.model_validate(payload)


def test_receipt_storage_failure_rolls_back_success(store, monkeypatch):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    original = ExecutionReceiptService.record
    calls = []
    def fail_once(self, receipt, **kwargs):
        calls.append(receipt.status)
        if len(calls) == 1:
            raise RuntimeError("injected receipt failure")
        return original(self, receipt, **kwargs)
    monkeypatch.setattr(ExecutionReceiptService, "record", fail_once)
    result = retrieve(store, provider=QueryProvider(), mode="vector", operation_id="receipt-failure")
    assert result.status == "failed"
    attempts = [r for _,r in store.records("ExecutionReceipt") if r.content["stage"] == "research_retrieval"]
    assert len(attempts) == 1 and attempts[0].content["status"] == "failed"
