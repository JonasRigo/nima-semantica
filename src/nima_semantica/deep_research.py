"""Bounded, resumable deep-research helpers.

The helpers deliberately do not acquire documents or call a model.  A visible
Langflow model is responsible for extraction and synthesis; native arXiv HTTP
nodes supply metadata for query validation.  This module binds their inputs and
outputs to stable, source-scoped contracts.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, canonical
from .ontology_profiles import OntologyProfile, saved_profile


Digest = str


class DeepResearchInput(StrictModel):
    corpus_id: str = Field(default="replacement_preview", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str = Field(default="replacement_preview", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    question: str = Field(default="What claims, methods, and open evidence gaps occur in these prepared sources?", min_length=1, max_length=20_000)
    region_ids: tuple[Digest, ...] = Field(default=("prepared_region_id",), min_length=1, max_length=256)
    previous_state: dict | None = None
    profile: OntologyProfile = Field(default_factory=lambda: saved_profile("literature_evidence"))
    max_batches: int = Field(default=4, ge=1, le=4)
    max_batch_characters: int = Field(default=6_000, ge=1_000, le=6_000)

    @model_validator(mode="after")
    def unique_regions(self):
        if len(set(self.region_ids)) != len(self.region_ids):
            raise ValueError("duplicate research region identifiers")
        return self


class ResearchBatch(StrictModel):
    id: str
    region_id: str
    document_id: str | None = None
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=6_000)
    normalized_artifact: str | None = None

    @model_validator(mode="after")
    def offsets(self):
        if self.end - self.start != len(self.text):
            raise ValueError("batch offsets do not match text")
        return self


class QueryCandidate(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=2_000)
    evidence_gap: str = Field(min_length=1, max_length=2_000)


class ArxivHit(StrictModel):
    arxiv_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=1_000)
    abstract: str = Field(min_length=1, max_length=3_000)


class QueryAssessment(StrictModel):
    query: QueryCandidate
    hits: tuple[ArxivHit, ...] = Field(max_length=5)
    relevant_ids: tuple[str, ...] = Field(max_length=5)
    reasons: dict[str, str] = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def references_returned_hits(self):
        identifiers = {hit.arxiv_id for hit in self.hits}
        if len(identifiers) != len(self.hits) or len(set(self.relevant_ids)) != len(self.relevant_ids):
            raise ValueError("duplicate arXiv identifiers cannot count as independent hits")
        if not set(self.relevant_ids) <= identifiers:
            raise ValueError("relevance assessment refers to an unreturned arXiv result")
        if set(self.reasons) - identifiers:
            raise ValueError("relevance reason refers to an unreturned arXiv result")
        if any(not self.reasons.get(identifier, "").strip() for identifier in self.relevant_ids):
            raise ValueError("every relevant hit requires a reason")
        return self


class ResearchFinding(StrictModel):
    statement: str = Field(min_length=1, max_length=10_000)
    batch_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    status: Literal["attributed", "apparent_conflict", "gap"] = "attributed"


class DeepResearchSynthesis(StrictModel):
    summaries: dict[str, str] = Field(default_factory=dict, max_length=64)
    findings: tuple[ResearchFinding, ...] = Field(default=(), max_length=64)
    ontology_graph: dict = Field(default_factory=dict)
    search_queries: tuple[QueryCandidate, ...] = Field(default=(), max_length=3)
    unresolved: tuple[str, ...] = Field(default=(), max_length=64)
    mathematically_verified: Literal[False] = False


def _fingerprint(region, start, end):
    return hashlib.sha256(canonical({"region": region, "start": start, "end": end})).hexdigest()


def prepare_research_batches(payload: dict, regions: list[dict]) -> dict:
    """Split authenticated source regions and resume after already processed spans."""
    request = DeepResearchInput.model_validate(payload)
    supplied = {region.get("id"): region for region in regions}
    if set(request.region_ids) != set(supplied):
        raise ValueError("research source read differs from the requested region set")
    done = set((request.previous_state or {}).get("processed_batch_ids", ()))
    batches, pending = [], []
    for region_id in request.region_ids:
        region = supplied[region_id]
        text = region.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("prepared research region has no text")
        for start in range(0, len(text), request.max_batch_characters):
            end = min(len(text), start + request.max_batch_characters)
            identifier = _fingerprint(region_id, start, end)
            candidate = {"id": identifier, "region_id": region_id,
                         "document_id": region.get("document_id"), "start": start,
                         "end": end, "text": text[start:end],
                         "normalized_artifact": region.get("normalized_artifact")}
            if identifier in done:
                continue
            (batches if len(batches) < request.max_batches else pending).append(candidate)
    processed = sorted(done | {batch["id"] for batch in batches})
    state = {"schema_version": 1, "question_sha256": hashlib.sha256(request.question.encode()).hexdigest(),
             "profile_digest": request.profile.digest, "processed_batch_ids": processed,
             "pending_batch_count": len(pending), "source_region_ids": list(request.region_ids)}
    return {"request": request.model_dump(mode="json"), "batches": batches,
            "state": state, "truncated": bool(pending),
            "scope": "Prepared source text only. Batches preserve exact offsets; source coverage and extraction correctness remain unresolved."}


def evaluate_arxiv_query(payload: dict) -> dict:
    """Apply the fixed relevance rule to a model assessment of supplied metadata."""
    assessment = QueryAssessment.model_validate(payload)
    count, relevant = len(assessment.hits), len(assessment.relevant_ids)
    required = count if count < 3 else 3
    validated = count > 0 and relevant >= required
    return {"query": assessment.query.model_dump(mode="json"),
            "hits": [hit.model_dump(mode="json") for hit in assessment.hits],
            "relevant_ids": list(assessment.relevant_ids), "reasons": assessment.reasons,
            "required_relevant_hits": required, "validated": validated,
            "outcome": "validated" if validated else "needs_revision" if count else "no_results",
            "scope": "arXiv metadata validates query relevance only; it is not source evidence or a coverage claim."}


def select_research_query(payload: dict, index: int) -> dict:
    if type(index) is not int or not 0 <= index < 3:
        raise ValueError("research query index must be 0, 1, or 2")
    queries = DeepResearchSynthesis.model_validate(payload).search_queries
    if index >= len(queries):
        return {"index": index, "query": None, "outcome": "no_query_proposed"}
    return {"index": index, "query": queries[index].model_dump(mode="json"), "outcome": "query_proposed"}


def collect_arxiv_query_validation(synthesis: dict, validations: list[dict]) -> dict:
    """Replace unvalidated proposals with the exact results of metadata review."""
    value = DeepResearchSynthesis.model_validate(synthesis).model_dump(mode="json")
    rows = [row for row in validations if row.get("query")]
    validated = [row["query"] for row in rows if row.get("validated")]
    unresolved = [*value["unresolved"], *[
        "arXiv query validation: " + row["query"]["query"] + " (" + row["outcome"] + ")"
        for row in rows if not row.get("validated")
    ]]
    return {**value, "validated_search_queries": validated, "query_validations": rows,
            "unresolved": unresolved, "search_queries": [],
            "scope": "Only arXiv-validated query suggestions are returned for external discovery. Source synthesis remains limited to supplied prepared text."}


def validate_deep_research_synthesis(context: dict, output: dict) -> dict:
    """Bind model findings to the current extraction batches before handoff."""
    batches = {batch["id"] for batch in context.get("batches", ())}
    synthesis = DeepResearchSynthesis.model_validate(output)
    if any(not set(finding.batch_ids) <= batches for finding in synthesis.findings):
        raise ValueError("research finding cites a batch outside this extraction call")
    if set(synthesis.summaries) - batches:
        raise ValueError("research summary cites a batch outside this extraction call")
    return {**synthesis.model_dump(mode="json"), "state": context["state"],
            "processed_batches": len(batches), "pending_batch_count": context["state"]["pending_batch_count"],
            "source_correspondence_verified": False,
            "scope": "Model synthesis over supplied extraction batches only. Attributions bind to batches, not entailment or literature completeness."}


class ResearchQueryReview(StrictModel):
    index: int = Field(ge=0, le=2)
    relevant_ids: tuple[str, ...] = Field(max_length=5)
    reasons: dict[str, str] = Field(default_factory=dict, max_length=5)
    revised_query: str | None = Field(default=None, min_length=1, max_length=500)


class ResearchQueryReviews(StrictModel):
    assessments: tuple[ResearchQueryReview, ...] = Field(max_length=3)


def start_query_validation(synthesis: dict) -> dict:
    """Retain source state outside the model's writable assessment contract."""
    value = DeepResearchSynthesis.model_validate({key: synthesis[key] for key in DeepResearchSynthesis.model_fields if key in synthesis})
    return {"synthesis": synthesis, "round": 0, "queries": [
        {"index": index, "original_query": query.model_dump(mode="json"),
         "query": query.model_dump(mode="json"), "validated": False, "attempts": []}
        for index, query in enumerate(value.search_queries)]}


