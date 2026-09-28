"""Two-pass and graph-continuation controller checks without network or paid calls."""

import pytest
from pydantic import ValidationError

from nima_semantica.deep_research_passes import (CoveragePlan, GapAssessment, PassageRelevance, QueryPlan,
    RegionalDraft, RelevanceScreen, ShortQuery, _bind_paper_nodes, _model_step,
    _require_exact_citations, _run_query, _validate_candidate, deep_research_passes)
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.graph_extraction import GraphExtractionCandidate, GraphExtractionRequest
from nima_semantica.providers import Invocation
from model_fixture import MANIFEST
from test_deep_extraction import region
from test_simple_deep_research import context, hit, request
from test_source_pipeline import store


def _draft(region_id, suffix):
    return {"nodes": [
        {"node_id": "paper-" + suffix, "node_type": "paper", "source_region_ids": [region_id], "properties": {"text": "Paper " + suffix}},
        {"node_id": "claim-" + suffix, "node_type": "claim", "source_region_ids": [region_id], "properties": {"text": "Claim " + suffix}}],
        "edges": [{"edge_id": "assert-" + suffix, "relation": "asserts", "source_id": "paper-" + suffix,
            "target_id": "claim-" + suffix, "source_region_ids": [region_id]}],
        "summary": "Paper " + suffix + " reports a scoped claim.", "gaps": []}


def _facets(prompt):
    return {"facets": [{"facet_id": "core", "source_question_id": prompt["original_questions"][0]["question_id"],
        "question": "What evidence answers the original research question?"}]}


def _coverage(prompt, status="partial", *, followup=()):
    facet = prompt["coverage_facets"][0]
    claims = [node["node_id"] for node in prompt["graph"]["nodes"] if node["node_type"] == "claim"]
    return {"coverage": [{"facet_id": facet["facet_id"], "status": status,
        "supporting_node_ids": claims[:1] if status != "uncovered" else [],
        "remaining_question": None if status == "covered" else "Which source establishes the missing facet?"}],
        "followup_queries": list(followup)}


def _setup(store, monkeypatch):
    a = region(store, "Paper A reports a pairing mechanism but not its experimental discrimination.")
    b = region(store, "Paper B reports a discriminating observable under additional assumptions.")
    def search(provider, phrase):
        assert provider == "openalex"
        row = hit() | {"provider_id": "https://openalex.org/" + ("WA" if "mechanism" in phrase else "WB"),
            "doi": "10.1234/" + ("a" if "mechanism" in phrase else "b"),
            "title": "Paper A" if "mechanism" in phrase else "Paper B"}
        return {"provider": provider, "outcome": "metadata_ready", "hits": [row]}
    def prepared(_store, _request, _context, candidate, _pdf_normalizer):
        item = a if candidate["observations"][0]["title"] == "Paper A" else b
        return {"candidate_id": candidate["candidate_id"], "status": "indexed", "region_ids": [item.id]}
    monkeypatch.setattr("nima_semantica.deep_research_passes._prepare_paper", prepared)
    return a.id, b.id, search


def test_fresh_two_passes_publish_regions_then_consolidated_review(store, monkeypatch):
    a, b, search = _setup(store, monkeypatch)
    stages = []
    def model(prompt):
        stage = prompt["stage"]
        stages.append(stage)
        if stage == "coverage_plan":
            result = _facets(prompt)
        elif stage == "query_plan":
            result = {"queries": [{"query": "pairing mechanism", "purpose": "Find mechanism evidence"}]}
        elif stage == "regional_graph":
            result = _draft(prompt["regions"][0]["region_id"], "A" if "mechanism" in prompt["query"]["query"] else "B")
        elif stage == "gap_assessment_initial":
            assert len(prompt["graph"]["nodes"]) == 2
            assert prompt["search_outcomes"][0]["evidence_count"] == 1
            result = _coverage(prompt, followup=[{"query": "pairing discriminating observable",
                "purpose": "Close observable gap"}])
        else:
            assert stage == "gap_assessment_final" and len(prompt["graph"]["nodes"]) == 4
            result = _coverage(prompt)
        return Invocation(result=result, manifest=MANIFEST, input_tokens=80, output_tokens=40)
    req = request(store, "passes-fresh")
    result = deep_research_passes(store, {"research": req.model_dump(mode="json")},
        context(discovery_providers=("openalex",)), model=model, paper_search=search)
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert stages == ["coverage_plan", "query_plan", "regional_graph", "gap_assessment_initial",
        "regional_graph", "gap_assessment_final"]
    assert len(result.data["regional_passes"]) == 2
    assert "answer" not in result.data
    assert result.data["uncovered_questions"][0]["facet_id"] == "core"
    assert all(row["status"] == "graph_proposed" for row in result.data["regional_passes"])
    delta = result.data["review_graph"]["artifact"]["delta"]
    assert {node["node_type"] for node in delta["upsert_nodes"]} == {"paper", "claim", "review"}
    assert len([edge for edge in delta["add_edges"] if edge["relation"] == "reviews"]) == 2
    assert store.graph_revision("papers", "research") == req.graph_revision


