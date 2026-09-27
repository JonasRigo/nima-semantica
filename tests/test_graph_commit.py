import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.models import ConflictError
from nima_semantica.okf_contracts import OKFDelta, OKFNode
from nima_semantica.storage import GraphStore
from nima_semantica.graph_persistence import scope_key


def test_approved_delta_commits_atomically_with_receipt(tmp_path):
    store = GraphStore(tmp_path)
    try:
        delta = OKFDelta(
            delta_id="delta-1",
            base_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers",
            project_id="project-a",
            ontology_profile="claim_obligation",
            upsert_nodes=(
                OKFNode(
                    node_id="claim-1",
                    node_type="claim",
                    corpus_id="papers",
                    project_id="project-a",
                    properties={"statement": "x >= 0"},
                ),
            ),
            reason="Approved project claim proposal.",
        )
        result = commit_okf_delta(
            store,
            delta,
            OKFCommitApproval.for_delta(delta,
                approval_id="approval-1",
                approved_by="researcher",
                rationale="Reviewed against the available evidence.",
            ),
        )

        assert result.status == "committed"
        assert result.final_revision != result.base_revision
        assert store._db.execute("SELECT object_id FROM graph_objects").fetchone() == ("claim-1",)
        assert store.get(result.receipt_id).kind == "GraphCommitReceipt"
    finally:
        store.close()


def test_stale_delta_is_rejected_without_commit(tmp_path):
    store = GraphStore(tmp_path)
    try:
        base = store.graph_revision("papers")
        first = OKFDelta(
            delta_id="delta-1",
            base_revision=base,
            corpus_id="papers",
            ontology_profile="claim_obligation",
            upsert_nodes=(OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers"),),
            reason="First proposal.",
        )
        approval = OKFCommitApproval.for_delta(first,
            approval_id="approval-1",
            approved_by="researcher",
            rationale="Approved.",
        )
        commit_okf_delta(store, first, approval)
        stale = first.model_copy(update={"delta_id": "delta-2"})

        with pytest.raises(ConflictError, match="stale graph revision"):
            commit_okf_delta(
                store,
                stale,
                OKFCommitApproval.for_delta(stale, approval_id="approval-2", approved_by="researcher", rationale="Approved"),
            )
        assert store._db.execute("SELECT count(*) FROM graph_objects").fetchone() == (1,)
    finally:
        store.close()


def test_replaying_same_delta_is_idempotent_and_reusing_id_conflicts(tmp_path):
    store = GraphStore(tmp_path)
    try:
        delta = OKFDelta(
            delta_id="delta-replay",
            base_revision=store.graph_revision("papers", None),
            corpus_id="papers",
            ontology_profile="claim_obligation",
            upsert_nodes=(OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers"),),
            reason="Replay test.",
        )
        approval = OKFCommitApproval.for_delta(delta,
            approval_id="approval-replay",
            approved_by="researcher",
            rationale="Approved.",
        )
        first = commit_okf_delta(store, delta, approval)
        second = commit_okf_delta(store, delta, approval)
        assert second == first
        assert store._db.execute("SELECT count(*) FROM graph_commits").fetchone() == (1,)

        with pytest.raises(ConflictError, match="authorize"):
            commit_okf_delta(
                store,
                delta.model_copy(update={"reason": "Different proposal."}),
                approval,
            )
    finally:
        store.close()


def test_unprofiled_delta_cannot_be_committed(tmp_path):
    store = GraphStore(tmp_path)
    try:
        delta = OKFDelta(
            delta_id="delta-unprofiled",
            base_revision=store.graph_revision("papers", None),
            corpus_id="papers",
            upsert_nodes=(OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers"),),
            reason="Missing ontology profile.",
        )
        with pytest.raises(ConflictError, match="ontology profile"):
            commit_okf_delta(
                store,
                delta,
                OKFCommitApproval.for_delta(delta,
                    approval_id="approval-unprofiled",
                    approved_by="researcher",
                    rationale="Should not commit.",
                ),
            )
    finally:
        store.close()


def test_project_commit_does_not_validate_other_project_ontology(tmp_path):
    store = GraphStore(tmp_path)
    try:
        unrelated = OKFNode(node_id="legacy", node_type="attempt", corpus_id="papers",
            project_id="project-a", ontology_profile="0" * 64)
        store._db.execute("INSERT INTO graph_objects VALUES (?,?,?,?)",
            (scope_key("papers", "project-a"), "node", "legacy", unrelated.model_dump_json()))
        delta = OKFDelta(delta_id="project-b-delta", corpus_id="papers", project_id="project-b",
            base_revision=store.graph_revision("papers", "project-b"),
            ontology_profile="claim_obligation",
            upsert_nodes=(OKFNode(node_id="claim-b", node_type="claim", corpus_id="papers",
                project_id="project-b"),), reason="Separate project scope")
        result = commit_okf_delta(store, delta, OKFCommitApproval.for_delta(delta,
            approval_id="project-b-approval", approved_by="tester", rationale="Isolated fixture"))
        assert result.status == "committed"
        assert store.graph_revision("papers", "project-a").project_revision == 0
        assert store.graph_revision("papers", "project-b").project_revision == 1
    finally:
        store.close()
