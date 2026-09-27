"""Contracts for the first framework-independent NIMA workflow services."""

from __future__ import annotations

from .okf_contracts import GraphRevision

from typing import Any, Literal

from pydantic import Field, model_validator

from .evidence_contracts import EvidenceReference
from .models import StrictModel
from .normalization_contracts import (
    FlowAdapterManifest,
    NormalizedOutput,
    NormalizedRequest,
)
from .okf_contracts import GraphIdentifier, OKFDelta, OKFReference
from .retrieval_contracts import RetrievalContextPacket
from .graph_commit import OKFCommitApproval, OKFCommitResult


class WorkflowDiagnostic(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    message: str = Field(min_length=1, max_length=2_000)
    severity: Literal["info", "warning", "error"] = "warning"


class WorkflowReceipt(StrictModel):
    receipt_id: GraphIdentifier
    contract_id: GraphIdentifier = "operation_receipt"


class InputNormalizerRequest(StrictModel):
    manifest: FlowAdapterManifest
    request: NormalizedRequest

    @model_validator(mode="after")
    def adapter_matches_manifest(self) -> "InputNormalizerRequest":
        if self.request.adapter_id != self.manifest.adapter_id:
            raise ValueError("normalized request adapter does not match its manifest")
        return self


InputNormalizerOutput = NormalizedOutput


class GraphRetrievalPolicy(StrictModel):
    max_hops: int = Field(default=2, ge=0, le=8)
    limit: int = Field(default=8, ge=1, le=256)
    max_nodes: int = Field(default=256, ge=1, le=10_000)
    max_edges: int = Field(default=1_024, ge=1, le=50_000)
    max_neighbors: int = Field(default=64, ge=1, le=1_000)
    max_results: int = Field(default=64, ge=1, le=10_000)
    max_omitted: int = Field(default=256, ge=0, le=10_000)
    prepared_only: bool = False


class GraphRetrievalNormalizerRequest(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    query: str = Field(min_length=1, max_length=20_000)
    policy: GraphRetrievalPolicy = Field(default_factory=GraphRetrievalPolicy)


class GraphRetrievalNormalizerOutput(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision
    context_packet: RetrievalContextPacket
    selected: tuple[dict[str, Any], ...] = Field(default=(), max_length=100_000)
    diagnostics: tuple[WorkflowDiagnostic, ...] = Field(default=(), max_length=256)
    receipts: tuple[WorkflowReceipt, ...] = Field(default=(), max_length=256)
    authority: Literal["read_only_retrieval"] = "read_only_retrieval"

    @model_validator(mode="after")
    def validate_context(self) -> "GraphRetrievalNormalizerOutput":
        if self.context_packet.corpus_id != self.corpus_id:
            raise ValueError("retrieval context corpus scope differs from output")
        if self.context_packet.project_id not in (None, self.project_id):
            raise ValueError("retrieval context project scope differs from output")
        if self.context_packet.graph_revision != self.graph_revision:
            raise ValueError("retrieval context revision differs from output")
        return self


HypothesisKind = Literal[
    "research",
    "bound",
    "mechanism",
    "missing_support",
    "review_problem",
    "interpretation",
    "limitation",
    "inconsistency",
    "reproducibility",
]


class HypothesisProposal(StrictModel):
    schema_version: Literal[2] = 2
    proposal_id: GraphIdentifier
    corpus_id: GraphIdentifier | None = None
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    kind: HypothesisKind
    statement: str = Field(min_length=1, max_length=20_000)
    supporting_references: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    contradicting_references: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=256)
    assumptions: tuple[str, ...] = Field(default=(), max_length=256)
    expected_consequences: tuple[str, ...] = Field(default=(), max_length=256)
    unresolved_obligations: tuple[str, ...] = Field(default=(), max_length=256)
    verification_plans: tuple[str, ...] = Field(default=(), max_length=256)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=256)
    model_metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)
    diagnostics: tuple[WorkflowDiagnostic, ...] = Field(default=(), max_length=256)
    status: Literal["proposed"] = "proposed"
    authority: Literal["proposal_only"] = "proposal_only"