def test_graph_continuation_uses_focus_question_and_skips_initial_search(store, monkeypatch):
    a, b, search = _setup(store, monkeypatch)
    base_req = request(store, "passes-base")
    def first_model(prompt):
        stage = prompt["stage"]
        if stage == "coverage_plan":
            output = _facets(prompt)
        elif stage == "regional_graph":
            output = _draft(a, "A")
        else:
            output = _coverage(prompt, "covered")
        return Invocation(result=output, manifest=MANIFEST, input_tokens=80, output_tokens=40)
    base = deep_research_passes(store, {"research": base_req.model_dump(mode="json"),
        "search_phrases": [{"query": "pairing mechanism", "purpose": "Mechanism"}]},
        context(discovery_providers=("openalex",)), model=first_model, paper_search=search)
    assert base.status == "partial", (base.data.get("error"), base.data.get("diagnostic"))
    artifact_id = base.data["review_graph"]["artifact"]["envelope"]["artifact_id"]
    stages = []
    def second_model(prompt):
        stage = prompt["stage"]
        stages.append(stage)
        if stage == "coverage_plan":
            output = _facets(prompt)
        elif stage == "gap_assessment_initial":
            assert prompt["focus_question"] == "Which observable discriminates the mechanism?"
            output = _coverage(prompt, followup=[{"query": "pairing discriminating observable",
                "purpose": "Observable"}])
        elif stage == "regional_graph":
            output = _draft(b, "B")
        else:
            output = _coverage(prompt)
        return Invocation(result=output, manifest=MANIFEST, input_tokens=80, output_tokens=40)
    continuation = deep_research_passes(store, {"research": request(store, "passes-continue").model_dump(mode="json"),
        "input_graph_artifact_id": artifact_id,
        "focus_question": "Which observable discriminates the mechanism?"},
        context(discovery_providers=("openalex",)), model=second_model, paper_search=search)
    assert continuation.status == "partial", (continuation.data.get("error"), continuation.data.get("diagnostic"))
    assert stages == ["coverage_plan", "gap_assessment_initial", "regional_graph", "gap_assessment_final"]
    assert continuation.data["entry_mode"] == "continue_from_graph"
    assert len(continuation.data["regional_passes"]) == 1
    assert store.graph_revision("papers", "research") == base_req.graph_revision


def test_foreign_graph_fails_before_provider_calls(store, monkeypatch):
    req = request(store, "passes-foreign")
    calls = []
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "input_graph_artifact_id": "f" * 64, "focus_question": "Check a missing facet"},
        context(discovery_providers=("openalex",)),
        model=lambda _: calls.append("model"), paper_search=lambda *_: calls.append("provider"))
    assert result.status == "failed" and result.data["error"] in ("NimaError", "ConflictError")
    assert calls == []


