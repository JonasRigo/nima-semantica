"""Provisional fast-reading path stays outside corpus indexing and graph admission."""

import pytest

from nima_semantica.deep_research_passes import deep_research_passes
from nima_semantica.deep_research_contracts import ResearchQuestion
from nima_semantica.deep_research_fast import exact_fast_passage, fast_text, stage_fast_paper
from nima_semantica.models import AcquisitionPolicy, ConflictError
from nima_semantica.paper_discovery import preferred_full_text_urls
from nima_semantica.providers import Invocation
from nima_semantica.source_tools import AcquiredSourceInput, SourceToolContext, _source_bytes
from model_fixture import MANIFEST
from test_simple_deep_research import context, hit, request
from test_source_pipeline import store


HTML = ("<html><body><h1>Pauli propagation</h1><p>" +
    "The source describes propagation of Pauli operators through a quantum circuit. " * 12 +
    "</p></body></html>").encode()


def test_fast_html_text_and_exact_snapshot():
    text, method = fast_text(HTML, "paper.html")
    assert "Pauli operators" in text and method == "html-visible-text-v1"


def test_arxiv_versioned_html_precedes_pdf_and_is_the_retained_source(store, monkeypatch):
    candidate = {"candidate_id": "paper-pauli", "observations": [{"arxiv_id": "2511.21651"}],
        "full_text_urls": ["https://arxiv.org/pdf/2511.21651v3"]}
    assert preferred_full_text_urls(candidate) == (
        "https://arxiv.org/html/2511.21651v3", "https://arxiv.org/pdf/2511.21651v3")
    fetched = []
    def acquire(address, policy):
        fetched.append(address)
        return HTML, "2511.21651v3.html", {"content_type": "text/html"}
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", acquire)
    ctx = context(reading_mode="fast_provisional", allow_fast_read=True,
        acquisition=AcquisitionPolicy(enabled=True, domains=("arxiv.org",)))
    result = stage_fast_paper(store, request(store, "html-first"), ctx, candidate,
        "Pauli operator propagation")
    assert result["status"] == "provisional_read"
    assert result["method"] == "html-visible-text-v1"
    assert fetched == ["https://arxiv.org/html/2511.21651v3"]
    passage = exact_fast_passage(store, result["passage_ids"][0], corpus_id="papers", project_id="research")
    assert passage["url"] == fetched[0]


def test_unavailable_arxiv_html_is_recorded_before_fallback(store, monkeypatch):
    candidate = {"candidate_id": "paper-older", "observations": [{"arxiv_id": "0406196"}],
        "full_text_urls": ["https://arxiv.org/pdf/quant-ph/0406196v5",
            "https://example.org/paper.html"]}
    fetched = []
    def acquire(address, policy):
        fetched.append(address)
        if address.startswith("https://arxiv.org/"):
            raise ValueError("rendition unavailable")
        return HTML, "paper.html", {"content_type": "text/html"}
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", acquire)
    ctx = context(reading_mode="fast_provisional", allow_fast_read=True,
        acquisition=AcquisitionPolicy(enabled=True, domains=("arxiv.org", "example.org")))
    result = stage_fast_paper(store, request(store, "html-fallback"), ctx, candidate,
        "Pauli operator propagation")
    assert result["status"] == "provisional_read"
    assert fetched == ["https://arxiv.org/html/quant-ph/0406196v5",
        "https://arxiv.org/pdf/quant-ph/0406196v5", "https://example.org/paper.html"]
    assert [attempt["status"] for attempt in result["attempts"]] == ["failed", "failed"]


