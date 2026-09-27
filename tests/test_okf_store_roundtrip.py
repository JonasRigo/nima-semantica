from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.okf_contracts import OKFDelta, OKFEdge, OKFNode
from nima_semantica.okf_mapping import bundle_to_delta, store_snapshot_to_bundle
from nima_semantica.storage import GraphStore


def test_graph_store_okf_bundle_round_trip_is_proposal_based(tmp_path):
    store = GraphStore(tmp_path)
    try:
        delta = OKFDelta(
            delta_id="delta-roundtrip",
            base_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers",
            project_id="project-a",
            ontology_profile="claim_obligation",
            upsert_nodes=(
                OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers", project_id="project-a"),
                OKFNode(node_id="evidence-1", node_type="obligation", corpus_id="papers", project_id="project-a"),
            ),
            add_edges=(
                OKFEdge(
                    edge_id="edge-1",
                    relation="supports",
                    source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="claim-1"),
                    target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="evidence-1"),
                    corpus_id="papers",
                    project_id="project-a",
                ),
            ),
            reason="Round-trip fixture.",
        )
        commit_okf_delta(
            store,
            delta,
            OKFCommitApproval.for_delta(delta,
                approval_id="approval-roundtrip",
                approved_by="researcher",
                rationale="Approved fixture.",
            ),
        )
        bundle = store_snapshot_to_bundle(store, corpus_id="papers", project_id="project-a")
        proposal = bundle_to_delta(
            bundle,
            delta_id="delta-import",
            base_revision=store.graph_revision("papers", "project-a"),
            reason="Imported OKF proposal.",
        )

        assert {node.node_id for node in proposal.upsert_nodes} == {"claim-1", "evidence-1"}
        assert proposal.add_edges[0].edge_id == "edge-1"
        assert proposal.base_revision == store.graph_revision("papers", "project-a")
    finally:
        store.close()