def test_no_exact_evidence_cannot_be_reported_as_answered(store):
    req = request(store, "passes-no-evidence")
    def model(prompt):
        if prompt["stage"] == "coverage_plan":
            output = _facets(prompt)
        elif prompt["stage"] == "gap_assessment_initial":
            output = {"coverage": [{"facet_id": "core", "status": "covered",
                "supporting_node_ids": ["not-in-graph"]}]}
        else:
            raise AssertionError("unexpected model stage")
        return Invocation(result=output, manifest=MANIFEST, input_tokens=80, output_tokens=40)
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "unknown exact paper", "purpose": "Seek support"}]},
        context(discovery_providers=("openalex",)), model=model,
        paper_search=lambda *_: {"outcome": "metadata_ready", "hits": []})
    assert result.status == "failed"
    assert result.data["error"] == "ConflictError"
    assert "outside the review graph" in result.data["diagnostic"]


def test_assessment_cannot_omit_a_harness_pinned_question(store):
    req = request(store, "passes-missing-facet")
    def model(prompt):
        assert prompt["stage"] == "gap_assessment_initial"
        return Invocation(result={"coverage": [{"facet_id": "method", "status": "uncovered",
            "remaining_question": "Which source establishes the proposed method?"}]},
            manifest=MANIFEST, input_tokens=40, output_tokens=20)
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "unknown exact paper", "purpose": "Seek support"}],
        "coverage_facets": [
            {"facet_id": "method", "source_question_id": "q", "question": "Which method is proposed?"},
            {"facet_id": "bound", "source_question_id": "q", "question": "What bound has been proved?"}]},
        context(discovery_providers=("openalex",)), model=model,
        paper_search=lambda *_: {"outcome": "metadata_ready", "hits": []})
    assert result.status == "failed"
    assert "every pinned facet" in result.data["diagnostic"]


def test_overloaded_query_is_repaired_once_with_immutable_failed_receipt(store):
    req = request(store, "passes-query-repair")
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        query = ("superconducting pairing mechanism interaction gap symmetry phonon spin fluctuations isotope tunneling"
            if len(prompts) == 1 else "superconducting pairing mechanisms")
        return Invocation(result={"queries": [{"query": query, "purpose": "Find source papers"}]},
            manifest=MANIFEST, input_tokens=40, output_tokens=20)
    attempts = []
    plan = _model_step(store, req, context(), model, attempts, "query_plan",
        {"stage": "query_plan", "task": "Plan short searches", "response_contract": QueryPlan.model_json_schema()}, QueryPlan)
    assert plan.queries[0].query == "superconducting pairing mechanisms"
    assert [row["status"] for row in attempts] == ["failed", "completed"]
    assert "validation_feedback" in prompts[1]


def test_exact_passage_typo_gets_one_receipted_correction(store):
    req = request(store, "passes-citation-repair")
    exact = "a" * 63 + "b"
    wrong = "a" * 64
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return Invocation(result={"relevant": True, "reason": "The exact passage addresses the question.",
            "supporting_passage_ids": [wrong if len(prompts) == 1 else exact]},
            manifest=MANIFEST, input_tokens=40, output_tokens=20)
    attempts = []
    verdict = _model_step(store, req, context(), model, attempts, "passage_relevance_test",
        {"stage": "passage_relevance", "response_contract": PassageRelevance.model_json_schema()},
        PassageRelevance, validator=lambda item: _require_exact_citations(item.supporting_passage_ids,
            {exact}, label="passage relevance", required=item.relevant))
    assert verdict.supporting_passage_ids == (exact,)
    assert [item["status"] for item in attempts] == ["failed", "completed"]
    assert wrong in prompts[1]["validation_feedback"] and exact in prompts[1]["validation_feedback"]
    receipt = ExecutionReceiptService(store).get(attempts[0]["receipt_id"], corpus_id="papers", project_id="research")
    assert receipt.status == "failed" and receipt.error == "CitationMismatch"


def test_regional_graph_typo_gets_one_receipted_correction(store):
    req = request(store, "passes-regional-citation-repair")
    exact = "a" * 63 + "b"
    wrong = "a" * 64
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return Invocation(result=_draft(wrong if len(prompts) == 1 else exact, "fixed"),
            manifest=MANIFEST, input_tokens=40, output_tokens=20)
    attempts = []
    def check(draft):
        for item in (*draft.nodes, *draft.edges):
            _require_exact_citations(item.source_region_ids, {exact}, label="regional graph citation")
    draft = _model_step(store, req, context(), model, attempts, "regional_0",
        {"stage": "regional_graph", "response_contract": RegionalDraft.model_json_schema()},
        RegionalDraft, validator=check)
    assert draft.nodes[0].source_region_ids == (exact,)
    assert [item["status"] for item in attempts] == ["failed", "completed"]
    assert wrong in prompts[1]["validation_feedback"] and exact in prompts[1]["validation_feedback"]


