"""No-network acceptance for the bounded Deep Research replacement."""
from asyncio import CancelledError
import pytest

from nima_semantica.deep_research_contracts import DeepResearchRequest
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.models import AcquisitionPolicy
from nima_semantica.okf_contracts import OKFDelta
from nima_semantica.project_update import UpdateProjectContext, UpdateProjectRequest, update_project_graph
from nima_semantica.providers import Invocation
from nima_semantica.simple_deep_research import SimpleDeepResearchContext, simple_deep_research, stock_arxiv_hits
from model_fixture import MANIFEST
from test_deep_extraction import region
from test_source_pipeline import store


def request(store, operation="simple-review"):
    return DeepResearchRequest(mode="research", operation_id=operation,
        graph_revision=store.graph_revision("papers", "research"),
        questions=({"question_id": "q", "question": "What does the paper claim about pairing?"},))


def context(**overrides):
    values = dict(corpus_id="papers", project_id="research", allow_model_calls=True,
        allow_audit_writes=True, allow_source_ingestion=True,
        discovery_providers=("openalex",),
        acquisition=AcquisitionPolicy(enabled=True, domains=("example.org",)), model_manifest=MANIFEST)
    return SimpleDeepResearchContext(**(values | overrides))


def hit():
    return {"provider": "openalex", "provider_id": "https://openalex.org/W1", "title": "Pairing claim",
        "doi": "10.1234/pairing", "arxiv_id": None, "year": 2024, "abstract": "A pairing claim",
        "full_text_urls": ["https://example.org/paper.html"], "license": None, "metadata_hash": "1" * 64}


def draft(region_id):
    return {"nodes": [
        {"node_id": "paper-one", "node_type": "paper", "source_region_ids": [region_id], "properties": {"text": "Pairing claim", "attribution": "paper"}},
        {"node_id": "pairing", "node_type": "entity", "source_region_ids": [region_id], "properties": {"text": "pairing"}},
        {"node_id": "claim-one", "node_type": "claim", "source_region_ids": [region_id], "properties": {"text": "The source reports a pairing mechanism", "attribution": "paper"}}],
        "edges": [
            {"edge_id": "paper-asserts-claim", "relation": "asserts", "source_id": "paper-one", "target_id": "claim-one", "source_region_ids": [region_id]},
            {"edge_id": "claim-about-pairing", "relation": "about", "source_id": "claim-one", "target_id": "pairing", "source_region_ids": [region_id]}],
        "sections": [{"question_id": "q", "answer": "The paper reports a pairing mechanism.", "status": "source_grounded", "node_ids": ["claim-one"], "limitations": ["The mechanism has not been independently established."]}],
        "gaps": ["Independent verification remains open."]}


def run(store, monkeypatch, *, invalid=False):
    item = region(store, "The source reports a pairing mechanism, with assumptions still open.")
    def prepared(*args):
        return {"candidate_id": args[3]["candidate_id"], "status": "indexed", "region_ids": [item.id]}
    monkeypatch.setattr("nima_semantica.simple_deep_research._prepare_paper", prepared)
    def search(provider, query):
        assert provider == "openalex"
        return {"provider": provider, "outcome": "metadata_ready", "hits": [hit()]}
    result = draft("outside" if invalid else item.id)
    def model(prompt):
        assert item.id in {r["region_id"] for r in prompt["regions"]}
        return Invocation(result=result, manifest=MANIFEST, input_tokens=100, output_tokens=60)
    req = request(store, "simple-invalid" if invalid else "simple-valid")
    return req, simple_deep_research(store, req, context(), model=model, paper_search=search)


