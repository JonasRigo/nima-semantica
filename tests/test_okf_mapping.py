from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.okf_contracts import OKFEdge, OKFNode, OKFSnapshot
from nima_semantica.okf_mapping import bundle_to_snapshot, snapshot_to_bundle


def test_snapshot_mapping_preserves_graph_and_writes_readable_relations():
    nodes = (
        OKFNode(
            node_id="claim-1",
            node_type="claim",
            corpus_id="papers",
            project_id="project-a",
            properties={"title": "A candidate bound", "statement": "x >= 0"},
        ),
        OKFNode(
            node_id="evidence-1",
            node_type="evidence",
            corpus_id="papers",
            project_id="project-a",
            properties={"title": "Numerical test"},
        ),
    )
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        corpus_id="papers",
        project_id="project-a",
        nodes=nodes,
        edges=(
            OKFEdge(
                edge_id="edge-1",
                relation="supports",
                source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="claim-1"),
                target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="evidence-1"),
                corpus_id="papers",
                project_id="project-a",
            ),
        ),
    )

    bundle = snapshot_to_bundle(snapshot)
    claim_doc = next(doc for doc in bundle.concepts if doc.concept_id == "nodes/papers/projects/project-a/claim-1")
    restored = bundle_to_snapshot(bundle)

    assert "## Relations" in claim_doc.body
    assert "supports" in claim_doc.body
    assert restored.nodes == snapshot.nodes
    assert restored.edges == snapshot.edges


def test_mapping_rejects_disagreement_between_links_and_relation_metadata():
    node = OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers")
    evidence = OKFNode(node_id="evidence-1", node_type="evidence", corpus_id="papers")
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
        corpus_id="papers",
        nodes=(node, evidence),
        edges=(
            OKFEdge(
                edge_id="edge-1",
                relation="supports",
                source_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="claim-1"),
                target_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="evidence-1"),
                corpus_id="papers",
            ),
        ),
    )
    bundle = snapshot_to_bundle(snapshot)
    claim = bundle.concepts[0].model_copy(
        update={"body": bundle.concepts[0].body.replace("`supports`", "`contradicts`")}
    )
    inconsistent = bundle.model_copy(update={"concepts": (claim, bundle.concepts[1])})

    try:
        bundle_to_snapshot(inconsistent)
    except ValueError as error:
        assert "inconsistent Markdown" in str(error)
    else:
        raise AssertionError("inconsistent relation representations must be rejected")
