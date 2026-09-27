import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.okf_contracts import OKFDelta, OKFNode
from nima_semantica.retrieval_contracts import (
    EmbeddingProjectionManifest,
    RetrievalContextPacket,
    RetrievalSeed,
    validate_projection_revision,
)
from nima_semantica.storage import GraphStore


def test_snapshot_read_is_scope_filtered_and_revision_bound(tmp_path):
    store = GraphStore(tmp_path)
    try:
        from test_rewrite_audit_regressions import commit, node
        base = store.graph_revision("papers")
        commit(store, "corpus", upsert_nodes=(node("global-claim"),))
        commit(store, "project", "project-a", upsert_nodes=(node("project-claim", "project-a"),))
        snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="project-a")

        assert [node.node_id for node in snapshot.nodes] == ["global-claim", "project-claim"]
        assert store.read_okf_snapshot(corpus_id="papers").nodes[0].node_id == "global-claim"
        assert store.read_okf_snapshot(corpus_id="papers", revision=base).nodes == ()
    finally:
        store.close()


def test_retrieval_packet_rejects_scope_and_stale_projection():
    projection = EmbeddingProjectionManifest(
        projection_id="projection-1",
        corpus_id="papers",
        project_id="project-a",
        source_revision="sqlite:4",
        provider="local",
        model="embedding-model",
        model_revision="v1",
        dimension=3,
        indexed_artifact_ids=("region-1",),
    )
    packet = RetrievalContextPacket(
        query="find evidence",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        projection=projection,
        seeds=(RetrievalSeed(record_id="region-1", score=0.9, source="vector"),),
        selected_record_ids=("region-1",),
    )

    assert packet.model_dump(mode="json")["projection"]["model"] == "embedding-model"
    validate_projection_revision(projection, corpus_id="papers", project_id="project-a", source_revision="sqlite:4")
    with pytest.raises(ValueError, match="stale"):
        validate_projection_revision(projection, corpus_id="papers", project_id="project-a", source_revision="sqlite:5")