def test_fast_provisional_pass_and_continuation_without_corpus_publication(store, monkeypatch):
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire",
        lambda address, policy: (HTML, "paper.html", {"content_type": "text/html"}))
    def search(provider, phrase):
        return {"outcome": "metadata_ready", "hits": [hit() | {"title": "Pauli propagation",
            "abstract": "Pauli operators in quantum circuits"}]}
    stages = []
    def model(prompt):
        stages.append(prompt["stage"])
        if prompt["stage"] == "coverage_plan":
            output = {"facets": [{"facet_id": "core", "source_question_id": prompt["original_questions"][0]["question_id"],
                "question": "How does the source address Pauli operator propagation?"}]}
        elif prompt["stage"] == "relevance_screen":
            output = {"decisions": [{"candidate_id": row["candidate_id"], "relevant": True,
                "reason": "The abstract addresses Pauli operator propagation."} for row in prompt["candidates"]]}
        elif prompt["stage"] == "passage_relevance":
            output = {"relevant": True, "reason": "The passage describes propagation of Pauli operators.",
                "supporting_passage_ids": [prompt["passages"][0]["region_id"]]}
        elif prompt["stage"] == "regional_graph":
            passage = prompt["regions"][0]["region_id"]
            assert prompt["evidence_authority"] == "provisional_fast_read"
            output = {"nodes": [
                {"node_id": "paper", "node_type": "paper", "source_region_ids": [passage],
                    "properties": {"text": "Pauli propagation"}},
                {"node_id": "claim", "node_type": "claim", "source_region_ids": [passage],
                    "properties": {"text": "The paper studies Pauli propagation."}}],
                "edges": [{"edge_id": "assert", "relation": "asserts", "source_id": "paper",
                    "target_id": "claim", "source_region_ids": [passage]}],
                "summary": "The source reports a Pauli propagation method.", "gaps": []}
        else:
            claims = [node["node_id"] for node in prompt["graph"]["nodes"] if node["node_type"] == "claim"]
            output = {"coverage": [{"facet_id": "core", "status": "covered",
                "supporting_node_ids": claims[:1]}]}
        return Invocation(result=output, manifest=MANIFEST, input_tokens=50, output_tokens=30)
    ctx = context(reading_mode="fast_provisional", allow_fast_read=True,
        allow_source_ingestion=False, allow_pdf=True)
    req = request(store, "fast-review")
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "Pauli operator propagation", "purpose": "Find source"}]},
        ctx, model=model, paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert "answer" not in result.data
    assert result.data["uncovered_questions"] == []
    assert result.data["source_inventory"]["read_paper_count"] == 1
    assert result.data["source_inventory"]["graph_passage_count"] >= 1
    assert stages == ["coverage_plan", "relevance_screen", "passage_relevance", "regional_graph",
        "gap_assessment_initial", "gap_assessment_final"]
    assert result.data["graph_commit"]["status"] == "not_eligible_until_prepared"
    assert "project_graph_prepare_request" not in result.data
    assert not store.records("SourceRegion") and not store.records("Document")
    assert not store.records("ResearchPaperIngestion")
    passage_id = result.data["regional_passes"][0]["evidence_region_ids"][0]
    assert exact_fast_passage(store, passage_id, corpus_id="papers", project_id="research")["text"]
    staged = result.data["selected_source_staging"][0]["prepare_source_input"]
    assert _source_bytes(AcquiredSourceInput.model_validate(staged), store,
        SourceToolContext(corpus_id="papers", project_id="research")) == HTML
    with pytest.raises(ConflictError):
        _source_bytes(AcquiredSourceInput.model_validate(staged), store,
            SourceToolContext(corpus_id="papers", project_id="other"))
    with pytest.raises(ConflictError):
        _source_bytes(AcquiredSourceInput.model_validate(staged | {"document_id": "f" * 64}), store,
            SourceToolContext(corpus_id="papers", project_id="research"))
    assert store.graph_revision("papers", "research") == req.graph_revision
    graph_id = result.data["review_graph"]["artifact"]["envelope"]["artifact_id"]
    continued = deep_research_passes(store, {"research": request(store, "fast-continue").model_dump(mode="json"),
        "input_graph_artifact_id": graph_id, "focus_question": "Which quantum circuits?"},
        ctx, model=model, paper_search=search)
    assert continued.status == "partial", (continued.data.get("error"), continued.data.get("diagnostic"))
    assert continued.data["entry_mode"] == "continue_from_graph"


def test_fast_provisional_requires_permission_before_model_or_provider(store):
    called = []
    result = deep_research_passes(store, {"research": request(store, "fast-denied").model_dump(mode="json")},
        context(reading_mode="fast_provisional", allow_fast_read=False, allow_source_ingestion=False),
        model=lambda _: called.append("model"), paper_search=lambda *_: called.append("provider"))
    assert result.status == "failed" and called == []


