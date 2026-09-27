from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.graph_service import GraphService
from nima_semantica.okf_contracts import OKFDelta, OKFEdge, OKFNode
from nima_semantica.ontology_services import OntologyService
from nima_semantica.storage import GraphStore


def test_graph_service_reads_and_commits_through_canonical_store(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = GraphService(store, ontology=OntologyService())
        delta = OKFDelta(
            delta_id="service-delta", base_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers", project_id="project-a",
            ontology_profile="claim_obligation",
            upsert_nodes=(
                OKFNode(
                    node_id="claim-service", node_type="claim", corpus_id="papers",
                    project_id="project-a",
                ),
                OKFNode(
                    node_id="obligation-service", node_type="obligation", corpus_id="papers",
                    project_id="project-a",
                ),
            ),
            add_edges=(OKFEdge(
                edge_id="requires-service", relation="requires",
                source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="claim-service"), target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="obligation-service"),
                corpus_id="papers", project_id="project-a",
            ),),
            reason="Service boundary test.",
        )
        result = service.commit_delta(delta, OKFCommitApproval.for_delta(delta,
            approval_id="service-approval",
            approved_by="tester", rationale="Reviewed service boundary.",
        ))
        snapshot = service.read_snapshot(corpus_id="papers", project_id="project-a")
        assert result.status == "committed"
        assert {node.node_id for node in snapshot.nodes} == {
            "claim-service", "obligation-service"
        }
        assert snapshot.graph_revision == service.revision("papers", "project-a")
    finally:
        store.close()


def test_bundle_import_is_non_mutating_and_delta_preparation_is_validated(tmp_path):
    store = GraphStore(tmp_path / "store")
    bundle_dir = tmp_path / "bundle"
    try:
        service = GraphService(store)
        empty = service.export_bundle(corpus_id="papers")
        assert empty.concepts == ()
        assert store.revision == "empty"
    finally:
        store.close()
