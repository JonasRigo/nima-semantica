"""Deterministic public-tool discovery, with no dispatch or storage access."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictStr, field_validator

from .models import StrictModel, identity
from .tool_contracts import ToolResult


class ToolGuideRequest(StrictModel):
    tool: StrictStr = Field(default="", max_length=128, description="Exact public name or tool ID; empty lists the catalog.")

    @field_validator("tool")
    @classmethod
    def trim_name(cls, value):
        return value.strip()


class PublicToolDescriptor(StrictModel):
    tool_id: str
    name: str
    description: str
    implementation: Literal["implemented", "planned"] = "planned"
    visual_approval: Literal["approved", "pending", "not_ready"] = "not_ready"
    contract_version: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    examples: tuple[dict, ...] = ()
    boundary: str
    canvas_path: str | None = None


# This is the approved inventory, not a dispatch registry. Add schemas only when
# the corresponding public transport has actually been implemented and tested.
_INVENTORY = (
    ("tool_guide", "Tool Guide", "Inspect public tool contracts and delivery status.", "Pure catalog lookup; never selects or executes tools."),
    ("research_run", "Research Run", "Record research runs and transitions.", "Records harness decisions; does not schedule successor actions."),
    ("load_ontology", "Load Ontology", "Resolve an immutable ontology identity.", "Read-only ontology lookup."),
    ("save_ontology", "Save Ontology", "Validate and save an immutable ontology profile.", "Explicit ontology write; no scientific claim admission."),
    ("prepare_and_index_sources", "Prepare and Index Sources", "Prepare exact source regions and derived retrieval indexes.", "Explicit source/index writes; source content is not verified truth."),
    ("inspect_corpus", "Inspect Corpus", "Inspect scoped corpus inventory and readiness.", "Read-only inventory; readiness is not evidence fidelity."),
    ("retrieve_research_context", "Retrieve Research Context", "Retrieve revision-bound passages and graph context.", "Read-only retrieval with authorized scope and exact evidence references."),
    ("read_evidence", "Read Evidence", "Read exact source passages and provenance.", "Read-only, scope-checked source access."),
    ("calculate_mathematics", "Calculate Mathematics", "Execute symbolic or numerical calculations.", "Isolated execution; results do not certify correspondence to a source claim."),
    ("search_for_counterexamples", "Search for Counterexamples", "Search for independently checkable refuting witnesses.", "No witness found does not establish a proof."),
    ("verify_lean", "Verify Lean", "Check a formal proof in a pinned isolated environment.", "Kernel verification does not establish source-to-formal correspondence."),
    ("deep_extraction", "Deep Extraction", "Extract regional graphs and reconcile document-level proposals.", "Source-grounded extraction is proposal-only, not evidence of correctness."),
    ("analyze_graph", "Analyze Graph", "Analyze scoped graph structure and logical consequences.", "Results remain conditional on encoded premises and assumptions."),
    ("trace_claim_dependencies", "Trace Claim Dependencies", "Trace claim dependencies, cycles, and outstanding premises.", "Graph connectivity alone does not establish entailment."),
    ("substantiate_graph_snapshot", "Substantiate Graph Snapshot", "Assess graph fidelity and supporting or contradicting evidence.", "Revision-bound assessment and correction proposals; no automatic graph mutation."),
    ("update_project_graph", "Update Project Graph", "Apply explicitly approved graph changes.", "Requires exact scoped approval, revision, ontology, and provenance validation."),
    ("hypothesis_generation", "Hypothesis Generation", "Generate or reformulate grounded hypotheses.", "Hypotheses remain proposals and do not become admitted facts."),
    ("compare_research_objects", "Compare Research Objects", "Compare hypotheses or mathematical objects.", "Comparison does not confer verification or admission authority."),
    ("deep_research", "Deep Research", "Develop source-grounded research analyses.", "Preserves evidence, uncertainty, and attempts; no self-approval."),
    ("review_research", "Review Research", "Review claims, arguments, and research artifacts.", "Missing-support findings require matching substantiation before publication."),
    ("save_research_analysis", "Save Research Analysis", "Persist analyses, reports, and research bundles.", "Persistence is not certification; adverse findings retain substantiation requirements."),
    ("draft_proof", "Draft Proof", "Develop proof strategies through a harness skill and private proof MCP graph.", "Private working state; draft completion retains explicit obligations and is uncertified."),
    ("conduct_proof", "Conduct Proof", "Develop arguments using a private proof graph and the full research toolbox.", "Harness-owned execution and interpretation; project recording requires a separate approved operation."),
    ("draft_lean", "Draft Lean", "Retrieve library context, draft formal statements and repair proof attempts using verifier diagnostics.", "Generated code remains a proposal pending independent verification; the target and pinned environment cannot silently change."),
)


def public_tool_catalog() -> tuple[PublicToolDescriptor, ...]:
    """Fresh values prevent callers from modifying future lookup results."""
    tools = []
    for tool_id, name, description, boundary in _INVENTORY:
        details = {}
        if tool_id == "tool_guide":
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=ToolGuideRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({}, {"tool": "Tool Guide"}, {"tool": "calculate_mathematics"}),
                canvas_path="examples/langflow_replacement/tool_guide.json")
        elif tool_id == "research_run":
            from .research_run_tool import ResearchRunRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=ResearchRunRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"inspect", "run_id":"inspection-run"},
                    {"mode":"create", "run_id":"inspection-run", "operation_id":"create-1",
                     "creation":{"objective":"Inspect the research workflow", "skill_id":"manual-review", "skill_revision":"1"}}),
                canvas_path="examples/langflow_replacement/research_run.json")
        elif tool_id in ("load_ontology", "save_ontology"):
            from .ontology_tools import LoadOntologyRequest, SaveOntologyRequest
            schema = LoadOntologyRequest if tool_id == "load_ontology" else SaveOntologyRequest
            examples = ({"mode": "list"}, {"mode": "load", "name": "claim_obligation", "version": "1.1.0"}) if tool_id == "load_ontology" else (
                {"mode": "validate", "profile": {"name": "custom", "version": "1.0.0", "node_types": [{"name": "claim", "description": "A claim"}]}},)
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=schema.model_json_schema(), output_schema=ToolResult.model_json_schema(), examples=examples,
                canvas_path=f"examples/langflow_replacement/{tool_id}.json")
        elif tool_id == "prepare_and_index_sources":
            from .source_tools import PrepareSourcesRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=PrepareSourcesRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({"mode": "preview", "sources": [{"name": "paper.md", "text": "A source statement."}]},),
                canvas_path="examples/langflow_replacement/prepare_and_index_sources.json")
        elif tool_id == "inspect_corpus":
            from .corpus_inspection import InspectCorpusRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=InspectCorpusRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({}, {"offset": 0, "limit": 10}),
                canvas_path="examples/langflow_replacement/inspect_corpus.json")
        elif tool_id == "retrieve_research_context":
            from .research_retrieval import ResearchRetrievalRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=ResearchRetrievalRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({"query": "Find the stated assumptions", "mode": "lexical"},),
                canvas_path="examples/langflow_replacement/retrieve_research_context.json")
        elif tool_id == "read_evidence":
            from .evidence_reader import ReadEvidenceRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=ReadEvidenceRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({"region_id": "region-id-from-retrieval"},),
                canvas_path="examples/langflow_replacement/read_evidence.json")
        elif tool_id == "calculate_mathematics":
            from .math_mcp_contracts import OpenSession
            details = dict(implementation="implemented", visual_approval="approved", contract_version="mcp-v12",
                input_schema=OpenSession.model_json_schema(), output_schema={"type": "object"},
                examples=({"request_id": "calculation-1", "task": "Compute an antiderivative of x**2.", "required_paths": ["answer"]},),
                canvas_path="examples/langflow_replacement/math_mcp/open.json")
        elif tool_id == "search_for_counterexamples":
            from .counterexample_contracts import CounterexampleRequest
            details = dict(implementation="implemented", visual_approval="approved", contract_version="1",
                input_schema=CounterexampleRequest.model_json_schema(), output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"}, {"mode":"agent","operation_id":"search-1",
                    "statement":"For every integer x, x squared equals x.","domain":"integers","quantifier":"forall"}),
                canvas_path="examples/langflow_replacement/search_for_counterexamples.json")
        elif tool_id in ("draft_proof", "conduct_proof"):
            from .proof_mcp import Open
            details=dict(implementation="implemented",visual_approval="not_ready",contract_version="proof-session-v1",
                input_schema=Open.model_json_schema(),output_schema={"type":"object"},
                examples=({"request_id":"proof-1","target":"Prove the specified lemma", "mode":"draft" if tool_id == "draft_proof" else "conduct"},))
        elif tool_id == "draft_lean":
            from .lean_draft_contracts import DraftLeanRequest
            details=dict(implementation="implemented",visual_approval="approved",contract_version="2",
                input_schema=DraftLeanRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/draft_lean.json")
        elif tool_id == "save_research_analysis":
            from .research_analysis import SaveAnalysisRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=SaveAnalysisRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/save_research_analysis.json")
        elif tool_id == "verify_lean":
            from .verify_lean_tool import VerifyLeanRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="2",
                input_schema=VerifyLeanRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},{"mode":"verify","operation_id":"lean-check-1",
                    "sources":{"Submission":"theorem target : True := True.intro"},"targets":["target"],"imports":["Submission"]}),
                canvas_path="examples/langflow_replacement/verify_lean.json")
        elif tool_id == "deep_extraction":
            from .extraction_contracts import DeepExtractionRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=DeepExtractionRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},{"mode":"regional","operation_id":"extract-1","source_region_ids":["prepared-region-id"]}),
                canvas_path="examples/langflow_replacement/deep_extraction.json")
        elif tool_id == "analyze_graph":
            from .graph_analysis import AnalyzeGraphRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=AnalyzeGraphRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/analyze_graph.json")
        elif tool_id == "trace_claim_dependencies":
            from .claim_dependencies import TraceDependenciesRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=TraceDependenciesRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/trace_claim_dependencies.json")
        elif tool_id == "substantiate_graph_snapshot":
            from .substantiation_contracts import SubstantiationRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=SubstantiationRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/substantiate_graph_snapshot.json")
        elif tool_id == "review_research":
            from .review_contracts import ReviewRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=ReviewRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/review_research.json")
        elif tool_id == "update_project_graph":
            from .project_update import UpdateProjectRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=UpdateProjectRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/update_project_graph.json")
        elif tool_id == "hypothesis_generation":
            from .hypothesis_contracts import GenerateHypothesesRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=GenerateHypothesesRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/hypothesis_generation.json")
        elif tool_id == "compare_research_objects":
            from .comparison_contracts import CompareObjectsRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="1",
                input_schema=CompareObjectsRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"mode":"preview"},),canvas_path="examples/langflow_replacement/compare_research_objects.json")
        elif tool_id == "deep_research":
            from .deep_research_passes import PassResearchRequest
            details = dict(implementation="implemented",visual_approval="approved",contract_version="11",
                input_schema=PassResearchRequest.model_json_schema(),output_schema=ToolResult.model_json_schema(),
                examples=({"research":{"mode":"preview"}},),canvas_path="examples/langflow_replacement/deep_research_simple.json")
        tools.append(PublicToolDescriptor(tool_id=tool_id, name=name, description=description, boundary=boundary, **details))
    return tuple(tools)


def tool_guide(request: ToolGuideRequest) -> ToolResult:
    """Lookup only: no imports, evaluation, dispatch, provider calls, or writes."""
    request = ToolGuideRequest.model_validate(request.model_dump(mode="json"))
    catalog = public_tool_catalog()
    key = request.tool.casefold()
    selected = tuple(item for item in catalog if not key or key in (item.name.casefold(), item.tool_id))
    return ToolResult(
        operation="Tool Guide", status="complete" if selected else "failed",
        data={"catalog_version": "1", "catalog_hash": identity([item.model_dump(mode="json") for item in catalog]),
              "tools": [item.model_dump(mode="json") for item in selected],
              "execution_supported": False,
              "deployment_status": "Not inspected; catalog availability does not imply live publication.",
              "contract_note": "Planned tools have no public invocation schema yet. Only the harness selects actions."},
        diagnostics=() if selected else ({"code": "tool_guide.unknown_tool", "message": "Use an exact catalog name or tool ID."},),
        note="Catalog discovery only; no tool was executed and no persistent receipt was created.",
    )
