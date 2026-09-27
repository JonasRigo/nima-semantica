"""Two bounded literature passes over source-attributed regional graph proposals.

The harness owns the question, scope, permissions and further iterations. This
controller permits one initial multi-query pass and one gap-directed pass, or
only the latter when an exact prior review-graph artifact is supplied.
"""

from asyncio import CancelledError
import json
import re
import time
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .artifact_service import ArtifactService
from .corpus_registry import CorpusRegistry
from .deep_research_contracts import DeepResearchRequest, ResearchQuestion
from .evidence_reader import ReadEvidenceContext, ReadEvidenceRequest, read_evidence
from .deep_research_fast import exact_fast_passage, fast_evidence, stage_fast_paper
from .execution_receipts import ExecutionReceiptService
from .graph_analysis import persist_graph_outcome
from .graph_extraction import ExtractedEdge, ExtractedNode, GraphExtractionCandidate
from .models import ConflictError, Record, StrictModel, canonical, identity
from .okf_contracts import OKFDelta
from .ontology_profiles import saved_profile
from .ontology_services import OntologyService
from .paper_discovery import merge_candidates
from .providers import Invocation, validate_manifest
from .receipts import ExecutionReceipt
from .research_run_service import ResearchRunService
from .simple_deep_research import (PROFILE, SimpleDeepResearchContext, _evidence,
    _prepare_paper, _publish_graph, _ranked, stock_arxiv_hits)
from .tool_contracts import ToolResult


VERSION = "deep-research-passes-v11"


class CitationMismatch(ConflictError):
    """A model-proposed evidence ID is not among the exact IDs supplied to it."""


class ShortQuery(StrictModel):
    query: str = Field(min_length=3, max_length=96)
    purpose: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def bounded_terms(self):
        if not 2 <= len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+.-]*", self.query)) <= 8:
            raise ValueError("search phrase requires two to eight terms")
        return self


class QueryPlan(StrictModel):
    queries: tuple[ShortQuery, ...] = Field(min_length=1, max_length=3)


class CoverageFacet(StrictModel):
    facet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    source_question_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=8)


class CoveragePlan(StrictModel):
    facets: tuple[CoverageFacet, ...] = Field(min_length=1)


class FacetCoverage(StrictModel):
    facet_id: str
    status: Literal["covered", "partial", "uncovered", "contested"]
    supporting_node_ids: tuple[str, ...] = ()
    remaining_question: str | None = Field(default=None, min_length=8)

    @model_validator(mode="after")
    def valid_coverage(self):
        if self.status == "covered" and (not self.supporting_node_ids or self.remaining_question):
            raise ValueError("covered facet requires graph support and no remaining question")
        if self.status != "covered" and not self.remaining_question:
            raise ValueError("uncovered or partial facet requires an exact remaining question")
        return self


class RelevanceDecision(StrictModel):
    candidate_id: str
    relevant: bool
    reason: str = Field(min_length=3)


class RelevanceScreen(StrictModel):
    decisions: tuple[RelevanceDecision, ...] = ()
    refinement: ShortQuery | None = None

    @model_validator(mode="before")
    @classmethod
    def discard_unneeded_refinement(cls, value):
        """The controller never uses a refinement once a relevant hit exists."""
        if (isinstance(value, dict) and isinstance(value.get("decisions"), (list, tuple))
            and any(isinstance(item, dict) and item.get("relevant") is True
                for item in value["decisions"])):
            return {**value, "refinement": None}
        return value


class PassageRelevance(StrictModel):
    relevant: bool
    reason: str = Field(min_length=3)
    supporting_passage_ids: tuple[str, ...] = ()


