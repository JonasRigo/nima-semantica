"""Proposal-only persistence for project research outputs."""

from __future__ import annotations

from .okf_contracts import GraphRevision

from typing import Any

from .artifact_contracts import GraphArtifact
from .evidence_contracts import PromotionProposal
from .models import ConflictError, Record
from .research_contracts import CritiqueFinding, ReportManifest
from .storage import GraphStore
from .workflow_contracts import HypothesisComparison, HypothesisProposal


class ProposalService:
    """Persist proposals without admitting or mutating the project graph."""

    _KINDS = {
        HypothesisProposal: "HypothesisProposal",
        HypothesisComparison: "HypothesisComparison",
        CritiqueFinding: "CritiqueFinding",
        PromotionProposal: "PromotionProposal",
        ReportManifest: "ReportManifest",
    }

    def __init__(self, store: GraphStore):
        self.store = store

    def persist_hypothesis(
        self,
        proposal: HypothesisProposal,
        *,
        corpus_id: str,
        project_id: str,
        graph_revision: GraphRevision,
        expected_store_revision: str | None = None,
    ) -> str:
        scoped = proposal.model_copy(update={
            "corpus_id": self._scope_value(proposal.corpus_id, corpus_id, "corpus"),
            "project_id": self._scope_value(proposal.project_id, project_id, "project"),
            "graph_revision": self._scope_value(proposal.graph_revision, graph_revision, "graph"),
        })
        return self._persist(scoped, corpus_id=corpus_id, project_id=project_id,
                             expected_store_revision=expected_store_revision)

    def persist_comparison(
        self,
        comparison: HypothesisComparison,
        *,
        corpus_id: str,
        project_id: str,
        graph_revision: GraphRevision,
        expected_store_revision: str | None = None,
    ) -> str:
        scoped = comparison.model_copy(update={
            "corpus_id": self._scope_value(comparison.corpus_id, corpus_id, "corpus"),
            "project_id": self._scope_value(comparison.project_id, project_id, "project"),
            "graph_revision": self._scope_value(comparison.graph_revision, graph_revision, "graph"),
        })
        return self._persist(scoped, corpus_id=corpus_id, project_id=project_id,
                             expected_store_revision=expected_store_revision)

    def persist_finding(
        self,
        finding: CritiqueFinding,
        *,
        expected_store_revision: str | None = None,
    ) -> str:
        return self._persist(finding, corpus_id=finding.corpus_id,
                             project_id=finding.project_id,
                             expected_store_revision=expected_store_revision)

    def persist_promotion(
        self,
        proposal: PromotionProposal,
        *,
        expected_store_revision: str | None = None,
    ) -> str:
        # The record lives in the source project scope while the proposal's
        # target_corpus_id remains explicit in its content. No corpus mutation
        # occurs here; approved promotion is handled by graph commit.
        return self._persist(proposal, corpus_id=proposal.target_corpus_id,
                             project_id=proposal.source_project_id,
                             expected_store_revision=expected_store_revision)

    def persist_report(
        self,
        report: ReportManifest,
        *,
        expected_store_revision: str | None = None,
    ) -> str:
        return self._persist(report, corpus_id=report.corpus_id,
                             project_id=report.project_id,
                             expected_store_revision=expected_store_revision)

    def persist_graph_candidate(
        self,
        artifact: GraphArtifact,
        *,
        source_region_ids: tuple[str, ...],
        expected_store_revision: str | None = None,
    ) -> str:
        """Persist a graph artifact as a proposal without graph admission."""
        if artifact.delta is None or artifact.envelope.project_id is None:
            raise ValueError("graph candidate proposals require a project-scoped OKF delta")
        payload = {
            "artifact": artifact.model_dump(mode="json"),
            "source_region_ids": list(source_region_ids),
            "status": "proposed",
            "authority": "proposal_only",
        }
        with self.store.joined_transaction(expected_store_revision):
            for record_id, existing in self.store.records(
                "GraphArtifactProposal", corpus_id=artifact.envelope.corpus_id
            ):
                if existing.project_id != artifact.envelope.project_id:
                    continue
                if existing.content.get("artifact", {}).get("envelope", {}).get("artifact_id") != artifact.envelope.artifact_id:
                    continue
                if existing.content != payload:
                    raise ConflictError("graph candidate artifact ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind="GraphArtifactProposal", corpus_id=artifact.envelope.corpus_id,
                project_id=artifact.envelope.project_id, parents=source_region_ids,
                content=payload,
            ))

    def get(self, kind: str, logical_id: str, *, corpus_id: str, project_id: str) -> dict[str, Any] | None:
        for record_id, record in self.store.records(kind, corpus_id=corpus_id):
            if record.project_id == project_id and record.content.get(self._id_field(kind)) == logical_id:
                return {"record_id": record_id, "content": record.content}
        return None

    def _persist(
        self,
        model,
        *,
        corpus_id: str,
        project_id: str,
        expected_store_revision: str | None,
    ) -> str:
        kind = self._KINDS[type(model)]
        payload = model.model_dump(mode="json")
        logical_id = payload[self._id_field(kind)]
        with self.store.joined_transaction(expected_store_revision):
            for record_id, existing in self.store.records(kind, corpus_id=corpus_id):
                if existing.project_id != project_id or existing.content.get(self._id_field(kind)) != logical_id:
                    continue
                if existing.content != payload:
                    raise ConflictError(f"{kind} ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind=kind, corpus_id=corpus_id, project_id=project_id, content=payload,
            ))

    @staticmethod
    def _id_field(kind: str) -> str:
        return {
            "HypothesisProposal": "proposal_id",
            "HypothesisComparison": "proposal_id",
            "CritiqueFinding": "finding_id",
            "PromotionProposal": "proposal_id",
            "ReportManifest": "report_id",
        }[kind]

    @staticmethod
    def _scope_value(existing: str | None, expected: str, label: str) -> str:
        if existing is not None and existing != expected:
            raise ConflictError(f"proposal {label} scope differs from persistence scope")
        return expected


__all__ = ["ProposalService"]