@pytest.mark.parametrize("invalid_stage", ["passage_relevance", "regional_graph"])
def test_persistent_citation_typo_skips_source_not_whole_query(store, monkeypatch, invalid_stage):
    req = request(store, "passes-skip-" + invalid_stage)
    exact = "a" * 63 + "b"
    wrong = "a" * 64
    candidate = hit() | {"provider_id": "https://openalex.org/W-citation-typo",
        "doi": "10.1234/citation-typo", "title": "Relevant source"}
    def stage(_store, _request, _context, selected, _phrase):
        return {"candidate_id": selected["candidate_id"], "status": "provisional_read", "passage_ids": [exact]}
    def evidence(_store, ingestions, _context):
        return [{"candidate_id": ingestions[0]["candidate_id"], "region_id": exact,
            "text": "This source discusses the research topic."}]
    monkeypatch.setattr("nima_semantica.deep_research_passes.stage_fast_paper", stage)
    monkeypatch.setattr("nima_semantica.deep_research_passes.fast_evidence", evidence)
    def model(prompt):
        stage = prompt["stage"]
        if stage == "relevance_screen":
            output = {"decisions": [{"candidate_id": row["candidate_id"], "relevant": True,
                "reason": "The source directly addresses the topic."} for row in prompt["candidates"]]}
        elif stage == "passage_relevance":
            output = {"relevant": True, "reason": "The supplied text addresses the topic.",
                "supporting_passage_ids": [wrong if invalid_stage == stage else exact]}
        else:
            assert stage == "regional_graph"
            output = _draft(wrong, "bad")
        return Invocation(result=output, manifest=MANIFEST, input_tokens=40, output_tokens=20)
    attempts = []
    row, graph = _run_query(store, req,
        context(discovery_providers=("openalex",), reading_mode="fast_provisional",
            allow_fast_read=True, allow_source_ingestion=False), model, attempts,
        ShortQuery(query="relevant source", purpose="Find source"), 0, arxiv_search=None,
        paper_search=lambda *_: {"outcome": "metadata_ready", "hits": [candidate]}, pdf_normalizer=None)
    assert graph is None
    assert row["status"] == ("no_relevant_source" if invalid_stage == "passage_relevance"
        else "regional_citation_unresolved")
    stage_prefix = "regional_" if invalid_stage == "regional_graph" else invalid_stage
    assert [item["status"] for item in attempts if item["kind"].startswith(stage_prefix)] == ["failed", "failed"]