def test_review_graph_is_source_bound_and_commit_requires_exact_approval(store, monkeypatch):
    req, result = run(store, monkeypatch)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    graph = result.data["review_graph"]
    assert graph["artifact"]["delta"]["ontology_profile"]
    assert {node["node_type"] for node in graph["artifact"]["delta"]["upsert_nodes"]} == {"review", "paper", "entity", "claim"}
    assert {edge["relation"] for edge in graph["artifact"]["delta"]["add_edges"]} >= {"reviews", "discusses", "asserts", "about"}
    assert all(node["status"] == "proposed" for node in graph["artifact"]["delta"]["upsert_nodes"])
    assert store.graph_revision("papers", "research") == req.graph_revision
    pending = result.data["project_graph_prepare_request"]
    prepared = update_project_graph(store, UpdateProjectRequest.model_validate(pending), UpdateProjectContext(corpus_id="papers", project_id="research"))
    assert prepared.status == "complete"
    denied = update_project_graph(store, UpdateProjectRequest.model_validate({**pending, "mode": "commit"}),
        UpdateProjectContext(corpus_id="papers", project_id="research", allow_graph_writes=True, allow_audit_writes=True))
    assert denied.status == "failed" and store.graph_revision("papers", "research") == req.graph_revision
    approval = OKFCommitApproval.for_delta(OKFDelta.model_validate(prepared.data["delta"]), approval_id="approve-review", approved_by="harness", rationale="Record source-attributed review, not scientific truth.")
    committed = update_project_graph(store, UpdateProjectRequest.model_validate({**pending, "mode": "commit"}),
        UpdateProjectContext(corpus_id="papers", project_id="research", allow_graph_writes=True,
            allow_audit_writes=True, approval=approval))
    assert committed.data["committed"] and store.graph_revision("papers", "research") != req.graph_revision


def test_unread_region_is_not_allowed_to_ground_graph(store, monkeypatch):
    _, result = run(store, monkeypatch, invalid=True)
    assert result.status == "failed"
    assert result.data["error"] == "ConflictError", result.data
    assert "review_graph" not in result.data


