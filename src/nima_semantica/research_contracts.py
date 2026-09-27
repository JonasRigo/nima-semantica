"""Run, action, review, symbol, and report contracts."""

from __future__ import annotations

from .okf_contracts import GraphIdentity, GraphRevision

from typing import Any, Literal

from pydantic import Field, model_validator

from .evidence_contracts import PromotionProposal
from .models import StrictModel
from .okf_contracts import Digest, GraphIdentifier, OKFReference
from .workflow_contracts import HypothesisComparison, HypothesisProposal


ResearchRunStatus = Literal["running", "paused", "completed", "failed", "cancelled"]


class ProofDependency(StrictModel):
    source_node_id: GraphIdentity
    target_node_id: GraphIdentity
    relation: Literal["proof_depends_on"] = "proof_depends_on"


class ProofDAGContext(StrictModel):
    """A revision-bound project-graph view supplied to one Lean worker."""

    schema_version: Literal[2] = 2
    context_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    target_node_id: GraphIdentity
    node_ids: tuple[GraphIdentity, ...] = Field(min_length=1, max_length=100_000)
    dependencies: tuple[ProofDependency, ...] = Field(default=(), max_length=200_000)
    definition_node_ids: tuple[GraphIdentity, ...] = Field(default=(), max_length=100_000)
    source_references: tuple[OKFReference, ...] = Field(default=(), max_length=10_000)
    formalization_artifact_ids: tuple[Digest, ...] = Field(default=(), max_length=10_000)
    environment_manifest_id: GraphIdentifier | None = None
    status: Literal["proposed", "authorized", "superseded"] = "proposed"

    @model_validator(mode="after")
    def valid_dag_context(self) -> "ProofDAGContext":
        node_ids = set(self.node_ids)
        if len(node_ids) != len(self.node_ids):
            raise ValueError("proof DAG context contains duplicate node IDs")
        if self.target_node_id not in node_ids:
            raise ValueError("proof DAG target is absent from context nodes")
        if not set(self.definition_node_ids) <= node_ids:
            raise ValueError("proof DAG definition is absent from context nodes")
        if len(set(self.definition_node_ids)) != len(self.definition_node_ids):
            raise ValueError("proof DAG context contains duplicate definition IDs")
        adjacency = {node_id: set() for node_id in node_ids}
        for dependency in self.dependencies:
            if dependency.source_node_id not in node_ids or dependency.target_node_id not in node_ids:
                raise ValueError("proof DAG dependency references a node outside the context")
            adjacency[dependency.source_node_id].add(dependency.target_node_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("proof DAG context contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for child in adjacency[node_id]:
                visit(child)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)
        for reference in self.source_references:
            if reference.corpus_id != self.corpus_id or reference.project_id not in (None, self.project_id):
                raise ValueError("proof DAG source reference scope differs from context")
        return self


class ResearchRun(StrictModel):
    schema_version: Literal[2] = 2
    run_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    skill_id: GraphIdentifier
    skill_revision: GraphIdentifier
    objective: str = Field(min_length=1, max_length=20_000)
    status: ResearchRunStatus = "running"
    run_revision: int = Field(default=0, ge=0)
    registry_revision: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    parent_run_id: GraphIdentifier | None = None
    input_artifact_ids: tuple[Digest, ...] = Field(default=(), max_length=10_000)
    output_artifact_ids: tuple[Digest, ...] = Field(default=(), max_length=10_000)
    approval_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    doubt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)


class TaskTransition(StrictModel):
    schema_version: Literal[2] = 2
    transition_id: GraphIdentifier
    run_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    from_state: ResearchRunStatus
    to_state: ResearchRunStatus
    from_run_revision: int = Field(ge=0)
    to_run_revision: int = Field(ge=1)
    actor: GraphIdentifier = "harness"
    timestamp: str | None = Field(default=None, max_length=128)
    action_id: GraphIdentifier | None = None
    reason: str = Field(min_length=1, max_length=4_000)
    input_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    output_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    doubt_or_review: str | None = Field(default=None, max_length=4_000)
    idempotency_key: GraphIdentifier | None = None
    retry_count: int = Field(default=0, ge=0)
    authorization_scope: tuple[GraphIdentifier, ...] = Field(default=(), max_length=64)
    approved: bool = False

    @model_validator(mode="after")
    def revision_advances(self) -> "TaskTransition":
        if self.to_run_revision != self.from_run_revision + 1:
            raise ValueError("task transition must advance the run revision by one")
        return self


class ActionRecord(StrictModel):
    schema_version: Literal[2] = 2
    action_id: GraphIdentifier
    run_id: GraphIdentifier
    corpus_id: GraphIdentifier | None = None
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    source_revision: GraphIdentifier | None = None
    action_kind: GraphIdentifier
    actor: GraphIdentifier
    authority: Literal["observe", "propose", "approve", "commit"]
    input_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    output_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    receipt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    side_effect: Literal[
        "pure_transform", "read_only_retrieval", "proposal_only", "artifact_write", "graph_commit"
    ] = "pure_transform"


class CritiqueFinding(StrictModel):
    schema_version: Literal[2] = 2
    finding_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision | None = None
    source_revision: GraphIdentifier | None = None
    target_id: GraphIdentifier
    kind: Literal["problem", "gap", "inconsistency", "limitation", "reproducibility"]
    statement: str = Field(min_length=1, max_length=20_000)
    evidence_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    unresolved_obligations: tuple[str, ...] = Field(default=(), max_length=1_000)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    status: Literal["proposed", "accepted", "resolved", "rejected"] = "proposed"
    authority: Literal["proposal_only"] = "proposal_only"


class SymbolRegistry(StrictModel):
    schema_version: Literal[2] = 2
    symbol_id: GraphIdentifier
    canonical_name: str = Field(min_length=1, max_length=512)
    aliases: tuple[str, ...] = Field(default=(), max_length=256)
    domain: str | None = Field(default=None, max_length=512)
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    registry_revision: GraphIdentifier | None = None
    source_revision: GraphIdentifier | None = None
    mention_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    confidence: float = Field(ge=0, le=1)
    model_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    conflicts: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    status: Literal["proposed", "accepted", "rejected"] = "proposed"
    authority: Literal["proposal_only"] = "proposal_only"


class ReportManifest(StrictModel):
    schema_version: Literal[2] = 2
    report_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision | None = None
    source_revision: GraphIdentifier | None = None
    template_revision: GraphIdentifier | None = None
    title: str = Field(min_length=1, max_length=2_000)
    artifact_id: Digest
    source_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    finding_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    hypothesis_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    run_id: GraphIdentifier | None = None
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    status: Literal["draft", "reviewed", "published", "superseded"] = "draft"
    authority: Literal["artifact_write"] = "artifact_write"


__all__ = [
    "ActionRecord", "CritiqueFinding", "HypothesisComparison", "HypothesisProposal",
    "ProofDAGContext", "ProofDependency", "PromotionProposal", "ReportManifest", "ResearchRun", "ResearchRunStatus",
    "SymbolRegistry", "TaskTransition",
]