@pytest.mark.parametrize("invalid_stage", ["passage_relevance", "regional_graph"])
def test_persistent_citation_typo_reaches_uncovered_question(store, monkeypatch, invalid_stage):
    req = request(store, "passes-unresolved-" + invalid_stage)
    exact = "a" * 63 + "b"
    wrong = "a" * 64
    candidate = hit() | {"provider_id": "https://openalex.org/W-unresolved-citation",
        "doi": "10.1234/unresolved-citation", "title": "Relevant source"}
    def stage(_store, _request, _context, selected, _phrase):
        return {"candidate_id": selected["candidate_id"], "status": "provisional_read", "passage_ids": [exact]}
    def evidence(_store, ingestions, _context):
        return [{"candidate_id": ingestions[0]["candidate_id"], "region_id": exact,
            "text": "This source discusses the research topic."}]
    monkeypatch.setattr("nima_semantica.deep_research_passes.stage_fast_paper", stage)
    monkeypatch.setattr("nima_semantica.deep_research_passes.fast_evidence", evidence)
    def model(prompt):
        stage_name = prompt["stage"]
        if stage_name == "relevance_screen":
            output = {"decisions": [{"candidate_id": row["candidate_id"], "relevant": True,
                "reason": "The source directly addresses the topic."} for row in prompt["candidates"]]}
        elif stage_name == "passage_relevance":
            output = {"relevant": True, "reason": "The supplied text addresses the topic.",
                "supporting_passage_ids": [wrong if invalid_stage == stage_name else exact]}
        elif stage_name == "regional_graph":
            output = _draft(wrong, "bad")
        else:
            assert stage_name in ("gap_assessment_initial", "gap_assessment_final")
            output = {"coverage": [{"facet_id": "core", "status": "uncovered",
                "remaining_question": "Which exact source supports the research question?"}]}
        return Invocation(result=output, manifest=MANIFEST, input_tokens=40, output_tokens=20)
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "relevant source", "purpose": "Find source"}],
        "coverage_facets": [{"facet_id": "core", "source_question_id": req.questions[0].question_id,
            "question": "Which exact source supports the research question?"}]},
        context(discovery_providers=("openalex",), reading_mode="fast_provisional",
            allow_fast_read=True, allow_source_ingestion=False), model=model,
        paper_search=lambda *_: {"outcome": "metadata_ready", "hits": [candidate]})
    assert result.status == "partial", (result.data.get("error"), result.data.get("diagnostic"))
    assert result.data["coverage_status"] == "unanswered"
    assert result.data["uncovered_questions"][0]["remaining_question"] == "Which exact source supports the research question?"
    assert result.data["regional_passes"][0]["status"] == ("no_relevant_source"
        if invalid_stage == "passage_relevance" else "regional_citation_unresolved")
    assert store.graph_revision("papers", "research") == req.graph_revision


def test_unneeded_refinement_cannot_discard_valid_relevance_decisions():
    raw = {"decisions": [{"candidate_id": "paper-one", "relevant": True,
        "reason": "This paper addresses the selected research question."}],
        "refinement": {"query": "one two three four five six seven eight nine",
            "purpose": "Find another paper"}}
    screen = RelevanceScreen.model_validate(raw)
    assert screen.decisions[0].relevant is True and screen.refinement is None
    assert raw["refinement"]["query"].endswith("nine")
    with pytest.raises(ValidationError):
        RelevanceScreen.model_validate({"decisions": [{"candidate_id": "paper-one",
            "relevant": False, "reason": "Off topic"}], "refinement": raw["refinement"]})


@pytest.mark.parametrize('repair', [True, False])
def test_regional_ontology_error_has_one_audited_correction(store, monkeypatch, repair):
    from nima_semantica.deep_research_passes import RegionalGraphMismatch, _validate_provisional
    monkeypatch.setattr('nima_semantica.deep_research_passes.exact_fast_passage', lambda *a, **k: None)
    req = request(store, 'ontology-correction-' + str(repair))
    prompts, attempts = [], []
    def model(prompt):
        prompts.append(prompt)
        draft = _draft('exact-passage', 'fixture')
        if len(prompts) == 1 or not repair:
            draft['edges'][0]['relation'] = 'about'  # paper -> claim is not an allowed about relation
        return Invocation(result=draft, manifest=MANIFEST, input_tokens=40, output_tokens=20)
    def validate(draft):
        try:
            _validate_provisional(store, GraphExtractionCandidate(nodes=draft.nodes, edges=draft.edges),
                ['exact-passage'], context())
        except ValueError as exc:
            raise RegionalGraphMismatch(str(exc)) from exc
    def run():
        return _model_step(store, req, context(), model, attempts, 'regional_0',
            {'stage':'regional_graph'}, RegionalDraft, validator=validate)
    if repair:
        assert run().edges[0].relation == 'asserts'
    else:
        with pytest.raises(RegionalGraphMismatch):
            run()
    assert len(prompts) == 2
    assert 'violates the review ontology' in prompts[1]['validation_feedback']
    assert [a['status'] for a in attempts] == ['failed', 'completed' if repair else 'failed']
    assert not store.records('ProvisionalReviewGraph')


def test_pdf_worker_preflight_fails_before_paid_model_or_provider(store):
    req = request(store, "passes-pdf-unready")
    calls = []
    def unready():
        calls.append("health")
        raise RuntimeError("PDF worker unavailable")
    result = deep_research_passes(store, {"research": req.model_dump(mode="json")},
        context(allow_pdf=True), model=lambda _: calls.append("model"),
        arxiv_search=lambda _: calls.append("provider"),
        pdf_normalizer=lambda _: ("text", [{"provenance": [{"page": 1}]}]), pdf_preflight=unready)
    assert result.status == "failed" and result.data["error"] == "RuntimeError"
    assert calls == ["health"]