def test_two_papers_share_one_source_attributed_entity_in_review_graph(store, monkeypatch):
    first = region(store, "Paper one reports a pairing mechanism under approximation A.")
    second = region(store, "Paper two disputes the pairing mechanism under approximation B.")
    def prepared(_store, _request, _context, candidate, _pdf_normalizer):
        region_id = first.id if candidate["observations"][0]["title"] == "Pairing claim" else second.id
        return {"candidate_id": candidate["candidate_id"], "status": "indexed", "region_ids": [region_id]}
    monkeypatch.setattr("nima_semantica.simple_deep_research._prepare_paper", prepared)
    second_hit = hit() | {"provider_id": "https://openalex.org/W2", "title": "Pairing dispute",
        "doi": "10.1234/dispute", "full_text_urls": ["https://example.org/other.html"], "metadata_hash": "2" * 64}
    def search(provider, query):
        return {"provider": provider, "outcome": "metadata_ready", "hits": [hit(), second_hit]}
    def model(prompt):
        assert {row["region_id"] for row in prompt["regions"]} == {first.id, second.id}
        result = {"nodes": [
            {"node_id": "paper-one", "node_type": "paper", "source_region_ids": [first.id], "properties": {"text": "Pairing claim"}},
            {"node_id": "paper-two", "node_type": "paper", "source_region_ids": [second.id], "properties": {"text": "Pairing dispute"}},
            {"node_id": "pairing", "node_type": "entity", "source_region_ids": [first.id, second.id], "properties": {"text": "pairing mechanism"}},
            {"node_id": "claim-one", "node_type": "claim", "source_region_ids": [first.id], "properties": {"text": "Reports mechanism under A"}},
            {"node_id": "claim-two", "node_type": "claim", "source_region_ids": [second.id], "properties": {"text": "Disputes mechanism under B"}}],
            "edges": [
                {"edge_id": "assert-one", "relation": "asserts", "source_id": "paper-one", "target_id": "claim-one", "source_region_ids": [first.id]},
                {"edge_id": "assert-two", "relation": "asserts", "source_id": "paper-two", "target_id": "claim-two", "source_region_ids": [second.id]},
                {"edge_id": "about-one", "relation": "about", "source_id": "claim-one", "target_id": "pairing", "source_region_ids": [first.id]},
                {"edge_id": "about-two", "relation": "about", "source_id": "claim-two", "target_id": "pairing", "source_region_ids": [second.id]}],
            "sections": [{"question_id": "q", "answer": "The papers report differing conclusions under different approximations.",
                "status": "source_grounded", "node_ids": ["claim-one", "claim-two"], "limitations": ["Applicability remains unresolved."]}],
            "gaps": ["The approximation regimes have not been reconciled."]}
        return Invocation(result=result, manifest=MANIFEST, input_tokens=200, output_tokens=100)
    result = simple_deep_research(store, request(store, "simple-two-papers"), context(max_papers=2), model=model, paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    delta = result.data["review_graph"]["artifact"]["delta"]
    assert len([node for node in delta["upsert_nodes"] if node["node_type"] == "entity"]) == 1
    assert len([edge for edge in delta["add_edges"] if edge["relation"] == "reviews"]) == 2
    assert store.graph_revision("papers", "research") == request(store).graph_revision


def test_stock_arxiv_metadata_and_html_availability_are_distinct():
    rows = [{"id": "http://arxiv.org/abs/2601.12345", "title": "A paper", "summary": "Abstract", "pdf_url": "https://arxiv.org/pdf/2601.12345"}]
    hits, errors = stock_arxiv_hits(rows, [{"url": "https://arxiv.org/html/2601.12345", "text": "HTML available"}])
    assert not errors and hits[0]["full_text_urls"][0] == "https://arxiv.org/html/2601.12345"
    hits, errors = stock_arxiv_hits(rows, [])
    assert not errors and hits[0]["full_text_urls"] == ["https://arxiv.org/pdf/2601.12345"]
    assert stock_arxiv_hits([{"error": "HTTP 406"}], [])[1] == ["HTTP 406"]


def test_arxiv_failure_does_not_block_openalex_full_text_review(store, monkeypatch):
    item = region(store, "The source reports a pairing mechanism.")
    monkeypatch.setattr("nima_semantica.simple_deep_research._prepare_paper",
        lambda *args: {"candidate_id": args[3]["candidate_id"], "status": "indexed", "region_ids": [item.id]})
    def search(provider, query):
        assert provider == "openalex"
        return {"provider": provider, "outcome": "metadata_ready", "hits": [hit()]}
    def model(prompt):
        return Invocation(result=draft(item.id), manifest=MANIFEST, input_tokens=100, output_tokens=60)
    result = simple_deep_research(store, request(store, "simple-arxiv-fallback"),
        context(discovery_providers=("arxiv", "openalex")), model=model, stock_errors=("HTTP 406",), paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert result.data["discovery"][0]["provider"] == "arxiv"
    assert result.data["discovery"][0]["errors"] == ["HTTP 406"]
    assert result.data["discovery"][1]["provider"] == "openalex"
    assert result.data["review_graph"]


def test_native_html_acquisition_index_exact_read_and_graph(store, monkeypatch):
    raw = b"<html><body><h1>Pairing mechanism</h1><p>The paper reports a pairing mechanism under stated assumptions.</p></body></html>"
    calls = []
    def acquire(address, policy):
        calls.append(address)
        return raw, "paper.html", {"content_type": "text/html"}
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", acquire)
    def search(provider, query):
        return {"provider": provider, "outcome": "metadata_ready", "hits": [hit()]}
    def model(prompt):
        assert prompt["regions"] and "pairing mechanism" in prompt["regions"][0]["text"].lower()
        return Invocation(result=draft(prompt["regions"][0]["region_id"]), manifest=MANIFEST,
            input_tokens=150, output_tokens=70)
    req = request(store, "simple-html")
    result = simple_deep_research(store, req, context(), model=model, paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert calls == ["https://example.org/paper.html"]
    assert result.data["ingestions"][0]["status"] == "indexed"
    assert result.data["evidence"][0]["text"]
    assert result.data["review_graph"]["artifact"]["delta"]["add_edges"]
    assert store.records("ResearchPaperIngestion", corpus_id="papers", project_id="research")
    assert store.graph_revision("papers", "research") == req.graph_revision


@pytest.mark.parametrize("error", [ValueError("model transport failed"), CancelledError()])
def test_failed_and_cancelled_review_model_attempts_are_recorded(store, monkeypatch, error):
    item = region(store, "The source reports a pairing mechanism.")
    monkeypatch.setattr("nima_semantica.simple_deep_research._prepare_paper",
        lambda *args: {"candidate_id": args[3]["candidate_id"], "status": "indexed", "region_ids": [item.id]})
    def search(provider, query):
        return {"provider": provider, "outcome": "metadata_ready", "hits": [hit()]}
    def model(prompt):
        raise error
    req = request(store, "simple-model-" + type(error).__name__)
    if isinstance(error, CancelledError):
        with pytest.raises(CancelledError):
            simple_deep_research(store, req, context(), model=model, paper_search=search)
    else:
        result = simple_deep_research(store, req, context(), model=model, paper_search=search)
        assert result.status == "failed"
    rows = [r.content for _, r in store.records("ExecutionReceipt", corpus_id="papers", project_id="research")
        if r.content["stage"] == "deep_research_simple_model"]
    assert len(rows) == 1 and rows[0]["status"] == ("interrupted" if isinstance(error, CancelledError) else "failed")
    assert store.records("DeepResearchOutcome", corpus_id="papers", project_id="research")
