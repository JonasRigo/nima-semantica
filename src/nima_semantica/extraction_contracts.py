"""Harness-selected extraction scope and bounded OSA action contracts."""
from typing import Any, Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel, canonical
from .okf_contracts import GraphIdentifier, GraphName, GraphRevision
from .providers import ModelManifest
from .math_retrieval import MathRetrievalPolicy, RetrieveMathContext
from .reasoning_state import Anchor
from .consolidation_contracts import ConsolidationSelection

VERSION = "deep-extraction-v1"


class DeepExtractionRequest(StrictModel):
    mode: Literal["preview","plan","regional","document","document_to_proposal","consolidation_plan","consolidate"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    question: str = Field(default="Extract source-attributed claims, definitions, obligations and their relations.",min_length=1,max_length=12000)
    source_region_ids: tuple[GraphIdentifier,...] = Field(default=(),max_length=32)
    source_id: GraphIdentifier | None = None
    plan_attempt_id: GraphIdentifier | None = Field(default=None, description="Plan only: change for a deliberate new extraction attempt; omit for replay-stable batch IDs.")
    consolidation: ConsolidationSelection | None = None
    ontology_profile: GraphIdentifier = "literature_evidence@1.0.0"
    graph_revision: GraphRevision | None = None
    target_record_id: GraphIdentifier | None = None
    parent_record_id: GraphIdentifier | None = None

    @model_validator(mode="after")
    def selected_scope(self):
        if len(canonical(self)) > 1_000_000:raise ValueError("extraction request exceeds 1 MB complete-read transport bound")
        if len(set(self.source_region_ids))!=len(self.source_region_ids):raise ValueError("duplicate region")
        if self.source_id and self.source_region_ids and self.mode != "regional":raise ValueError("Use regional mode for explicit regions with an optional source consistency check; document and plan modes select source_id only")
        if self.plan_attempt_id and self.mode != "plan":raise ValueError("plan_attempt_id is only accepted in plan mode")
        if self.mode not in ("preview", "plan", "consolidation_plan") and not self.operation_id:raise ValueError("execution requires operation_id")
        if self.mode in ("consolidation_plan", "consolidate"):
            if not self.consolidation or self.graph_revision is None:raise ValueError("consolidation requires selected artifacts and exact graph_revision")
            if self.source_id or self.source_region_ids:raise ValueError("consolidation selects saved graph artifacts, not extraction regions")
            if self.mode == "consolidate" and not self.consolidation.plan_hash:raise ValueError("consolidate requires plan_hash from consolidation_plan")
            if self.mode == "consolidation_plan" and (self.consolidation.merges or self.consolidation.links):raise ValueError("plan is read-only; submit decisions with consolidate")
        elif self.consolidation is not None:raise ValueError("consolidation fields require a consolidation mode")
        if self.mode == "plan" and not self.source_id:raise ValueError("planning requires a prepared source ID")
        if self.mode=="regional" and not self.source_region_ids:raise ValueError("regional mode requires prepared regions")
        if self.mode in ("document","document_to_proposal") and not self.source_id:raise ValueError("document mode requires a prepared source ID")
        if (self.target_record_id or self.parent_record_id) and not self.graph_revision:raise ValueError("project links require graph revision")
        return self


class DeepExtractionContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool = False
    allow_audit_writes: StrictBool = False
    max_actions: StrictInt = Field(default=16,ge=1,le=64)
    max_nodes: StrictInt = Field(default=64,ge=1,le=128)
    max_edges: StrictInt = Field(default=128,ge=0,le=256)
    model_manifest: ModelManifest | None = None
    retrieval: MathRetrievalPolicy = Field(default_factory=MathRetrievalPolicy)


class ReadRegions(StrictModel):
    region_ids: tuple[GraphIdentifier,...] = Field(min_length=1,max_length=4)


class ExtractionNode(StrictModel):
    node_id: GraphIdentifier
    node_type: GraphName
    text: str = Field(min_length=1,max_length=4000)
    properties: dict[str,Any] = Field(default_factory=dict,max_length=32)
    anchors: tuple[Anchor,...] = Field(min_length=1,max_length=8,description="Exact quotations in selected, previously read source regions; source_id is the region ID.")


class ExtractionEdge(StrictModel):
    edge_id: GraphIdentifier
    relation: GraphName
    source_id: GraphIdentifier
    target_id: GraphIdentifier
    properties: dict[str,Any] = Field(default_factory=dict,max_length=32)
    anchors: tuple[Anchor,...] = Field(min_length=1,max_length=8)


class RegionCoverage(StrictModel):
    region_id: GraphIdentifier
    status: Literal["extracted","no_relevant_content","deferred"]
    reason: str = Field(min_length=1,max_length=2000)


class ExtractionIssue(StrictModel):
    issue_id: GraphIdentifier
    description: str = Field(min_length=1,max_length=2000)
    status: Literal["open","resolution_proposed"] = "open"
    resolution_reason: str = Field(default="",max_length=2000)


class ProposeGraph(StrictModel):
    nodes: tuple[ExtractionNode,...] = Field(default=(),max_length=128)
    edges: tuple[ExtractionEdge,...] = Field(default=(),max_length=256)
    coverage: tuple[RegionCoverage,...] = Field(min_length=1,max_length=32)
    issues: tuple[ExtractionIssue,...] = Field(default=(),max_length=16)
    correction_reason: str = Field(default="",max_length=4000)


class EmptyAction(StrictModel):
    pass


SCHEMAS={"read_regions":ReadRegions,"retrieve_context":RetrieveMathContext,"propose_graph":ProposeGraph,
    "analyze_graph":EmptyAction,"submit_result":EmptyAction}