def test_rate_limited_provider_is_not_retried_across_queries(store):
    req = request(store, "passes-provider-cooldown")
    calls = []
    def limited(provider, phrase):
        calls.append((provider, phrase))
        return {"outcome": "unavailable", "status_code": 429, "retry_after": "30", "hits": []}
    cooldown = {}
    for ordinal, phrase in enumerate(("pairing mechanisms", "pairing observables")):
        result, graph = _run_query(store, req, context(discovery_providers=("openalex",)), None, [],
            ShortQuery(query=phrase, purpose="Source search"), ordinal, arxiv_search=None,
            paper_search=limited, pdf_normalizer=None, provider_cooldown=cooldown)
        assert result["status"] == "no_exact_evidence" and graph is None
        assert result["providers"][0]["outcome"] == ("unavailable" if ordinal == 0 else "rate_limited_cooldown")
    assert calls == [("openalex", "pairing mechanisms")]


def test_regional_vocabulary_normalizes_only_closed_names():
    original = {"nodes": [
        {"node_id": "Paper-EXACT", "node_type": " Paper ", "source_region_ids": ["region-EXACT"],
            "properties": {"candidate_id": "Paper-EXACT", "text": "Case-Sensitive Title"}},
        {"node_id": "Claim-EXACT", "node_type": "CLAIM", "source_region_ids": ["region-EXACT"],
            "properties": {"text": "Claim text"}}],
        "edges": [{"edge_id": "Edge-EXACT", "relation": "AsSeRtS", "source_id": "Paper-EXACT",
            "target_id": "Claim-EXACT", "source_region_ids": ["region-EXACT"]}],
        "summary": "An attributed claim", "gaps": []}
    draft = RegionalDraft.model_validate(original)
    assert [node.node_type for node in draft.nodes] == ["paper", "claim"]
    assert draft.edges[0].relation == "asserts"
    assert draft.nodes[0].node_id == "Paper-EXACT"
    assert draft.nodes[0].properties["text"] == "Case-Sensitive Title"
    assert draft.edges[0].source_region_ids == ("region-EXACT",)
    assert original["nodes"][0]["node_type"] == " Paper "  # Raw model output stays auditable.
    assert GapAssessment.model_validate({"coverage": [{"facet_id": "core", "status": "uncovered",
        "remaining_question": "Which paper supplies the missing method?"}]}).status == "unanswered"
    with pytest.raises(ValidationError):
        GapAssessment.model_validate({"coverage": [{"facet_id": "core", "status": "uncovered",
            "remaining_question": "Which paper supplies the missing method?"}],
            "answer": "No full text was read."})
    candidate = GraphExtractionCandidate(nodes=draft.nodes, edges=draft.edges)
    with pytest.raises(Exception, match="exact-read"):
        _validate_candidate(candidate, {"different-region"})


def test_graph_and_coverage_counts_are_not_arbitrary_model_output_gates(store):
    region_id = "r" * 64
    nodes = [{"node_id": f"node-{i}", "node_type": "claim", "source_region_ids": [region_id],
        "properties": {"text": f"Distinct attributed claim {i}"}} for i in range(257)]
    edges = [{"edge_id": f"edge-{i}", "relation": "discusses", "source_id": "node-0",
        "target_id": f"node-{i % 257}", "source_region_ids": [region_id]} for i in range(513)]
    draft = RegionalDraft.model_validate({"nodes": nodes, "edges": edges,
        "summary": "Source-attributed proposal.", "gaps": [f"Gap {i}" for i in range(9)]})
    candidate = GraphExtractionCandidate(nodes=draft.nodes, edges=draft.edges, unresolved=draft.gaps)
    assert (len(candidate.nodes), len(candidate.edges), len(candidate.unresolved)) == (257, 513, 9)
    plan = CoveragePlan.model_validate({"facets": [{"facet_id": f"facet-{i}",
        "source_question_id": "q", "question": f"What evidence answers facet {i}?"} for i in range(9)]})
    assessment = GapAssessment.model_validate({"coverage": [{"facet_id": facet.facet_id,
        "status": "uncovered", "remaining_question": facet.question} for facet in plan.facets]})
    assert len(assessment.coverage) == 9
    native = GraphExtractionRequest(corpus_id="papers", project_id="research",
        graph_revision=store.graph_revision("papers", "research"), registry_revision="registry",
        ontology_profile="profile", source_region_ids=(region_id,), question="Research question",
        max_nodes=len(candidate.nodes), max_edges=len(candidate.edges))
    assert (native.max_nodes, native.max_edges) == (257, 513)


