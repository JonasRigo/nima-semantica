"""Harness-pinned review scope and advisory assessment actions."""
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from .models import StrictModel,identity
from .okf_contracts import GraphIdentifier,GraphIdentity,GraphRevision,Digest
from .providers import ModelManifest
from .math_retrieval import MathRetrievalPolicy,RetrieveMathContext
from .reasoning_state import Anchor
from .extraction_contracts import ReadRegions,EmptyAction
from .counterexample_contracts import IntegerSearch

VERSION="review-research-v1"


class ReviewTarget(StrictModel):
    target_id: GraphIdentifier
    ref: GraphIdentity
    statement: str=Field(min_length=1,max_length=12000)
    argument: str=Field(default="",max_length=16000)
    domain: str=Field(min_length=1,max_length=2000)
    quantifier: Literal["forall","exists","mixed"]="forall"
    assumptions: tuple[str,...]=Field(default=(),max_length=16)
    dependencies: tuple[GraphIdentity,...]=Field(default=(),max_length=16)
    unresolved_obligations: tuple[str,...]=Field(default=(),max_length=16)
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=16)
    content_revision: Digest

    @model_validator(mode="after")
    def pinned(self):
        if self.content_revision!=identity(self.model_dump(mode="json",exclude={"content_revision"})):raise ValueError("target content revision differs from exact selected content")
        for s in (*self.assumptions,*self.unresolved_obligations):
            if not s.strip() or len(s)>2000:raise ValueError("invalid target assumption/obligation")
        if not self.statement.strip() or not self.domain.strip():raise ValueError("empty target")
        return self


class ReviewRequest(StrictModel):
    mode: Literal["preview","argument_review","proof_assessment","project_assessment","critique"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    artifact_id: Digest | None=None
    targets: tuple[ReviewTarget,...]=Field(default=(),max_length=8)
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=32)
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None or not self.targets):raise ValueError("review requires operation, revision and exact targets")
        if len({t.target_id for t in self.targets})!=len(self.targets):raise ValueError("duplicate review target")
        if self.mode=="proof_assessment" and any(not t.argument.strip() for t in self.targets):raise ValueError("proof assessment requires the selected argument for each part")
        return self


class ReviewContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool=False
    allow_audit_writes: StrictBool=False
    allow_counterexamples: StrictBool=False
    counterexample_target_ids: tuple[GraphIdentifier,...]=()
    max_counterexamples: StrictInt=Field(default=3,ge=0,le=8)
    max_evaluations: StrictInt=Field(default=1000,ge=1,le=10000)
    timeout_seconds: StrictInt=Field(default=60,ge=1,le=120)
    allow_substantiation: StrictBool=False
    max_substantiations: StrictInt=Field(default=2,ge=0,le=8)
    substantiation_max_actions: StrictInt=Field(default=16,ge=1,le=32)
    max_actions: StrictInt=Field(default=24,ge=1,le=64)
    max_nodes: StrictInt=Field(default=256,ge=1,le=256)
    max_edges: StrictInt=Field(default=512,ge=0,le=512)
    max_read_regions: StrictInt=Field(default=32,ge=1,le=48)
    model_manifest: ModelManifest | None=None
    retrieval: MathRetrievalPolicy=Field(default_factory=MathRetrievalPolicy)


class SearchReviewTarget(StrictModel):
    target_id: GraphIdentifier
    encoding: IntegerSearch


class SubstantiateReviewTarget(StrictModel):
    target_id: GraphIdentifier
    finding: str=Field(min_length=1,max_length=4000)


class AssessReviewTarget(StrictModel):
    target_id: GraphIdentifier
    assessment: Literal["plausible","concerns","unresolved"]
    rationale: str=Field(min_length=1,max_length=6000)
    target_correspondence: str=Field(min_length=1,max_length=2000)
    assumption_analysis: str=Field(min_length=1,max_length=2000)
    inference_analysis: str=Field(min_length=1,max_length=4000)
    dependency_analysis: str=Field(min_length=1,max_length=2000)
    source_applicability: str=Field(min_length=1,max_length=2000)
    testability: Literal["searched","not_tested","unsupported"]
    testability_reason: str=Field(min_length=1,max_length=2000)
    missing_support_finding: str=Field(default="",max_length=4000)
    substantiation_operation_id: GraphIdentifier | None=None
    supporting: tuple[Anchor,...]=Field(default=(),max_length=8)
    contradicting: tuple[Anchor,...]=Field(default=(),max_length=8)
    unresolved_obligations: tuple[str,...]=Field(min_length=1,max_length=16)
    limitations: tuple[str,...]=Field(min_length=1,max_length=16)
    proposed_follow_up: tuple[str,...]=Field(default=(),max_length=8)
    correction_reason: str=Field(default="",max_length=2000)

    @model_validator(mode="after")
    def bounded(self):
        for s in (*self.unresolved_obligations,*self.limitations,*self.proposed_follow_up):
            if not s.strip() or len(s)>2000:raise ValueError("invalid assessment list")
        return self


SCHEMAS={"read_regions":ReadRegions,"retrieve_context":RetrieveMathContext,"search_counterexamples":SearchReviewTarget,
    "substantiate_target":SubstantiateReviewTarget,"assess_target":AssessReviewTarget,"analyze_review":EmptyAction,"submit_result":EmptyAction}
