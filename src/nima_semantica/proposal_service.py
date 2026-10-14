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

    def persist_vocabulary(self, proposal, *, corpus_id, project_id):
        """Append a provisional revision; never merge concepts or admit claims."""
        from .vocabulary_contracts import VocabularyExtension
        from .evidence_contracts import validate_reference
        proposal = VocabularyExtension.model_validate(proposal)
        if (proposal.corpus_id, proposal.project_id) != (corpus_id, project_id):
            raise ConflictError("vocabulary proposal scope mismatch")
        from .vocabulary_context import load_snapshot
        from .vocabulary_contracts import VocabularyPolicy
        matches = [(key, r) for key, r in self.store.records_matching("ArtifactEnvelope", "content.snapshot_hash",
            proposal.base_snapshot_hash, corpus_id=corpus_id, project_id=project_id)
            if r.project_id == project_id and r.content.get("artifact_kind") == "vocabulary_snapshot"]
        if not matches:
            raise ConflictError("vocabulary proposal base snapshot is unavailable")
        snapshot = load_snapshot(self.store, VocabularyPolicy(enabled=True, snapshot_artifact_id=matches[0][1].content["artifact_id"],
            snapshot_hash=proposal.base_snapshot_hash), corpus_id=corpus_id, project_id=project_id, current=False)
        if not set(proposal.related_entry_ids) <= {e.entry_id for e in snapshot.entries}:
            raise ConflictError("vocabulary proposal references unknown entries")
        for ref in proposal.evidence:
            if validate_reference(self.store, ref, corpus_id=corpus_id, project_id=project_id, target_id=proposal.proposal_id):
                raise ConflictError("vocabulary proposal exact evidence is invalid")
        payload = proposal.model_dump(mode="json")
        with self.store.joined_transaction():
            for record_id, record in self.store.records_matching("VocabularyExtension", "proposal_id", proposal.proposal_id,
                    corpus_id=corpus_id, project_id=project_id):
                if record.project_id == project_id and record.content.get("revision") == proposal.revision:
                    if record.content != payload:
                        raise ConflictError("vocabulary proposal revision replay differs")
                    return record_id
            if proposal.supersedes:
                previous = self.store.get(proposal.supersedes, corpus_id=corpus_id, project_id=project_id)
                if previous is None or previous.kind != "VocabularyExtension" or previous.project_id != project_id:
                    raise ConflictError("previous vocabulary proposal is missing or foreign")
                if previous.content["proposal_id"] != proposal.proposal_id or previous.content["revision"] + 1 != proposal.revision:
                    raise ConflictError("vocabulary proposal revision chain differs")
            return self.store.put(Record(kind="VocabularyExtension", corpus_id=corpus_id, project_id=project_id,
                parents=(proposal.supersedes,) if proposal.supersedes else (), content=payload))

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