def test_invalid_regional_output_fails_precisely_without_none_fallthrough(store):
    req = request(store, "passes-invalid-regional")
    attempts = []
    def model(_prompt):
        return Invocation(result={"nodes": [{"node_id": "paper-a", "node_type": "Not a Type",
            "source_region_ids": ["region-a"]}], "summary": "A candidate"},
            manifest=MANIFEST, input_tokens=40, output_tokens=20)
    with pytest.raises(ValidationError, match="node_type"):
        _model_step(store, req, context(), model, attempts, "regional_0",
            {"stage": "regional_graph", "response_contract": RegionalDraft.model_json_schema()}, RegionalDraft)
    assert len(attempts) == 1 and attempts[0]["status"] == "failed"
    receipt = ExecutionReceiptService(store).get(attempts[0]["receipt_id"], corpus_id="papers", project_id="research")
    assert receipt is not None and receipt.status == "failed"


def test_paper_identity_is_derived_only_from_unique_exact_region_ownership():
    draft = RegionalDraft.model_validate({"nodes": [
        {"node_id": "paper-one", "node_type": "PAPER", "source_region_ids": ["region-one"]},
        {"node_id": "paper-two", "node_type": "Paper", "source_region_ids": ["region-two"]}],
        "summary": "Two separate papers"})
    prepared = {"candidate-one": {"region-one"}, "candidate-two": {"region-two"}}
    selected = [{"candidate_id": "candidate-one", "observations": [{"title": "One"}]},
        {"candidate_id": "candidate-two", "observations": [{"title": "Two"}]}]
    bound = _bind_paper_nodes(draft, prepared, selected)
    assert [(node.properties["candidate_id"], node.properties["text"]) for node in bound] == [
        ("candidate-one", "One"), ("candidate-two", "Two")]
    conflicting = draft.model_copy(update={"nodes": (draft.nodes[0].model_copy(update={
        "properties": {"candidate_id": "candidate-two"}}), draft.nodes[1])})
    with pytest.raises(Exception, match="exact prepared candidate"):
        _bind_paper_nodes(conflicting, prepared, selected)
    with pytest.raises(Exception, match="exact prepared candidate"):
        _bind_paper_nodes(draft, {"candidate-one": {"region-one", "region-two"},
            "candidate-two": {"region-one", "region-two"}}, selected)


def test_normalized_relation_still_obeys_ontology_domain(store, monkeypatch):
    region_id, _, search = _setup(store, monkeypatch)
    req = request(store, "passes-invalid-relation-domain")
    before = store.graph_revision("papers", "research")
    def model(prompt):
        if prompt["stage"] == "coverage_plan":
            return Invocation(result=_facets(prompt), manifest=MANIFEST, input_tokens=40, output_tokens=20)
        assert prompt["stage"] == "regional_graph"
        draft = _draft(region_id, "A")
        draft["edges"][0]["relation"] = "USES"  # Paper -> Claim is not an allowed uses edge.
        return Invocation(result=draft, manifest=MANIFEST, input_tokens=40, output_tokens=20)
    result = deep_research_passes(store, {"research": req.model_dump(mode="json"),
        "search_phrases": [{"query": "pairing mechanism", "purpose": "Find a source"}]},
        context(discovery_providers=("openalex",)), model=model, paper_search=search)
    assert result.status == "failed" and "ontology" in result.data["diagnostic"]
    assert store.graph_revision("papers", "research") == before