class HypothesisGenerationRequest(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    objective: str | None = Field(default=None, max_length=20_000)
    claim_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    finding_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    conflict_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    obligation_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    graph_references: tuple[OKFReference, ...] = Field(default=(), max_length=10_000)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=10_000)
    ontology_profile: GraphIdentifier | None = None
    receipt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=256)


class HypothesisGenerationOutput(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    proposals: tuple[HypothesisProposal, ...] = Field(min_length=1, max_length=10_000)
    diagnostics: tuple[WorkflowDiagnostic, ...] = Field(default=(), max_length=256)
    receipts: tuple[WorkflowReceipt, ...] = Field(default=(), max_length=256)
    authority: Literal["proposal_only"] = "proposal_only"


class HypothesisComparisonRequest(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    context_packet: RetrievalContextPacket
    hypotheses: tuple[HypothesisProposal, ...] = Field(min_length=1, max_length=10_000)
    receipt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def context_scope(self) -> "HypothesisComparisonRequest":
        if self.context_packet.corpus_id != self.corpus_id:
            raise ValueError("comparison context corpus scope differs from request")
        if self.context_packet.project_id not in (None, self.project_id):
            raise ValueError("comparison context project scope differs from request")
        if self.context_packet.graph_revision != self.graph_revision:
            raise ValueError("comparison context revision differs from request")
        return self


class HypothesisComparison(StrictModel):
    schema_version: Literal[2] = 2
    proposal_id: GraphIdentifier
    corpus_id: GraphIdentifier | None = None
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    assessment: Literal["supported", "contradicted", "mixed", "unresolved"]
    supporting_references: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    contradicting_references: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    unresolved_obligations: tuple[str, ...] = Field(default=(), max_length=256)
    verification_plans: tuple[str, ...] = Field(default=(), max_length=256)
    rationale: str = Field(min_length=1, max_length=20_000)
    diagnostics: tuple[WorkflowDiagnostic, ...] = Field(default=(), max_length=256)
    authority: Literal["proposal_only"] = "proposal_only"


class HypothesisComparisonOutput(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    comparisons: tuple[HypothesisComparison, ...] = Field(min_length=1, max_length=10_000)
    diagnostics: tuple[WorkflowDiagnostic, ...] = Field(default=(), max_length=256)
    receipts: tuple[WorkflowReceipt, ...] = Field(default=(), max_length=256)
    authority: Literal["proposal_only"] = "proposal_only"


class GraphCommitRequest(StrictModel):
    request_id: GraphIdentifier
    delta: OKFDelta
    approval: OKFCommitApproval
    authority: Literal["approved_commit"] = "approved_commit"

    @model_validator(mode="after")
    def approval_matches_delta(self) -> "GraphCommitRequest":
        if self.approval.delta_id != self.delta.delta_id:
            raise ValueError("commit approval does not match delta")
        return self


class GraphCommitOutput(StrictModel):
    request_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    base_revision: GraphRevision
    final_revision: GraphRevision
    result: OKFCommitResult
    receipts: tuple[WorkflowReceipt, ...] = Field(min_length=1, max_length=8)
    authority: Literal["approved_commit"] = "approved_commit"


__all__ = [
    "GraphCommitOutput",
    "GraphCommitRequest",
    "GraphRetrievalNormalizerOutput",
    "GraphRetrievalNormalizerRequest",
    "GraphRetrievalPolicy",
    "InputNormalizerOutput",
    "InputNormalizerRequest",
    "HypothesisComparison",
    "HypothesisComparisonOutput",
    "HypothesisComparisonRequest",
    "HypothesisGenerationOutput",
    "HypothesisGenerationRequest",
    "HypothesisKind",
    "HypothesisProposal",
    "WorkflowDiagnostic",
    "WorkflowReceipt",
]
