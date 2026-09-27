"""Project-graph proof-DAG context construction and persistence."""

from __future__ import annotations

from .models import ConflictError, Record, canonical, identity
from .okf_contracts import GraphIdentity, OKFSnapshot
from .research_contracts import ProofDAGContext


PROOF_NODE_TYPES = frozenset({"proof_target", "proof_node", "lean_proof_target"})
PROOF_RELATION = "proof_depends_on"
CONTEXT_KIND = "ProofDAGContext"


def build_proof_dag_context(
    snapshot: OKFSnapshot,
    *,
    context_id: str,
    target_node_id: GraphIdentity,
    source_references=(),
    formalization_artifact_ids=(),
    environment_manifest_id: str | None = None,
    status: str = "proposed",
) -> ProofDAGContext:
    """Build the target's backward dependency context from a project snapshot."""
    if snapshot.project_id is None:
        raise ValueError("proof DAG contexts require a project-scoped snapshot")
    nodes = {node.ref: node for node in snapshot.nodes}
    target = nodes.get(target_node_id)
    if target is None:
        raise ValueError("proof DAG target is absent from the project snapshot")
    if target.node_type not in PROOF_NODE_TYPES:
        raise ValueError("proof DAG target is not a registered proof node")
    edges = [
        edge for edge in snapshot.edges
        if edge.relation == PROOF_RELATION
        and edge.source_id in nodes
        and edge.target_id in nodes
    ]
    incoming = {}
    for edge in edges:
        incoming.setdefault(edge.target_id, []).append(edge.source_id)
    selected = {target_node_id}
    stack = [target_node_id]
    while stack:
        current = stack.pop()
        for dependency in incoming.get(current, ()):
            if dependency not in selected:
                selected.add(dependency)
                stack.append(dependency)
    dependencies = tuple(
        {"source_node_id": edge.source_id, "target_node_id": edge.target_id}
        for edge in edges
        if edge.source_id in selected and edge.target_id in selected
    )
    definitions = tuple(
        node_id for node_id in sorted(selected, key=canonical)
        if nodes[node_id].node_type == "proof_definition"
    )
    return ProofDAGContext(
        context_id=context_id,
        corpus_id=snapshot.corpus_id,
        project_id=snapshot.project_id,
        graph_revision=snapshot.graph_revision,
        target_node_id=target_node_id,
        node_ids=tuple(sorted(selected, key=canonical)),
        dependencies=dependencies,
        definition_node_ids=definitions,
        source_references=tuple(source_references),
        formalization_artifact_ids=tuple(formalization_artifact_ids),
        environment_manifest_id=environment_manifest_id,
        status=status,
    )


def persist_proof_dag_context(store, context: ProofDAGContext) -> str:
    """Persist one project-scoped context record without graph admission."""
    payload = context.model_dump(mode="json")
    with store.transaction():
        for record_id, record in store.records(CONTEXT_KIND, corpus_id=context.corpus_id):
            if record.project_id != context.project_id or record.content.get("context_id") != context.context_id:
                continue
            if record.content != payload:
                raise ConflictError("proof DAG context ID is already bound to different metadata")
            return record_id
        return store.put(Record(
            kind=CONTEXT_KIND,
            corpus_id=context.corpus_id,
            project_id=context.project_id,
            parents=tuple(identity(ref) for ref in context.node_ids),
            content=payload,
        ))


__all__ = ["CONTEXT_KIND", "PROOF_NODE_TYPES", "PROOF_RELATION", "build_proof_dag_context", "persist_proof_dag_context"]