class RegionalDraft(StrictModel):
    nodes: tuple[ExtractedNode, ...] = Field(min_length=1)
    edges: tuple[ExtractedEdge, ...] = ()
    summary: str = Field(min_length=1)
    gaps: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_vocabulary(cls, value):
        """Canonicalize only model-proposed ontology names, never IDs or evidence."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if isinstance(value.get("nodes"), (list, tuple)):
            normalized["nodes"] = [
                {**node, "node_type": node["node_type"].strip().lower()}
                if isinstance(node, dict) and isinstance(node.get("node_type"), str) else node
                for node in value["nodes"]]
        if isinstance(value.get("edges"), (list, tuple)):
            normalized["edges"] = [
                {**edge, "relation": edge["relation"].strip().lower()}
                if isinstance(edge, dict) and isinstance(edge.get("relation"), str) else edge
                for edge in value["edges"]]
        return normalized


class GapAssessment(StrictModel):
    coverage: tuple[FacetCoverage, ...] = Field(min_length=1)
    followup_queries: tuple[ShortQuery, ...] = Field(default=(), max_length=2)

    @property
    def status(self):
        if all(item.status == "covered" for item in self.coverage):
            return "answered"
        if any(item.status in ("covered", "partial", "contested") for item in self.coverage):
            return "partial"
        return "unanswered"

    @property
    def gaps(self):
        return tuple(item.remaining_question for item in self.coverage if item.remaining_question)


class PassResearchRequest(StrictModel):
    research: DeepResearchRequest
    search_phrases: tuple[ShortQuery, ...] = Field(default=(), max_length=3)
    coverage_facets: tuple[CoverageFacet, ...] = ()
    input_graph_artifact_id: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    focus_question: str | None = Field(default=None, min_length=3, max_length=2000)

    @model_validator(mode="after")
    def valid_mode(self):
        if self.input_graph_artifact_id and self.search_phrases:
            raise ValueError("continuation starts with gap assessment, not an initial search plan")
        return self


def _model_step(store, request, context, model, attempts, stage, payload, result_type, *, validator=None):
    correctable = result_type in (QueryPlan, CoveragePlan, RelevanceScreen, GapAssessment) or validator is not None
    feedback = "The previous structured response failed the typed contract. Preserve the scientific question, return every required field and ID, and correct only the invalid structure. Search phrases must contain two to eight terms and at most 96 characters."
    for ordinal in range(2 if correctable else 1):
        current = payload if ordinal == 0 else {**payload,
            "validation_feedback": feedback}
        receipt_id = identity((VERSION, request.operation_id, stage, len(attempts)))
        invocation = None
        error = None
        status = "failed"
        retryable = False
        try:
            invocation = Invocation.model_validate(model(current))
            if invocation.manifest != context.model_manifest or invocation.error:
                raise ValueError("research model returned an invalid or mismatched envelope")
            try:
                result = result_type.model_validate(invocation.result)
            except ValidationError:
                retryable = ordinal == 0 and result_type in (QueryPlan, CoveragePlan, RelevanceScreen, GapAssessment)
                raise
            if validator is not None:
                validator(result)
            status = "completed"
            return result
        except (CancelledError, KeyboardInterrupt) as exc:
            status, error = "interrupted", type(exc).__name__
            raise
        except Exception as exc:
            error = type(exc).__name__
            if isinstance(exc, CitationMismatch):
                retryable = ordinal == 0
                feedback = str(exc)
            if not retryable:
                raise
        finally:
            attempts.append({"kind": stage, "receipt_id": receipt_id, "status": status})
            ExecutionReceiptService(store).record(ExecutionReceipt(receipt_id=receipt_id,
                operation_id=request.operation_id, stage="deep_research_passes_" + stage,
                corpus_id=context.corpus_id, project_id=context.project_id, run_id=request.run_id,
                graph_revision=request.graph_revision, status=status, error=error, tool_version=VERSION,
                metadata={"request_hash": identity(current), "input": current,
                    "output": invocation.model_dump(mode="json") if invocation else {}}))


def _require_exact_citations(cited, available, *, label, required=False):
    cited_ids = set(cited)
    available_ids = set(available)
    unknown = sorted(cited_ids - available_ids)
    if unknown or (required and not cited_ids):
        raise CitationMismatch(f"{label}: invalid IDs {unknown}; exact available IDs {sorted(available_ids)}. "
            "Cite only supplied IDs that substantively support the proposal; if none do, reject the source. "
            "Do not infer an ID from a near match.")


def _validate_candidate(candidate, allowed_regions):
    node_ids = {node.node_id for node in candidate.nodes}
    if len(node_ids) != len(candidate.nodes):
        raise ValueError("regional graph contains duplicate node identities")
    for item in (*candidate.nodes, *candidate.edges):
        if not set(item.source_region_ids) <= allowed_regions:
            raise ConflictError("regional graph cites evidence that was not exact-read")
    if any(edge.source_id not in node_ids or edge.target_id not in node_ids for edge in candidate.edges):
        raise ValueError("regional graph relation references an absent node")


def _provisional_profile():
    """Versioned review vocabulary with explicitly provisional evidence semantics."""
    return saved_profile(PROFILE).model_copy(update={"version": "2.0.0",
        "instructions": "The same literature review vocabulary applies to regional and consolidated graphs. Fast-read passages are immutable provisional evidence, not prepared corpus regions. No project-graph admission or certified scientific truth follows from this profile."})


def _validate_provisional(store, candidate, passage_ids, context):
    _validate_candidate(candidate, set(passage_ids))
    profile = _provisional_profile()
    types = {node.name.casefold() for node in profile.node_types}
    relations = {relation.name: relation for relation in profile.relation_types}
    nodes = {node.node_id: node for node in candidate.nodes}
    for node in candidate.nodes:
        if node.node_type not in types or not node.source_region_ids:
            raise ValueError(f"provisional node {node.node_id} lacks a valid type or passage")
    for edge in candidate.edges:
        rule = relations.get(edge.relation)
        if (rule is None or nodes[edge.source_id].node_type not in {name.casefold() for name in rule.source_types}
            or nodes[edge.target_id].node_type not in {name.casefold() for name in rule.target_types}
            or not edge.source_region_ids):
            raise ValueError(f"provisional relation {edge.edge_id}: {nodes[edge.source_id].node_type} "
                f"-{edge.relation}-> {nodes[edge.target_id].node_type} violates the review ontology")
    for passage_id in passage_ids:
        exact_fast_passage(store, passage_id, corpus_id=context.corpus_id, project_id=context.project_id)


def _publish_provisional(store, request, context, candidate, passage_ids):
    _validate_provisional(store, candidate, passage_ids, context)
    profile = _provisional_profile()
    body = {"schema_version": 1, "authority": "provisional_fast_read",
        "scientific_admission": False, "ontology_profile": profile.digest,
        "ontology_name": profile.name, "ontology_version": profile.version,
        "graph_revision": request.graph_revision.model_dump(mode="json"),
        "question_hash": identity(request.questions), "passage_ids": sorted(passage_ids),
        "candidate": candidate.model_dump(mode="json")}
    artifact_id = store.artifact(canonical(body))
    record_id = store.put(Record(kind="ProvisionalReviewGraph", corpus_id=context.corpus_id,
        project_id=context.project_id, content={"artifact_id": artifact_id,
            "question_hash": body["question_hash"], "ontology_profile": profile.digest,
            "graph_revision": body["graph_revision"], "passage_ids": body["passage_ids"]}))
    receipt_id = identity((VERSION, request.operation_id, "provisional_graph", artifact_id))
    ExecutionReceiptService(store).record(ExecutionReceipt(receipt_id=receipt_id,
        operation_id=request.operation_id, stage="deep_research_provisional_graph",
        corpus_id=context.corpus_id, project_id=context.project_id, run_id=request.run_id,
        graph_revision=request.graph_revision, status="completed", tool_version=VERSION,
        output_ids=(artifact_id, record_id), metadata={"request_hash": identity(body),
            "authority": "provisional_fast_read"}))
    return {"status": "provisional", "receipt_id": receipt_id,
        "artifact": {"envelope": {"artifact_id": artifact_id, "status": "provisional"},
            "candidate": body["candidate"]}, "ontology_profile": profile.digest}, None


def _bind_paper_nodes(draft, prepared_by_candidate, selected):
    """Derive omitted paper identity only from unique exact-region ownership."""
    sources = {candidate["candidate_id"]: candidate for candidate in selected}
    paper_nodes = []
    for node in draft.nodes:
        if node.node_type != "paper":
            paper_nodes.append(node)
            continue
        candidate_id = node.properties.get("candidate_id")
        if not candidate_id:
            owners = [identifier for identifier, region_ids in prepared_by_candidate.items()
                if set(node.source_region_ids) <= region_ids]
            if len(owners) == 1:
                candidate_id = owners[0]
        if (candidate_id not in prepared_by_candidate or candidate_id not in sources or
            not set(node.source_region_ids) <= prepared_by_candidate[candidate_id]):
            raise ConflictError("paper node is not bound to its exact prepared candidate")
        properties = {**node.properties, "candidate_id": candidate_id,
            "text": sources[candidate_id]["observations"][0]["title"]}
        paper_nodes.append(node.model_copy(update={"properties": properties}))
    if not any(node.node_type == "paper" for node in paper_nodes):
        raise ValueError("evidence-bearing regional graph requires an attributed paper node")
    return tuple(paper_nodes)


def _merge(regions, base=None):
    """Deterministically consolidate exact-attributed region graphs without LLM identity guesses."""
    nodes = {}
    edges = {}
    shared = {}
    for ordinal, candidate in enumerate(([base] if base else []) + list(regions)):
        if candidate is None:
            continue
        renamed = {}
        for node in candidate.nodes:
            text = node.properties.get("text")
            paper_identity = node.properties.get("candidate_id") if node.node_type == "paper" else None
            key = ("paper", paper_identity) if isinstance(paper_identity, str) and paper_identity else (
                (node.node_type, text.strip().casefold()) if node.node_type in ("entity", "method") and isinstance(text, str) and text.strip() else None)
            identifier = shared.get(key) if key else None
            if identifier is None:
                identifier = "merged-" + identity((ordinal, node.node_id, node.node_type))[:24]
                if key:
                    shared[key] = identifier
                nodes[identifier] = ExtractedNode(node_id=identifier, node_type=node.node_type,
                    source_region_ids=node.source_region_ids, properties=node.properties)
            else:
                old = nodes[identifier]
                nodes[identifier] = old.model_copy(update={"source_region_ids": tuple(sorted(set((*old.source_region_ids, *node.source_region_ids))))})
            renamed[node.node_id] = identifier
        for edge in candidate.edges:
            source_id, target_id = renamed[edge.source_id], renamed[edge.target_id]
            key = (edge.relation, source_id, target_id)
            if key in edges:
                old = edges[key]
                edges[key] = old.model_copy(update={"source_region_ids": tuple(sorted(set((*old.source_region_ids, *edge.source_region_ids))))})
            else:
                edges[key] = ExtractedEdge(edge_id="merged-" + identity((ordinal, edge.edge_id, key))[:24],
                    relation=edge.relation, source_id=source_id, target_id=target_id,
                    source_region_ids=edge.source_region_ids, properties=edge.properties)
    return GraphExtractionCandidate(nodes=tuple(nodes.values()), edges=tuple(edges.values()))


def _load_base(store, artifact_id, request, context):
    if context.reading_mode == "fast_provisional":
        matches = [record for _, record in store.records("ProvisionalReviewGraph", corpus_id=context.corpus_id)
            if record.project_id == context.project_id and record.content.get("artifact_id") == artifact_id]
        if len(matches) != 1:
            raise ConflictError("supplied provisional graph is missing or outside scope")
        body = json.loads(store.read_artifact(artifact_id))
        record = matches[0]
        if (body.get("authority") != "provisional_fast_read"
            or body.get("ontology_profile") != _provisional_profile().digest
            or body.get("ontology_profile") != record.content.get("ontology_profile")
            or body.get("question_hash") != identity(request.questions)
            or body.get("question_hash") != record.content.get("question_hash")
            or body.get("graph_revision") != request.graph_revision.model_dump(mode="json")
            or body.get("graph_revision") != record.content.get("graph_revision")
            or body.get("passage_ids") != record.content.get("passage_ids")):
            raise ConflictError("supplied provisional graph binding differs")
        candidate = GraphExtractionCandidate.model_validate(body["candidate"])
        candidate = GraphExtractionCandidate(nodes=tuple(node for node in candidate.nodes
            if node.node_type != "review"), edges=tuple(edge for edge in candidate.edges
            if edge.source_id in {node.node_id for node in candidate.nodes if node.node_type != "review"}
            and edge.target_id in {node.node_id for node in candidate.nodes if node.node_type != "review"}))
        _validate_provisional(store, candidate, body["passage_ids"], context)
        return candidate
    envelope, raw = ArtifactService(store).read(artifact_id, corpus_id=context.corpus_id, project_id=context.project_id)
    if envelope.project_id != context.project_id or envelope.artifact_kind != "graph_candidate" or envelope.status != "proposed":
        raise ConflictError("supplied graph is not a scoped review proposal")
    if envelope.content.get("model_metadata", {}).get("question_hash") != identity(request.questions):
        raise ConflictError("supplied graph belongs to a different research question")
    if envelope.content.get("graph_revision") != request.graph_revision.model_dump(mode="json"):
        raise ConflictError("supplied graph belongs to a different project graph revision")
    profile = OntologyService.from_store(store, corpus_id=context.corpus_id, project_id=context.project_id).resolve(PROFILE)
    delta = OKFDelta.model_validate_json(raw)
    if delta.ontology_profile != profile.digest or delta.base_revision != request.graph_revision:
        raise ConflictError("supplied graph ontology or revision differs")
    nodes = tuple(ExtractedNode(node_id=node.node_id, node_type=node.node_type,
        source_region_ids=tuple(ref.region_id for ref in node.evidence if ref.region_id),
        properties=node.properties) for node in delta.upsert_nodes if node.node_type != "review")
    node_ids = {node.node_id for node in nodes}
    edges = tuple(ExtractedEdge(edge_id=edge.edge_id, relation=edge.relation,
        source_id=edge.source_id.local_id, target_id=edge.target_id.local_id,
        source_region_ids=tuple(ref.region_id for ref in edge.evidence if ref.region_id),
        properties=edge.properties) for edge in delta.add_edges
        if edge.source_id.local_id in node_ids and edge.target_id.local_id in node_ids)
    if len(nodes) > 64 or len(edges) > 128:
        raise ValueError("supplied graph exceeds continuation bounds")
    candidate = GraphExtractionCandidate(nodes=nodes, edges=edges)
    region_ids = set(envelope.provenance)
    _validate_candidate(candidate, region_ids)
    for region_id in region_ids:
        read = read_evidence(store, ReadEvidenceRequest(region_id=region_id, max_bytes=100_000),
            ReadEvidenceContext(corpus_id=context.corpus_id, project_id=context.project_id))
        if read.status != "complete" or not read.data.get("exact_source_checked"):
            raise ConflictError("supplied graph has unavailable source evidence")
    return candidate


def _graph_digest(candidate):
    return {"nodes": [{"node_id": node.node_id, "node_type": node.node_type,
        "properties": node.properties, "source_region_ids": node.source_region_ids} for node in candidate.nodes],
        "edges": [{"relation": edge.relation, "source_id": edge.source_id, "target_id": edge.target_id,
            "source_region_ids": edge.source_region_ids} for edge in candidate.edges]}


def _coverage_plan(store, request, context, model, attempts, packet, focus):
    if packet.coverage_facets:
        plan = CoveragePlan(facets=packet.coverage_facets)
    else:
        plan = _model_step(store, request, context, model, attempts, "coverage_plan",
            {"stage": "coverage_plan", "original_questions": [q.model_dump(mode="json") for q in request.questions],
                "focus_question": focus,
                "task": "Decompose the original question and focus into the atomic, answerable research questions needed for coverage. Assign stable short facet IDs. Every original question ID must have at least one facet. These facets are the pinned coverage checklist; do not answer them or claim source support yet.",
                "response_contract": CoveragePlan.model_json_schema()}, CoveragePlan)
    facet_ids = [item.facet_id for item in plan.facets]
    source_ids = {item.question_id for item in request.questions}
    if (len(facet_ids) != len(set(facet_ids)) or
        {item.source_question_id for item in plan.facets} != source_ids):
        raise ConflictError("coverage plan needs unique facet IDs and every original question ID")
    return plan


def _source_inventory(graph, search_history, context):
    passages = {region_id for node in graph.nodes for region_id in node.source_region_ids}
    read_candidates = {item["candidate_id"] for row in search_history for item in row.get("ingestions", ())
        if item.get("status") in ("provisional_read", "indexed", "reused")}
    read_candidates.update(node.properties.get("candidate_id") for node in graph.nodes
        if node.node_type == "paper" and node.properties.get("candidate_id"))
    failed_acquisitions = sum(1 for row in search_history for item in row.get("ingestions", ())
        for attempt in item.get("attempts", ()) if attempt.get("status") not in ("completed", "indexed", "reused"))
    return {"read_paper_count": len(read_candidates), "graph_passage_count": len(passages),
        "failed_acquisition_attempt_count": failed_acquisitions,
        "evidence_authority": "provisional_fast_read" if context.reading_mode == "fast_provisional"
            else "exact_prepared_region"}


def _assessment(store, request, context, model, attempts, graph, focus, stage, coverage_plan, search_history=()):
    search_outcomes = [{"query": row["query"]["query"], "status": row["status"],
        "candidate_count": row["candidate_count"], "evidence_count": len(row["evidence_region_ids"]),
        "providers": [{"provider": item["provider"], "outcome": item["outcome"],
            "hit_count": item["hit_count"], "status_code": item.get("status_code")}
            for item in row["providers"]]} for row in search_history]
    payload = {"stage": stage, "original_questions": [q.model_dump(mode="json") for q in request.questions],
        "focus_question": focus, "coverage_facets": [item.model_dump(mode="json") for item in coverage_plan.facets],
        "graph": _graph_digest(graph), "source_inventory": _source_inventory(graph, search_history, context),
        "search_outcomes": search_outcomes,
        "task": "For every pinned facet ID, return exactly one coverage item. A covered facet needs source-attributed claim node IDs in this graph. For partial, uncovered or contested facets, give one specific unanswered question; this means missing from the CURRENT GRAPH, never absent from the literature. Do not write a narrative answer, summarize acquisition, or invent source-read facts. Follow-up queries, if needed, must be broad distinct phrases of two to eight terms and at most 96 characters.",
        "response_contract": GapAssessment.model_json_schema()}
    assessment = _model_step(store, request, context, model, attempts, stage, payload, GapAssessment)
    expected = {item.facet_id for item in coverage_plan.facets}
    observed = [item.facet_id for item in assessment.coverage]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        raise ConflictError("coverage assessment must decide every pinned facet exactly once")
    claims = {node.node_id for node in graph.nodes if node.node_type == "claim" and node.source_region_ids}
    nodes = {node.node_id for node in graph.nodes}
    for item in assessment.coverage:
        if not set(item.supporting_node_ids) <= nodes:
            raise ConflictError("coverage assessment references a node outside the review graph")
        if item.status == "covered" and not set(item.supporting_node_ids) & claims:
            raise ConflictError("covered facet requires a source-attributed claim in this graph")
    if assessment.status == "answered" and assessment.followup_queries:
        raise ConflictError("answered coverage cannot request a gap search")
    return assessment


def _run_query(store, request, context, model, attempts, query, ordinal, *, arxiv_search, paper_search,
    pdf_normalizer, provider_cooldown=None):
    from .paper_discovery import search_papers
    phrase = query.query.strip()
    scope = {"corpus_id": context.corpus_id, "project_id": context.project_id}
    providers = []
    candidates = {}
    screens = []
    selected = []
    tried_phrases = set()
    for search_round in range(2):
        if phrase.casefold() in tried_phrases:
            break
        tried_phrases.add(phrase.casefold())
        for provider in context.discovery_providers:
            if provider == "arxiv":
                packet = arxiv_search(phrase) if arxiv_search else {"outcome": "unavailable", "rows": [], "errors": ["arXiv capability unavailable"]}
                hits, errors = stock_arxiv_hits(packet.get("rows", ()), packet.get("html_pages", ()))
                errors.extend(str(error)[:500] for error in packet.get("errors", ()))
                providers.append({"provider": provider, "query": phrase, "outcome": "metadata_ready" if hits else "unavailable",
                    "source": packet.get("backend", "stock_langflow_arxiv"), "errors": errors, "hit_count": len(hits)})
                candidates = merge_candidates(candidates, hits)
            else:
                if provider_cooldown is not None and time.monotonic() < provider_cooldown.get(provider, 0):
                    providers.append({"provider": provider, "query": phrase, "outcome": "rate_limited_cooldown",
                        "hit_count": 0})
                    continue
                packet = (paper_search or search_papers)(provider, phrase)
                providers.append({"provider": provider, "query": phrase, "outcome": packet.get("outcome"),
                    "status_code": packet.get("status_code"), "error": packet.get("error"),
                    "retry_after": packet.get("retry_after"),
                    "hit_count": len(packet.get("hits", ()))})
                if packet.get("status_code") == 429 and provider_cooldown is not None:
                    raw_delay = str(packet.get("retry_after") or "")
                    delay = min(max(int(raw_delay), 1), 60) if raw_delay.isdecimal() else 10
                    provider_cooldown[provider] = time.monotonic() + delay
                if packet.get("outcome") == "metadata_ready":
                    candidates = merge_candidates(candidates, packet.get("hits", ()))
        shortlist = _ranked(candidates, phrase, 12)
        if context.reading_mode != "fast_provisional":
            selected = shortlist[:min(context.max_papers, 2)]
            break
        screening = _model_step(store, request, context, model, attempts,
            f"relevance_screen_{ordinal}_{search_round}",
            {"stage": "relevance_screen", "focus_question": " ".join(q.question for q in request.questions),
                "query": phrase, "purpose": query.purpose,
                "candidates": [{"candidate_id": row["candidate_id"], "observations": row["observations"],
                    "full_text_available": bool(row["full_text_urls"])} for row in shortlist],
                "task": "Screen every candidate for substantive relevance to the research question, not keyword overlap. Metadata is only a discovery signal, never scientific evidence. Reject off-topic or ambiguous hits. If none are relevant, propose one distinct short refinement targeting the missing subject or mechanism; do not search for a whole calculation or assume every hit is useful.",
                "response_contract": RelevanceScreen.model_json_schema()}, RelevanceScreen)
        expected = {row["candidate_id"] for row in shortlist}
        observed = [decision.candidate_id for decision in screening.decisions]
        if len(observed) != len(set(observed)) or set(observed) != expected:
            raise ConflictError("relevance screen must decide every shortlisted candidate exactly once")
        decisions = {decision.candidate_id: decision for decision in screening.decisions}
        titles = {row["candidate_id"]: row["observations"][0]["title"] for row in shortlist}
        screens.append({"query": phrase, "decisions": [{**decision.model_dump(mode="json"),
            "title": titles[decision.candidate_id]} for decision in screening.decisions],
            "refinement": screening.refinement.model_dump(mode="json") if screening.refinement else None})
        selected = [row for row in shortlist if decisions[row["candidate_id"]].relevant][:min(context.max_papers, 2)]
        if selected or search_round or screening.refinement is None:
            break
        phrase = screening.refinement.query.strip()
    ingestions = []
    provisional = context.reading_mode == "fast_provisional"
    if provisional and context.allow_fast_read and context.acquisition.enabled:
        for candidate in selected:
            ingestions.append(stage_fast_paper(store, request, context, candidate, phrase))
    elif not provisional and context.allow_source_ingestion and context.acquisition.enabled:
        for candidate in selected:
            ingestions.append(_prepare_paper(store, request, context, candidate, pdf_normalizer))
    evidence_request = request.model_copy(update={"questions": (ResearchQuestion(question_id="regional", question=phrase),)})
    evidence = fast_evidence(store, ingestions, context) if provisional else _evidence(store, evidence_request, context, ingestions)
    passage_screen = []
    confirmed = []
    for candidate in selected if provisional else ():
        passages = [row for row in evidence if row["candidate_id"] == candidate["candidate_id"]]
        if not passages:
            continue
        available = {row["region_id"] for row in passages}
        try:
            verdict = _model_step(store, request, context, model, attempts,
                f"passage_relevance_{ordinal}_{candidate['candidate_id'][:12]}",
                {"stage": "passage_relevance", "focus_question": " ".join(q.question for q in request.questions),
                    "query": phrase, "candidate_id": candidate["candidate_id"],
                    "passages": passages,
                    "task": "Decide whether these exact passages substantively address the research question. Cite the passage IDs that demonstrate relevance; a title or abstract alone is insufficient. Reject an off-topic paper even if metadata looked promising. Do not yet infer scientific conclusions.",
                    "response_contract": PassageRelevance.model_json_schema()}, PassageRelevance,
                validator=lambda item: _require_exact_citations(item.supporting_passage_ids, available,
                    label="passage relevance", required=item.relevant))
        except CitationMismatch as exc:
            passage_screen.append({"candidate_id": candidate["candidate_id"],
                "status": "citation_unresolved", "relevant": False, "supporting_passage_ids": [],
                "reason": "Exact passage support was not established after correction.", "diagnostic": str(exc)})
            continue
        passage_screen.append({"candidate_id": candidate["candidate_id"], **verdict.model_dump(mode="json")})
        if verdict.relevant:
            confirmed.append(candidate)
    if provisional:
        selected = confirmed
        selected_ids = {candidate["candidate_id"] for candidate in selected}
        evidence = [row for row in evidence if row["candidate_id"] in selected_ids]
    result = {"query": query.model_dump(mode="json"), "providers": providers,
        "candidate_count": len(candidates), "selected_candidate_ids": [candidate["candidate_id"] for candidate in selected],
        "selected_papers": [{"candidate_id": candidate["candidate_id"],
            "title": candidate["observations"][0]["title"]} for candidate in selected],
        "relevance_screens": screens, "passage_relevance": passage_screen,
        "ingestions": ingestions, "evidence_region_ids": [row["region_id"] for row in evidence]}
    if not evidence:
        result["status"] = ("no_relevant_source" if provisional and not selected else
            "no_provisional_evidence" if provisional else "no_exact_evidence")
        return result, None
    payload = {"stage": "regional_graph", "query": query.model_dump(mode="json"),
        "original_questions": [q.model_dump(mode="json") for q in request.questions],
        "papers": [{"candidate_id": candidate["candidate_id"], "observations": candidate["observations"]} for candidate in selected],
        "regions": evidence, "ontology": (_provisional_profile() if provisional else OntologyService.from_store(store, **scope).resolve(PROFILE)).model_dump(mode="json"),
        "evidence_authority": "provisional_fast_read" if provisional else "exact_prepared_region",
        "task": "Represent only what supplied source passages report. Include paper and attributed claim/entity/method nodes, with atomic passage-cited relations. Do not create a review node or certify scientific truth. Fast-read passages are provisional and cannot authorize a project-graph commit.",
        "response_contract": RegionalDraft.model_json_schema()}
    allowed = {row["region_id"] for row in evidence}
    def validate_regional_citations(draft):
        for item in (*draft.nodes, *draft.edges):
            _require_exact_citations(item.source_region_ids, allowed, label="regional graph citation")
    try:
        draft = _model_step(store, request, context, model, attempts, "regional_" + str(ordinal),
            payload, RegionalDraft, validator=validate_regional_citations)
    except CitationMismatch as exc:
        result.update(status="regional_citation_unresolved", diagnostic=str(exc))
        return result, None
    prepared_by_candidate = {item["candidate_id"]: set(item.get("passage_ids" if provisional else "region_ids", ())) for item in ingestions
        if item.get("status") in (("provisional_read",) if provisional else ("indexed", "reused"))}
    paper_nodes = _bind_paper_nodes(draft, prepared_by_candidate, selected)
    candidate = GraphExtractionCandidate(nodes=paper_nodes, edges=draft.edges,
        unresolved=draft.gaps, model_metadata={"query": phrase, "question_hash": identity(request.questions), "authority": "source_attributed_proposal"})
    _validate_candidate(candidate, allowed)
    regional_request = request.model_copy(update={"operation_id": identity((request.operation_id, "regional", ordinal))})
    graph, _ = (_publish_provisional(store, regional_request, context, candidate, sorted(allowed))
        if provisional else _publish_graph(store, regional_request, context, candidate, sorted(allowed)))
    receipt_id = graph["receipt_id"] if provisional else graph.receipt_id
    attempts.append({"kind": "regional_graph", "receipt_id": receipt_id,
        "status": "provisional" if provisional else graph.status})
    result.update(status="graph_proposed", summary=draft.summary,
        regional_graph_artifact_id=graph["artifact"]["envelope"]["artifact_id"] if provisional else graph.artifact.envelope.artifact_id)
    return result, candidate


def deep_research_passes(store, packet, context, *, model=None, arxiv_search=None, paper_search=None,
    pdf_normalizer=None, pdf_preflight=None):
    packet = PassResearchRequest.model_validate(packet)
    request = packet.research
    context = SimpleDeepResearchContext.model_validate(context)
    if request.mode == "preview":
        return ToolResult(operation="Deep Research", status="complete", data={"executed": False, "version": VERSION,
            "entry_mode": "continue_from_graph" if packet.input_graph_artifact_id else "fresh"})
    if store is None or not context.allow_audit_writes:
        return ToolResult(operation="Deep Research", status="failed", diagnostics=({"code": "audit_or_store_unavailable"},))
    scope = {"corpus_id": context.corpus_id, "project_id": context.project_id}
    receipts = ExecutionReceiptService(store)
    receipt_id = identity((VERSION, request.operation_id, scope))
    fingerprint = identity({"packet": packet, "context": context, "version": VERSION})
    previous = receipts.replay(receipt_id, request_hash=fingerprint, **scope)
    if previous:
        return ToolResult.model_validate(previous.metadata["result"])
    attempts = []
    data = {"version": VERSION, "entry_mode": "continue_from_graph" if packet.input_graph_artifact_id else "fresh",
        "focus_question": packet.focus_question or " ".join(q.question for q in request.questions),
        "regional_passes": [], "assessments": [], "attempts": attempts, "scientific_admission": False}
    status = "partial"
    interrupted = None
    try:
        if not CorpusRegistry(store).corpus(context.corpus_id):
            raise ConflictError("registered corpus required")
        if store.graph_revision(**scope) != request.graph_revision:
            raise ConflictError("stale or foreign project graph revision")
        if request.run_id and ResearchRunService(store).get_run(request.run_id, **scope) is None:
            raise ConflictError("run outside scope")
        if not context.allow_model_calls or model is None or context.model_manifest is None:
            raise ValueError("operator-configured model required")
        validate_manifest(context.model_manifest)
        if context.reading_mode == "fast_provisional" and not context.allow_fast_read:
            raise ValueError("fast provisional reading requires explicit permission")
        if context.allow_pdf and context.reading_mode == "prepared":
            if pdf_normalizer is None or pdf_preflight is None:
                raise ValueError("authorized PDF worker and readiness check are required")
            pdf_preflight()
        base = _load_base(store, packet.input_graph_artifact_id, request, context) if packet.input_graph_artifact_id else None
        coverage_plan = _coverage_plan(store, request, context, model, attempts, packet, data["focus_question"])
        data["coverage_facets"] = [item.model_dump(mode="json") for item in coverage_plan.facets]
        regional = []
        provider_cooldown = {}
        if base is None:
            if packet.search_phrases:
                plan = QueryPlan(queries=packet.search_phrases)
            else:
                plan = _model_step(store, request, context, model, attempts, "query_plan",
                    {"stage": "query_plan", "questions": [q.model_dump(mode="json") for q in request.questions],
                        "focus_question": data["focus_question"],
                        "task": "Produce 1-3 distinct literature search phrases, each two to eight terms and at most 96 characters. Search one facet per phrase; do not pass the full research instruction to a provider.",
                        "response_contract": QueryPlan.model_json_schema()}, QueryPlan)
            for query in plan.queries:
                result, candidate = _run_query(store, request, context, model, attempts, query,
                    len(data["regional_passes"]), arxiv_search=arxiv_search, paper_search=paper_search,
                    pdf_normalizer=pdf_normalizer, provider_cooldown=provider_cooldown)
                data["regional_passes"].append(result)
                if candidate:
                    regional.append(candidate)
        combined = _merge(regional, base)
        first = _assessment(store, request, context, model, attempts, combined,
            data["focus_question"], "gap_assessment_initial", coverage_plan, data["regional_passes"])
        data["assessments"].append({**first.model_dump(mode="json"), "status": first.status})
        if first.status != "answered":
            for query in first.followup_queries:
                result, candidate = _run_query(store, request, context, model, attempts, query,
                    len(data["regional_passes"]), arxiv_search=arxiv_search, paper_search=paper_search,
                    pdf_normalizer=pdf_normalizer, provider_cooldown=provider_cooldown)
                data["regional_passes"].append(result)
                if candidate:
                    regional.append(candidate)
        combined = _merge(regional, base)
        final = _assessment(store, request, context, model, attempts, combined,
            data["focus_question"], "gap_assessment_final", coverage_plan, data["regional_passes"])
        data["assessments"].append({**final.model_dump(mode="json"), "status": final.status})
        data["gaps"] = list(final.gaps)
        data["coverage_status"] = final.status
        data["coverage"] = [item.model_dump(mode="json") for item in final.coverage]
        data["uncovered_questions"] = [item.model_dump(mode="json") for item in final.coverage
            if item.status != "covered"]
        data["source_inventory"] = _source_inventory(combined, data["regional_passes"], context)
        if combined.nodes:
            all_regions = sorted({region_id for node in combined.nodes for region_id in node.source_region_ids})
            review_id = "review-" + identity((request.operation_id, "overview"))[:24]
            overview = ExtractedNode(node_id=review_id, node_type="review",
                source_region_ids=tuple(all_regions[:16]), properties={"text": data["focus_question"][:2000],
                    "question_hash": identity(request.questions), "authority": "source_attributed_proposal"})
            links = tuple(ExtractedEdge(edge_id="overview-" + identity((review_id, node.node_id))[:24],
                relation="reviews" if node.node_type == "paper" else "discusses",
                source_id=review_id, target_id=node.node_id, source_region_ids=node.source_region_ids,
                properties={"authority": "source_attributed_proposal"}) for node in combined.nodes)
            candidate = GraphExtractionCandidate(nodes=(*combined.nodes, overview), edges=(*combined.edges, *links),
                unresolved=final.gaps, model_metadata={"question_hash": identity(request.questions),
                    "focus_question": data["focus_question"], "authority": "source_attributed_proposal"})
            if context.reading_mode == "fast_provisional":
                graph, _ = _publish_provisional(store, request, context, candidate, all_regions)
                attempts.append({"kind": "consolidated_graph", "receipt_id": graph["receipt_id"],
                    "status": "provisional"})
                data.update(review_graph=graph, graph_commit={"status": "not_eligible_until_prepared",
                    "scientific_admission": False},
                    selected_source_staging=[item for regional_pass in data["regional_passes"]
                        for item in regional_pass["ingestions"] if item.get("status") == "provisional_read"])
            else:
                graph, commit_request = _publish_graph(store, request, context, candidate, all_regions)
                attempts.append({"kind": "consolidated_graph", "receipt_id": graph.receipt_id, "status": graph.status})
                data.update(review_graph=graph.model_dump(mode="json"), project_graph_prepare_request=commit_request,
                    graph_commit={"status": "pending_approval", "scientific_admission": False})
    except (Exception, CancelledError, KeyboardInterrupt) as exc:
        interrupted = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
        status = "failed"
        data.update(error=type(exc).__name__, diagnostic=str(exc)[:2000])
    terminal = "interrupted" if interrupted else status
    with store.joined_transaction():
        artifact, progress = persist_graph_outcome(store, request, context, data, terminal,
            [receipt_id, *(item["receipt_id"] for item in attempts)], version=VERSION,
            record_prefix="DeepResearch", artifact_kind="deep_research_passes_review",
            target=[target.model_dump(mode="json") for target in request.graph_targets])
        data["project_progress"] = progress
        result = ToolResult(operation="Deep Research", status=status, data=data,
            receipt_ids=(receipt_id, *(item["receipt_id"] for item in attempts)), artifacts={"review": artifact},
            note=("Fast-read review is provisional; harness selection and later Prepare and Index Sources are required before project-graph preparation."
                if context.reading_mode == "fast_provisional" else
                "Two bounded passes or one graph-continuation pass. Review graphs remain source-attributed proposals until exact approved project commit."))
        receipts.record(ExecutionReceipt(receipt_id=receipt_id, operation_id=request.operation_id,
            stage="deep_research_passes", **scope, run_id=request.run_id,
            graph_revision=request.graph_revision, status=terminal, error=data.get("error"),
            tool_version=VERSION, output_ids=(artifact,),
            metadata={"request_hash": fingerprint, "result": result.model_dump(mode="json")}))
    if interrupted:
        raise interrupted
    return result
