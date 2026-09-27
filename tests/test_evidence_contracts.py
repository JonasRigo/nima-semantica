import hashlib

import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.evidence_contracts import (
    PromotionApproval,
    build_promotion_proposal,
    commit_promotion,
    validate_snapshot_evidence,
)
from nima_semantica.okf_contracts import EvidenceReference, OKFNode, OKFSnapshot
from nima_semantica.storage import GraphStore
from nima_semantica.models import Record, identity


def evidence(store, *, text="The claim is stated here."):
    artifact_id = store.artifact(text.encode())
    from conftest import seed_region
    region = seed_region(store, text)
    return EvidenceReference(
        corpus_id="papers",
        artifact_id=artifact_id,
        region_id=region.id,
        source_revision=artifact_id,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        quotation=text,
    )


def test_source_evidence_is_validated_against_artifact_and_region(tmp_path):
    store = GraphStore(tmp_path)
    try:
        reference = evidence(store)
        snapshot = OKFSnapshot(
            snapshot_id="snapshot-1",
            graph_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers",
            project_id="project-a",
            ontology_profile="claim_obligation",
            nodes=(
                OKFNode(
                    node_id="claim-1",
                    node_type="claim",
                    corpus_id="papers",
                    project_id="project-a",
                    evidence=(reference,),
                ),
            ),
        )

        report = validate_snapshot_evidence(store, snapshot)

        assert report.valid is True
        assert report.checked_references == 1
    finally:
        store.close()


def test_stale_source_revision_is_rejected(tmp_path):
    store = GraphStore(tmp_path)
    try:
        original = evidence(store)
        replacement_artifact = store.artifact(b"A newer source revision.")
        reference = original.model_copy(
            update={
                "artifact_id": replacement_artifact,
                "source_revision": replacement_artifact,
                "content_hash": replacement_artifact,
            }
        )
        snapshot = OKFSnapshot(
            snapshot_id="snapshot-1",
            graph_revision=store.graph_revision("papers", None),
            corpus_id="papers",
            nodes=(OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers", evidence=(reference,)),),
        )

        report = validate_snapshot_evidence(store, snapshot)

        assert report.valid is False
        assert report.issues[0].code == "evidence.revision_mismatch"
    finally:
        store.close()


def test_promotion_requires_project_scope_and_evidence(tmp_path):
    store = GraphStore(tmp_path)
    try:
        reference = evidence(store)
        snapshot = OKFSnapshot(
            snapshot_id="snapshot-project",
            graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
            corpus_id="papers",
            project_id="project-a",
            nodes=(OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers", project_id="project-a", evidence=(reference,)),),
        )
        proposal = build_promotion_proposal(
            snapshot,
            proposal_id="promotion-1",
            target_corpus_id="papers",
            rationale="Evidence is reusable beyond this project.",
        )
        assert proposal.status == "proposed"
        assert proposal.source_project_id == "project-a"

        with pytest.raises(ValueError, match="project-scoped"):
            build_promotion_proposal(
                snapshot.model_copy(update={"project_id": None}),
                proposal_id="promotion-2",
                target_corpus_id="papers",
                rationale="Invalid.",
            )
    finally:
        store.close()


def test_approved_promotion_creates_corpus_record_with_origin(tmp_path):
    store = GraphStore(tmp_path)
    try:
        reference = evidence(store)
        snapshot = OKFSnapshot(
            snapshot_id="snapshot-project",
            graph_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers",
            project_id="project-a",
            ontology_profile="claim_obligation",
            nodes=(
                OKFNode(
                    node_id="claim-1",
                    node_type="claim",
                    corpus_id="papers",
                    project_id="project-a",
                    evidence=(reference,),
                ),
            ),
        )
        from test_rewrite_audit_regressions import commit
        commit(store, "seed-promotion", "project-a", upsert_nodes=snapshot.nodes)
        snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="project-a")
        proposal = build_promotion_proposal(
            snapshot,
            proposal_id="promotion-1",
            target_corpus_id="papers",
            rationale="Promote this source-grounded finding for reuse.",
        )

        result = commit_promotion(
            store,
            snapshot,
            proposal,
            PromotionApproval(
                proposal_hash=identity(proposal),
                proposal_id=proposal.proposal_id,
                approval_id="promotion-approval-1",
                approved_by="researcher",
                rationale="Reviewed provenance and reuse scope.",
            ),
        )
        promoted = store.read_okf_snapshot(corpus_id="papers").nodes[0]

        assert result.status == "committed"
        assert promoted.project_id is None
        assert promoted.promotion_origin.proposal_id == proposal.proposal_id
        assert promoted.promotion_origin.source_project_id == "project-a"
    finally:
        store.close()