def apply_query_reviews(context: dict, output: dict) -> dict:
    """Bind reviews to observed metadata; revisions keep the original gap/purpose."""
    import copy
    value = copy.deepcopy(context)
    reviews = ResearchQueryReviews.model_validate(output).assessments
    expected = {row["index"] for row in value["queries"] if not row["validated"]}
    if len({review.index for review in reviews}) != len(reviews) or {review.index for review in reviews} != expected:
        raise ValueError("assessment indices must match exactly the pending queries")
    by_index = {review.index: review for review in reviews}
    for row in value["queries"]:
        if row["validated"]:
            continue
        review = by_index[row["index"]]
        metadata = row.pop("metadata")
        checked = evaluate_arxiv_query({"query": row["query"], "hits": metadata["hits"],
            "relevant_ids": review.relevant_ids, "reasons": review.reasons})
        if metadata["outcome"] == "validation_unavailable":
            if review.relevant_ids:
                raise ValueError("unavailable metadata cannot validate a query")
            checked.update(validated=False, outcome="validation_unavailable")
        checked["metadata_receipt"] = {key: item for key, item in metadata.items() if key != "hits"}
        row["attempts"].append(checked)
        row["validated"] = checked["validated"]
        if not checked["validated"] and value["round"] < 3 and metadata["outcome"] != "validation_unavailable":
            revised = review.revised_query
            previous = {attempt["query"]["query"].strip().casefold() for attempt in row["attempts"]}
            if not revised or revised.strip().casefold() in previous:
                raise ValueError("empty or irrelevant searches require a new query before the next round")
            row["query"] = {**row["query"], "query": revised.strip()}
    return value


def finish_query_validation(context: dict) -> dict:
    if context["round"] != 3:
        raise ValueError("all three visible validation rounds must finish")
    synthesis = dict(context["synthesis"])
    queries = context["queries"]
    if len(queries) != len(synthesis["search_queries"]):
        raise ValueError("missing query validation")
    accepted = []
    failures = []
    for row in queries:
        if not row["attempts"]:
            raise ValueError("query has no metadata validation attempt")
        last = row["attempts"][-1]
        if row["validated"]:
            if not evaluate_arxiv_query({key: last[key] for key in QueryAssessment.model_fields})["validated"]:
                raise ValueError("query validation evidence does not meet threshold")
            accepted.append(last["query"])
        else:
            failures.append("arXiv query validation unresolved: " + row["original_query"]["query"] + " (" + last["outcome"] + ")")
    return {**synthesis, "search_queries": accepted, "validated_search_queries": accepted,
        "query_validations": queries, "unresolved": [*synthesis["unresolved"], *failures],
        "query_validation_status": "complete" if not failures else "partial",
        "scope": "Source synthesis over supplied batches only. Returned search queries passed arXiv metadata relevance review; this does not verify scientific claims or literature completeness."}
