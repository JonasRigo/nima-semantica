"""Pinned research-object comparison inputs and advisory agent actions."""
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from .models import StrictModel
from .okf_contracts import GraphIdentifier,GraphIdentity,GraphRevision,Digest
from .workflow_contracts import HypothesisProposal
from .providers import ModelManifest
from .math_retrieval import MathRetrievalPolicy,RetrieveMathContext
from .reasoning_state import Anchor
from .extraction_contracts import ReadRegions,EmptyAction

VERSION="compare-research-objects-v4"
ObjectKind=Literal["mathematical_object","hypothesis","proof_strategy","proof_attempt"]


class ResearchObject(StrictModel):
    """Harness-supplied proposal content, never a verified proof or admitted fact."""
    kind: ObjectKind
    statement: str=Field(min_length=1,max_length=12000)
    domain: str=Field(min_length=1,max_length=2000)
    target: GraphIdentity | None=None
    assumptions: tuple[str,...]=Field(default=(),max_length=32)
    predictions: tuple[str,...]=Field(default=(),max_length=32)
    unresolved_obligations: tuple[str,...]=Field(default=(),max_length=32)
    dependencies: tuple[GraphIdentity,...]=Field(default=(),max_length=16)
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=32)
    authority: Literal["proposal_only"]="proposal_only"

    @model_validator(mode="after")
    def bounded(self):
        if self.kind in ("proof_strategy","proof_attempt") and self.target is None:raise ValueError("proof object needs exact target")
        if not self.statement.strip() or not self.domain.strip():raise ValueError("empty object")
        if any(not s.strip() or len(s)>2000 for s in (*self.assumptions,*self.predictions,*self.unresolved_obligations)):raise ValueError("invalid object list entry")
        return self


class ComparisonObject(StrictModel):
    object_id: GraphIdentifier
    graph_ref: GraphIdentity | None=None
    hypothesis_id: GraphIdentifier | None=None
    hypothesis: HypothesisProposal | None=None
    record_id: GraphIdentifier | None=None
    inline: ResearchObject | None=None

    @model_validator(mode="after")
    def one_binding(self):
        if sum(v is not None for v in (self.graph_ref,self.hypothesis_id,self.hypothesis,self.record_id,self.inline))!=1:raise ValueError("select exactly one object binding")
        return self


class ComparisonCriterion(StrictModel):
    criterion_id: GraphIdentifier
    description: str=Field(min_length=1,max_length=2000)


class CompareObjectsRequest(StrictModel):
    mode: Literal["preview","mathematical_objects","hypotheses","proof_strategies","proof_attempts"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    artifact_id: Digest | None=None
    target: GraphIdentity | None=None
    objects: tuple[ComparisonObject,...]=Field(default=(),max_length=6)
    criteria: tuple[ComparisonCriterion,...]=Field(default=(),max_length=6)
    objective: str=Field(default="Compare the selected objects without choosing a winner or certifying equivalence.",min_length=1,max_length=12000)
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=32)
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None or len(self.objects)<2 or not self.criteria):raise ValueError("comparison requires operation, exact revision, two objects and explicit criteria")
        if self.mode in ("proof_strategies","proof_attempts") and self.target is None:raise ValueError("proof comparison requires exact common target")
        for seq in (tuple(o.object_id for o in self.objects),tuple(c.criterion_id for c in self.criteria),self.source_region_ids):
            if len(set(seq))!=len(seq):raise ValueError("duplicate comparison scope item")
        return self


class ComparisonContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool=False
    allow_audit_writes: StrictBool=False
    max_actions: StrictInt=Field(default=24,ge=1,le=64)
    max_nodes: StrictInt=Field(default=256,ge=1,le=256)
    max_edges: StrictInt=Field(default=512,ge=0,le=512)
    max_read_regions: StrictInt=Field(default=32,ge=1,le=64)
    model_manifest: ModelManifest | None=None
    retrieval: MathRetrievalPolicy=Field(default_factory=MathRetrievalPolicy)


class RegionAnchor(Anchor):
    source_id: GraphIdentifier=Field(description="Exact region_id returned by read_regions or retrieve_context; never the document source_id. Quote that region's text exactly.")


class PairComparison(StrictModel):
    left: GraphIdentifier
    right: GraphIdentifier
    relation: Literal["proposed_equivalent","proposed_conflict","proposed_distinct","unresolved"]
    rationale: str=Field(min_length=1,max_length=4000)
    shared_premises: tuple[str,...]=Field(default=(),max_length=16)
    conflicting_premises: tuple[str,...]=Field(default=(),max_length=16)
    consequences: tuple[str,...]=Field(default=(),max_length=16)
    alignment_losses: tuple[str,...]=Field(default=(),max_length=16)
    unresolved_obligations: tuple[str,...]=Field(min_length=1,max_length=16)
    supporting: tuple[RegionAnchor,...]=Field(default=(),max_length=8)
    contradicting: tuple[RegionAnchor,...]=Field(default=(),max_length=8)

    @model_validator(mode="after")
    def nonempty(self):
        if self.left==self.right:raise ValueError("cannot compare an object to itself")
        for s in (*self.shared_premises,*self.conflicting_premises,*self.consequences,*self.alignment_losses,*self.unresolved_obligations):
            if not s.strip() or len(s)>2000:raise ValueError("invalid finding entry")
        if self.relation=="proposed_equivalent" and (self.conflicting_premises or self.alignment_losses):raise ValueError("represented conflicts or losses preclude unqualified proposed equivalence")
        return self


class AssessCriterion(StrictModel):
    criterion_id: GraphIdentifier
    pairs: tuple[PairComparison,...]=Field(min_length=1,max_length=15)
    correction_reason: str=Field(default="",max_length=2000)


SCHEMAS={"read_regions":ReadRegions,"retrieve_context":RetrieveMathContext,"assess_criterion":AssessCriterion,
    "analyze_objects":EmptyAction,"submit_result":EmptyAction}