def test_off_topic_metadata_is_rejected_before_acquisition_and_query_is_refined(store, monkeypatch):
    acquired = []
    def acquire(address, policy):
        acquired.append(address)
        return HTML, "paper.html", {"content_type": "text/html"}
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", acquire)
    markov = hit() | {"provider_id": "https://openalex.org/W-markov", "doi": "10.1234/markov",
        "title": "Truncation of Markov chains", "abstract": "Error bounds for a Markov chain approximation",
        "full_text_urls": ["https://example.org/markov.html"]}
    pauli = hit() | {"provider_id": "https://openalex.org/W-pauli", "doi": "10.1234/pauli",
        "title": "Pauli operator propagation in quantum circuits",
        "abstract": "Clifford circuit methods for propagating Pauli operators",
        "full_text_urls": ["https://example.org/pauli.html"]}
    queries = []
    def search(provider, phrase):
        queries.append(phrase)
        return {"outcome": "metadata_ready", "hits": [markov] if len(queries) == 1 else [pauli]}
    def model(prompt):
        stage = prompt["stage"]
        if stage == "coverage_plan":
            return Invocation(result={"facets": [{"facet_id": "core",
                "source_question_id": prompt["original_questions"][0]["question_id"],
                "question": "How does Pauli propagation work in circuits?"}]},
                manifest=MANIFEST, input_tokens=50, output_tokens=30)
        if stage == "relevance_screen":
            return Invocation(result={"decisions": [{"candidate_id": row["candidate_id"],
                "relevant": "Pauli" in row["observations"][0]["title"],
                "reason": "Pauli propagation is the target; Markov truncation is off-topic."}
                for row in prompt["candidates"]],
                "refinement": {"query": "Pauli operator Clifford propagation", "purpose": "Find operator propagation"}
                    if len(queries) == 1 else None}, manifest=MANIFEST, input_tokens=50, output_tokens=30)
        if stage == "passage_relevance":
            return Invocation(result={"relevant": True, "reason": "The passage addresses Pauli propagation.",
                "supporting_passage_ids": [prompt["passages"][0]["region_id"]]},
                manifest=MANIFEST, input_tokens=50, output_tokens=30)
        if stage == "regional_graph":
            passage = prompt["regions"][0]["region_id"]
            output = {"nodes": [
                {"node_id": "paper", "node_type": "paper", "source_region_ids": [passage]},
                {"node_id": "claim", "node_type": "claim", "source_region_ids": [passage],
                    "properties": {"text": "The source studies Pauli operator propagation."}}],
                "edges": [{"edge_id": "assert", "relation": "asserts", "source_id": "paper",
                    "target_id": "claim", "source_region_ids": [passage]}],
                "summary": "A source on Pauli propagation.", "gaps": []}
        else:
            output = {"coverage": [{"facet_id": "core", "status": "partial",
                "supporting_node_ids": [node["node_id"] for node in prompt["graph"]["nodes"]
                    if node["node_type"] == "claim"][:1],
                "remaining_question": "Which other sources establish broader field coverage?"}],
                "followup_queries": []}
        return Invocation(result=output, manifest=MANIFEST, input_tokens=50, output_tokens=30)
    req = request(store, "fast-refine").model_copy(update={"questions": (
        ResearchQuestion(question_id="q", question="How do Pauli operators propagate through quantum circuits?"),)})
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "Pauli propagation approximation", "purpose": "Find method"}]},
        context(reading_mode="fast_provisional", allow_fast_read=True, allow_source_ingestion=False),
        model=model, paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert "answer" not in result.data
    assert result.data["uncovered_questions"][0]["remaining_question"] == "Which other sources establish broader field coverage?"
    assert queries == ["Pauli propagation approximation", "Pauli operator Clifford propagation"]
    assert acquired == ["https://example.org/pauli.html"]
    screens = result.data["regional_passes"][0]["relevance_screens"]
    assert screens[0]["decisions"][0]["relevant"] is False
    assert result.data["regional_passes"][0]["status"] == "graph_proposed"
