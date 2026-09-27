from pydantic import ValidationError
import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.okf_contracts import OKFDelta, OKFEdge, OKFNode, OKFSnapshot


def node(node_id, *, project_id=None):
    return OKFNode(
        node_id=node_id,
        node_type="claim",
        corpus_id="papers",
        project_id=project_id,
        properties={"statement": node_id},
    )


def edge(edge_id, source_id, target_id, *, project_id=None):
    return OKFEdge(
        edge_id=edge_id,
        relation="supports",
        source_id=GraphIdentity(corpus_id="papers", project_id=project_id, local_id=source_id),
        target_id=GraphIdentity(corpus_id="papers", project_id=None, local_id=target_id),
        corpus_id="papers",
        project_id=project_id,
    )


def test_snapshot_is_revision_bound_and_validates_endpoints_and_scope():
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        corpus_id="papers",
        project_id="project-a",
        nodes=(node("claim", project_id="project-a"), node("evidence")),
        edges=(edge("support", "claim", "evidence", project_id="project-a"),),
    )

    assert snapshot.graph_revision .corpus_revision == 0
    assert snapshot.model_dump(mode="json")["nodes"][0]["node_id"] == "claim"

    with pytest.raises(ValidationError, match="absent node"):
        OKFSnapshot(
            snapshot_id="snapshot-2",
            graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
            corpus_id="papers",
            nodes=(node("claim"),),
            edges=(edge("support", "claim", "missing"),),
        )


def test_delta_is_proposal_data_and_is_bound_to_one_base_revision():
    delta = OKFDelta(
        delta_id="delta-1",
        base_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        corpus_id="papers",
        project_id="project-a",
        upsert_nodes=(node("claim", project_id="project-a"),),
        add_edges=(edge("support", "claim", "evidence", project_id="project-a"),),
        reason="Record a reviewed project proposal.",
    )

    assert delta.base_revision .corpus_revision == 0

    with pytest.raises(ValidationError, match="upserted and removed"):
        OKFDelta(
            delta_id="delta-2",
            base_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
            corpus_id="papers",
            upsert_nodes=(node("claim"),),
            remove_node_ids=(GraphIdentity(corpus_id="papers", local_id="claim"),),
            reason="Invalid replacement.",
        )


def test_delta_rejects_cross_scope_changes():
    with pytest.raises(ValidationError, match="scope"):
        OKFDelta(
            delta_id="delta-3",
            base_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
            corpus_id="papers",
            upsert_nodes=(
                OKFNode(node_id="claim", node_type="claim", corpus_id="other-corpus"),
            ),
            reason="Invalid scope.",
        )
