"""Framework-independent service boundary for the canonical OKF graph."""

from __future__ import annotations

from pathlib import Path

from .graph_commit import OKFCommitApproval, OKFCommitResult, commit_okf_delta
from .models import ConflictError
from .okf_contracts import GraphRevision, OKFDelta, OKFSnapshot
from .okf_io import OKFBundle, export_bundle, import_bundle
from .okf_mapping import bundle_to_delta, bundle_to_snapshot, snapshot_to_bundle
from .ontology_services import OntologyService


class GraphService:
    """Own graph reads, approved writes, and OKF interchange over one store."""

    def __init__(self, store, *, ontology: OntologyService | None = None):
        self.store = store
        self.ontology = ontology or OntologyService()

    def revision(self, corpus_id: str, project_id: str | None = None) -> GraphRevision:
        return self.store.graph_revision(corpus_id, project_id)

    def read_snapshot(
        self,
        *,
        corpus_id: str,
        project_id: str | None = None,
        revision: GraphRevision | None = None,
    ) -> OKFSnapshot:
        snapshot = self.store.read_okf_snapshot(
            corpus_id=corpus_id, project_id=project_id, revision=revision
        )
        if snapshot.ontology_profile is not None:
            report = self.ontology.validate_snapshot(snapshot)
            if any(issue.severity == "error" and issue.code not in {"ontology.required_node_type_missing", "ontology.required_relation_missing"} for issue in report.issues):
                raise ConflictError("stored OKF snapshot is incompatible with its ontology profile")
        return snapshot

    def validate_snapshot(
        self, snapshot: OKFSnapshot, *, profile: str | None = None
    ):
        return self.ontology.validate_snapshot(snapshot, profile=profile)

    def commit_delta(
        self, delta: OKFDelta, approval: OKFCommitApproval
    ) -> OKFCommitResult:
        return commit_okf_delta(self.store, delta, approval, ontology=self.ontology)

    def export_bundle(
        self, *, corpus_id: str, project_id: str | None = None
    ) -> OKFBundle:
        return snapshot_to_bundle(
            self.read_snapshot(corpus_id=corpus_id, project_id=project_id)
        )

    def export_bundle_directory(
        self, directory: str | Path, *, corpus_id: str, project_id: str | None = None
    ) -> OKFBundle:
        bundle = self.export_bundle(corpus_id=corpus_id, project_id=project_id)
        export_bundle(bundle, directory)
        return bundle

    def import_bundle(self, directory: str | Path) -> OKFBundle:
        """Read an OKF bundle without mutating the graph store."""
        return import_bundle(directory)

    def prepare_bundle_delta(
        self,
        bundle: OKFBundle,
        *,
        delta_id: str,
        reason: str,
        base_revision: GraphRevision | None = None,
    ) -> OKFDelta:
        source = bundle_to_snapshot(bundle)
        current = self.store.read_okf_snapshot(corpus_id=source.corpus_id, project_id=source.project_id)
        delta = bundle_to_delta(
            bundle,
            delta_id=delta_id,
            base_revision=base_revision or current.graph_revision,
            current_snapshot=current,
            reason=reason,
        )
        report = self.ontology.validate_delta(delta)
        if not report.valid:
            raise ConflictError("OKF bundle is incompatible with its ontology profile")
        return delta


__all__ = ["GraphService"]
