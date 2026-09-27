import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.models import ConflictError
from nima_semantica.okf_contracts import OKFEdge, OKFNode, OKFSnapshot
from nima_semantica.proof_dag import build_proof_dag_context, persist_proof_dag_context
from nima_semantica.storage import GraphStore


def snapshot():
    nodes = tuple(OKFNode(
        node_id=node_id, node_type=node_type, corpus_id="papers", project_id="project-a",
    ) for node_id, node_type in (
        ("target", "proof_target"),
        ("helper", "proof_node"),
        ("definition", "proof_definition"),
    ))
    edges = (
        OKFEdge(
            edge_id="edge-helper-target", relation="proof_depends_on",
            source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="helper"), target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"), corpus_id="papers", project_id="project-a",
        ),
        OKFEdge(
            edge_id="edge-definition-helper", relation="proof_depends_on",
            source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="definition"), target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="helper"), corpus_id="papers", project_id="project-a",
        ),
    )
    return OKFSnapshot(
        snapshot_id="snapshot-1", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0), corpus_id="papers",
        project_id="project-a", nodes=nodes, edges=edges,
    )


def test_context_selects_target_dependencies_and_persists_in_project_scope(tmp_path):
    context = build_proof_dag_context(snapshot(), context_id="context-1", target_node_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"))
    assert tuple(ref.local_id for ref in context.node_ids) == ("definition", "helper", "target")
    assert tuple(ref.local_id for ref in context.definition_node_ids) == ("definition",)
    store = GraphStore(tmp_path)
    try:
        record_id = persist_proof_dag_context(store, context)
        assert persist_proof_dag_context(store, context) == record_id
        stored = store.get(record_id, corpus_id="papers", project_id="project-a")
        assert stored is not None and stored.kind == "ProofDAGContext"
        context_records = store.records("ProofDAGContext", corpus_id="papers")
        assert len(context_records) == 1
        assert context_records[0][1].project_id == "project-a"
    finally:
        store.close()


def test_context_rejects_cycles_and_divergent_replay(tmp_path):
    with pytest.raises(ValueError, match="cycle"):
        build_proof_dag_context(
            snapshot().model_copy(update={"edges": (
                OKFEdge(
                    edge_id="cycle-a", relation="proof_depends_on", source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"),
                    target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="helper"), corpus_id="papers", project_id="project-a",
                ),
                OKFEdge(
                    edge_id="cycle-b", relation="proof_depends_on", source_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="helper"),
                    target_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"), corpus_id="papers", project_id="project-a",
                ),
            )}),
            context_id="context-cycle", target_node_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"),
        )
    context = build_proof_dag_context(snapshot(), context_id="context-1", target_node_id=GraphIdentity(corpus_id="papers", project_id="project-a", local_id="target"))
    store = GraphStore(tmp_path)
    try:
        persist_proof_dag_context(store, context)
        with pytest.raises(ConflictError, match="different metadata"):
            persist_proof_dag_context(store, context.model_copy(update={"status": "authorized"}))
    finally:
        store.close()
