"""Target-bound source substantiation, without scientific admission authority."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel
from .okf_contracts import GraphIdentifier, GraphIdentity, GraphRevision, Digest
from .providers import ModelManifest
from .math_retrieval import MathRetrievalPolicy, RetrieveMathContext
from .reasoning_state import Anchor
from .extraction_contracts import ReadRegions, EmptyAction

VERSION = "substantiate-graph-snapshot-v2"


class SubstantiationTarget(StrictModel):
    kind: Literal["node", "edge"] = "node"
    ref: GraphIdentity
    finding: str = Field(min_length=1, max_length=4000, description="Exact assessment being challenged, not a replacement claim.")


class SubstantiationRequest(StrictModel):
    mode: Literal["preview", "substantiate"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    artifact_id: Digest | None = None
    targets: tuple[SubstantiationTarget, ...] = Field(default=(), max_length=16)
    source_region_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    target_record_id: GraphIdentifier | None = None
    parent_record_id: GraphIdentifier | None = None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode != "preview" and (not self.operation_id or self.graph_revision is None or not self.targets):
            raise ValueError("execution requires operation ID, graph revision and selected targets")
        if len({(t.kind, t.ref) for t in self.targets}) != len(self.targets):raise ValueError("duplicate target")
        if len(set(self.source_region_ids)) != len(self.source_region_ids):raise ValueError("duplicate region")
        return self


class SubstantiationContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool = False
    allow_audit_writes: StrictBool = False
    max_actions: StrictInt = Field(default=20, ge=1, le=64)
    max_nodes: StrictInt = Field(default=128, ge=1, le=256)
    max_edges: StrictInt = Field(default=256, ge=0, le=512)
    max_read_regions: StrictInt = Field(default=32, ge=1, le=64)
    model_manifest: ModelManifest | None = None
    retrieval: MathRetrievalPolicy = Field(default_factory=MathRetrievalPolicy)


class RegionAnchor(Anchor):
    source_id: GraphIdentifier = Field(description="Exact region_id returned by read_regions or retrieve_context, never the document source_id.")


class JustificationStep(StrictModel):
    explanation: str = Field(min_length=1,max_length=2000)
    anchors: tuple[RegionAnchor,...] = Field(min_length=1,max_length=8)


class AssessTarget(StrictModel):
    target_index: StrictInt = Field(ge=0, le=15)
    extraction_fidelity: Literal["consistent", "misrepresented", "unresolved"] = Field(description="Whether the selected graph node or edge faithfully represents the source; this is not a verdict on the harness finding.")
    source_substantiation: Literal["support_located", "conflicting", "support_not_located", "unresolved"] = Field(description="Verdict about the underlying graph claim, not the harness finding. support_located requires an applicable source argument beyond a bare assertion; support_not_located is limited to examined scope.")
    recommendation: Literal["retain", "revise", "withdraw", "unresolved"] = Field(description="Recommendation about the selected harness finding, not the graph claim. A finding that the claim lacks support may need withdrawal when support is located.")
    supporting: tuple[RegionAnchor, ...] = Field(default=(), max_length=8, description="Exact read-region passages supporting the underlying graph claim, not the harness finding.")
    contradicting: tuple[RegionAnchor, ...] = Field(default=(), max_length=8, description="Exact read-region passages conflicting with the underlying graph claim, not the harness finding.")
    justification: str = Field(min_length=1, max_length=6000, description="Explain separately what the cited source says about the graph claim and why the selected harness finding should be retained or changed.")
    justification_steps: tuple[JustificationStep,...] = Field(default=(),max_length=16)
    assumptions: tuple[str, ...] = Field(default=(), max_length=16)
    proposed_correction: str = Field(default="", max_length=4000, description="Replacement text or withdrawal explanation for the selected harness finding; not a graph edit.")
    proposed_claim_revision: str = Field(default="", max_length=4000, description="Optional proposed revision to the underlying graph claim, kept separate from the finding recommendation; never an automatic graph edit.")
    verification_obligations: tuple[str, ...] = Field(min_length=1, max_length=16)
    coverage_limitations: tuple[str, ...] = Field(min_length=1, max_length=16)
    correction_reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def evidence_requirements(self):
        if self.source_substantiation == "support_located" and (not self.supporting or not self.justification_steps):raise ValueError("support requires evidence and a passage-bound justification chain")
        if self.source_substantiation == "conflicting" and not self.contradicting:raise ValueError("conflict requires counterevidence")
        if self.source_substantiation == "support_not_located" and self.supporting:raise ValueError("located support conflicts with not-located assessment")
        if self.extraction_fidelity != "unresolved" and not (self.supporting or self.contradicting):raise ValueError("fidelity assessment requires evidence")
        if self.recommendation in ("revise", "withdraw") and not self.proposed_correction.strip():raise ValueError("correction or withdrawal needs explanation")
        if any(not v.strip() or len(v)>2000 for v in (*self.assumptions, *self.verification_obligations, *self.coverage_limitations)):
            raise ValueError("assessment lists require bounded nonempty items")
        return self


class GroundedPassage(StrictModel):
    polarity: Literal["supporting","contradicting","justification"]
    step_index: int | None = None
    region_id: GraphIdentifier
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quotation: str
    source_id: GraphIdentifier
    source_revision: GraphIdentifier
    artifact_id: Digest
    content_hash: Digest
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None
    region_hash: Digest
    receipt_id: Digest


class TargetSubstantiationAssessment(AssessTarget):
    target: SubstantiationTarget
    snapshot_hash: Digest
    grounding: tuple[GroundedPassage,...]
    verification_obligations: tuple[str,...] = Field(min_length=1,max_length=32)
    mathematical_validity: Literal["not_verified"] = "not_verified"
    semantic_assessment_authority: Literal["model_proposal"] = "model_proposal"
    source_substantiation_subject: Literal["graph_claim"] = "graph_claim"
    recommendation_subject: Literal["harness_finding"] = "harness_finding"
    unqualified_absence_supported: Literal[False] = False


SCHEMAS = {"read_regions": ReadRegions, "retrieve_context": RetrieveMathContext,
    "assess_target": AssessTarget, "analyze_dependencies": EmptyAction, "submit_result": EmptyAction}
