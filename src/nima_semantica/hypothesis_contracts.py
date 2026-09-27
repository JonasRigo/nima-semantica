"""Harness-owned hypothesis scope and small proposal-only agent actions."""
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from .models import StrictModel
from .okf_contracts import GraphIdentifier,GraphIdentity,GraphRevision,Digest
from .workflow_contracts import HypothesisKind
from .providers import ModelManifest
from .math_retrieval import MathRetrievalPolicy,RetrieveMathContext
from .reasoning_state import Anchor
from .counterexample_contracts import IntegerSearch
from .extraction_contracts import ReadRegions,EmptyAction

VERSION="hypothesis-generation-v1"


class HypothesisTarget(StrictModel):
    kind: Literal["node","edge"]="node"
    ref: GraphIdentity


class HypothesisConstraint(StrictModel):
    constraint_id: GraphIdentifier
    text: str = Field(min_length=1,max_length=2000)


class GenerateHypothesesRequest(StrictModel):
    mode: Literal["preview","generate","restate"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    artifact_id: Digest | None=None
    objective: str = Field(default="Propose grounded, testable research alternatives and retain their uncertainties.",min_length=1,max_length=12000)
    graph_targets: tuple[HypothesisTarget,...] = Field(default=(),max_length=16)
    source_region_ids: tuple[GraphIdentifier,...] = Field(default=(),max_length=32)
    prior_hypothesis_ids: tuple[GraphIdentifier,...] = Field(default=(),max_length=8)
    required_assumptions: tuple[str,...] = Field(default=(),max_length=8)
    constraints: tuple[HypothesisConstraint,...] = Field(default=(),max_length=16)
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def scope(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None):raise ValueError("execution requires operation and exact graph revision")
        if self.mode=="restate" and not self.prior_hypothesis_ids:raise ValueError("restate requires existing hypothesis IDs")
        for seq in (self.source_region_ids,self.prior_hypothesis_ids,tuple((t.kind,t.ref) for t in self.graph_targets),tuple(c.constraint_id for c in self.constraints)):
            if len(set(seq))!=len(seq):raise ValueError("duplicate scope item")
        if any(not a.strip() or len(a)>2000 for a in self.required_assumptions):raise ValueError("invalid required assumption")
        return self


class HypothesisContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool=False
    allow_audit_writes: StrictBool=False
    allow_counterexample_checks: StrictBool=False
    max_actions: StrictInt=Field(default=20,ge=1,le=64)
    max_candidates: StrictInt=Field(default=6,ge=1,le=8)
    max_nodes: StrictInt=Field(default=256,ge=1,le=256)
    max_edges: StrictInt=Field(default=512,ge=0,le=512)
    max_read_regions: StrictInt=Field(default=32,ge=1,le=64)
    max_existing_hypotheses: StrictInt=Field(default=1000,ge=1,le=2000)
    max_evaluations: StrictInt=Field(default=1000,ge=1,le=10000)
    timeout_seconds: StrictInt=Field(default=30,ge=1,le=120)
    model_manifest: ModelManifest | None=None
    retrieval: MathRetrievalPolicy=Field(default_factory=MathRetrievalPolicy)


class ConstraintResponse(StrictModel):
    constraint_id: GraphIdentifier
    assessment: Literal["proposed_consistent","unresolved"]
    explanation: str = Field(min_length=1,max_length=2000)


class ProposeHypothesis(StrictModel):
    candidate_id: GraphIdentifier
    kind: HypothesisKind="research"
    statement: str = Field(min_length=1,max_length=6000)
    domain: str = Field(min_length=1,max_length=2000)
    quantifier: Literal["forall","exists","mixed","not_formalized"]="not_formalized"
    disposition: Literal["active","withdrawn"]="active"
    grounding: Literal["source_grounded","speculative"]
    rationale: str = Field(min_length=1,max_length=4000)
    concern_severity: Literal["unspecified","low","medium","high"]="unspecified"
    supporting: tuple[Anchor,...] = Field(default=(),max_length=8)
    contradicting: tuple[Anchor,...] = Field(default=(),max_length=8)
    graph_references: tuple[HypothesisTarget,...] = Field(default=(),max_length=16)
    assumptions: tuple[str,...] = Field(default=(),max_length=8)
    expected_consequences: tuple[str,...] = Field(min_length=1,max_length=8)
    unresolved_obligations: tuple[str,...] = Field(min_length=1,max_length=16)
    verification_plans: tuple[str,...] = Field(min_length=1,max_length=8)
    constraints: tuple[ConstraintResponse,...] = Field(default=(),max_length=16)
    correction_reason: str = Field(default="",max_length=2000)

    @model_validator(mode="after")
    def bounded(self):
        if not self.statement.strip() or not self.domain.strip():raise ValueError("nonempty statement/domain required")
        if self.grounding=="source_grounded" and not self.supporting:raise ValueError("source-grounded candidate needs exact supporting passages")
        if self.kind in ("review_problem","missing_support") and not (self.supporting or self.graph_references):raise ValueError("review concern needs an exact selected graph target or passage")
        if self.kind in ("review_problem","missing_support") and self.concern_severity=="unspecified":raise ValueError("review concern needs a proposed severity estimate")
        if self.disposition=="withdrawn" and not self.correction_reason.strip():raise ValueError("withdrawal requires a reason")
        for value in (*self.assumptions,*self.expected_consequences,*self.unresolved_obligations,*self.verification_plans):
            if not value.strip() or len(value)>2000:raise ValueError("invalid candidate list entry")
        return self


class ProbeHypothesis(StrictModel):
    candidate_id: GraphIdentifier
    encoding: IntegerSearch


SCHEMAS={"read_regions":ReadRegions,"retrieve_context":RetrieveMathContext,"propose_hypothesis":ProposeHypothesis,
    "probe_hypothesis":ProbeHypothesis,"analyze_candidates":EmptyAction,"submit_result":EmptyAction}
