"""Framework-independent evidence and provenance service boundary."""

from __future__ import annotations

from typing import Iterable, Literal

from .evidence_contracts import (
    EvidenceValidationIssue,
    EvidenceValidationReport,
    PromotionProposal,
    build_promotion_proposal,
)
from .models import ConflictError, StrictModel
from .okf_contracts import EvidenceReference, OKFDelta, OKFEdge, OKFNode, OKFSnapshot
from .proposal_service import ProposalService


class EvidenceProvenanceResult(StrictModel):
    """Result of preparing a proposal without admitting it to the graph."""

    proposal: PromotionProposal
    record_id: str | None = None
    validation: EvidenceValidationReport
    authority: Literal["proposal_only"] = "proposal_only"


class EvidenceProvenanceService:
    """Validate source bindings, attach evidence, and prepare promotions."""

    stage = "evidence_provenance"

    def __init__(self, store, *, proposals: ProposalService | None = None):
        self.store = store
        self.proposals = proposals or ProposalService(store)

    def validate_reference(
        self,
        reference: EvidenceReference,
        *,
        corpus_id: str,
        project_id: str | None,
        target_id: str,
    ) -> EvidenceValidationReport:
        """Validate scope, immutable bytes, source-region identity, and quotation."""
        from .evidence_contracts import validate_reference
        issues = validate_reference(self.store, reference, corpus_id=corpus_id, project_id=project_id, target_id=target_id)
        return EvidenceValidationReport(valid=not issues, issues=tuple(issues), checked_references=1)

    def validate_snapshot(self, snapshot: OKFSnapshot) -> EvidenceValidationReport:
        from .evidence_contracts import validate_snapshot_evidence
        return validate_snapshot_evidence(self.store, snapshot)

    def validate_delta(self, delta: OKFDelta) -> EvidenceValidationReport:
        from .evidence_contracts import validate_delta_evidence
        return validate_delta_evidence(self.store, delta)

    def bind_node(self, node: OKFNode, evidence: Iterable[EvidenceReference]) -> OKFNode:
        """Return a new node with validated source evidence; storage is unchanged."""
        additions = tuple(evidence)
        report = self._validate_references(
            ((node.node_id, reference) for reference in additions),
            corpus_id=node.corpus_id,
            project_id=node.project_id,
        )
        if not report.valid:
            raise ConflictError("node contains invalid evidence bindings")
        return node.model_copy(update={"evidence": node.evidence + additions})

    def bind_edge(self, edge: OKFEdge, evidence: Iterable[EvidenceReference]) -> OKFEdge:
        """Return a new edge with validated source evidence; storage is unchanged."""
        additions = tuple(evidence)
        report = self._validate_references(
            ((edge.edge_id, reference) for reference in additions),
            corpus_id=edge.corpus_id,
            project_id=edge.project_id,
        )
        if not report.valid:
            raise ConflictError("edge contains invalid evidence bindings")
        return edge.model_copy(update={"evidence": edge.evidence + additions})

    def prepare_promotion(
        self,
        snapshot: OKFSnapshot,
        *,
        proposal_id: str,
        target_corpus_id: str,
        rationale: str,
        persist: bool = True,
        expected_store_revision: str | None = None,
    ) -> EvidenceProvenanceResult:
        """Create and optionally persist an evidence-complete promotion proposal."""
        if expected_store_revision is not None and self.store.revision != expected_store_revision:
            raise ConflictError("evidence promotion request targets a stale store revision")
        if self.store.graph_revision(snapshot.corpus_id, snapshot.project_id) != snapshot.graph_revision:
            raise ConflictError("promotion snapshot is stale")
        validation = self.validate_snapshot(snapshot)
        if not validation.valid:
            raise ConflictError("promotion contains invalid evidence bindings")
        proposal = build_promotion_proposal(
            snapshot,
            proposal_id=proposal_id,
            target_corpus_id=target_corpus_id,
            rationale=rationale,
        )
        record_id = self.proposals.persist_promotion(
            proposal, expected_store_revision=expected_store_revision
        ) if persist else None
        return EvidenceProvenanceResult(
            proposal=proposal, record_id=record_id, validation=validation
        )

    def _validate_references(
        self,
        references: Iterable[tuple[str, EvidenceReference]],
        *,
        corpus_id: str,
        project_id: str | None,
    ) -> EvidenceValidationReport:
        issues: list[EvidenceValidationIssue] = []
        checked = 0
        for target_id, reference in references:
            report = self.validate_reference(
                reference,
                corpus_id=corpus_id,
                project_id=project_id,
                target_id=target_id,
            )
            checked += report.checked_references
            issues.extend(report.issues)
        return EvidenceValidationReport(valid=not issues, issues=tuple(issues), checked_references=checked)


__all__ = ["EvidenceProvenanceResult", "EvidenceProvenanceService"]
