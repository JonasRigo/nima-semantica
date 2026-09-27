"""Atomic scope, evidence, ontology, approval and history boundary."""
from typing import Literal
from pydantic import Field

from .models import ConflictError, StrictModel, identity, now, Record
from .okf_contracts import Digest, GraphIdentifier, GraphRevision, OKFDelta
from .ontology_services import OntologyService
from .evidence_contracts import validate_delta_evidence


class OKFCommitApproval(StrictModel):
    schema_version: Literal[2] = 2
    approval_id: GraphIdentifier
    delta_id: GraphIdentifier
    delta_hash: Digest
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    base_revision: GraphRevision
    approved_by: GraphIdentifier
    rationale: str = Field(min_length=1, max_length=4000)
    approved_at: str = Field(default_factory=now)

    @classmethod
    def for_delta(cls, delta, **metadata):
        return cls(delta_id=delta.delta_id, delta_hash=identity(delta), corpus_id=delta.corpus_id,
                   project_id=delta.project_id, base_revision=delta.base_revision, **metadata)


class OKFCommitResult(StrictModel):
    status: Literal["committed"] = "committed"
    delta_id: GraphIdentifier
    approval_id: GraphIdentifier
    receipt_id: str
    base_revision: GraphRevision
    final_revision: GraphRevision


def commit_okf_delta(store, delta, approval, *, ontology=None):
    if not isinstance(approval, OKFCommitApproval):
        raise ConflictError("an explicit scoped approval is required")
    delta = OKFDelta.model_validate(delta.model_dump(mode="json"))
    approval = OKFCommitApproval.model_validate(approval.model_dump(mode="json"))
    if (approval.delta_id != delta.delta_id or approval.delta_hash != identity(delta)
        or (approval.corpus_id, approval.project_id) != (delta.corpus_id, delta.project_id)
        or approval.base_revision != delta.base_revision or not approval.rationale.strip()):
        raise ConflictError("approval does not authorize the exact scoped delta")
    ontology = ontology or OntologyService()
    with store.joined_transaction():
        previous = store.get_okf_operation(delta.delta_id, corpus_id=delta.corpus_id, project_id=delta.project_id)
        if previous is not None:
            if previous["delta_hash"] != identity(delta) or previous["approval"] != approval.model_dump(mode="json"):
                raise ConflictError("divergent graph operation replay")
            return OKFCommitResult.model_validate(previous["result"])
        if store.graph_revision(delta.corpus_id, delta.project_id) != delta.base_revision:
            raise ConflictError("stale graph revision")
        if delta.ontology_profile is None:
            raise ConflictError("graph commit requires an ontology profile")
        profile = ontology.resolve(delta.ontology_profile)
        bound = delta.model_copy(update={
            "ontology_profile": profile.digest,
            "upsert_nodes": tuple(n.model_copy(update={"ontology_profile": profile.digest}) for n in delta.upsert_nodes),
            "add_edges": tuple(e.model_copy(update={"ontology_profile": profile.digest}) for e in delta.add_edges),
        })
        for item in (*delta.upsert_nodes, *delta.add_edges):
            if item.ontology_profile is not None and ontology.resolve(item.ontology_profile).digest != profile.digest:
                raise ConflictError("object ontology differs from delta ontology")
        nodes, edges = store.prospective_graph(bound)
        for node in nodes.values():
            if node.corpus_id != delta.corpus_id or node.project_id not in (None, delta.project_id):
                continue
            selected = ontology.resolve(node.ontology_profile)
            if node.node_type.casefold() not in {t.name.casefold() for t in selected.node_types}:
                raise ConflictError("node type is outside its ontology")
        for edge in edges.values():
            if edge.corpus_id != delta.corpus_id or edge.project_id not in (None, delta.project_id):
                continue
            selected = ontology.resolve(edge.ontology_profile)
            relation = next((r for r in selected.relation_types if r.name.casefold() == edge.relation.casefold()), None)
            if (relation is None or nodes[edge.source_id].node_type.casefold() not in {t.casefold() for t in relation.source_types}
                or nodes[edge.target_id].node_type.casefold() not in {t.casefold() for t in relation.target_types}):
                raise ConflictError("edge endpoint types violate ontology")
            if any(nodes[ref].ontology_profile != selected.digest for ref in (edge.source_id, edge.target_id)):
                raise ConflictError("cross-profile edge requires explicit ontology alignment")
        if not validate_delta_evidence(store, bound).valid:
            raise ConflictError("graph delta contains invalid evidence")
        final = delta.base_revision.model_copy(update={
            "corpus_revision" if delta.project_id is None else "project_revision":
            (delta.base_revision.corpus_revision if delta.project_id is None else delta.base_revision.project_revision) + 1})
        record = Record(kind="GraphCommitReceipt", corpus_id=delta.corpus_id, project_id=delta.project_id,
                        content={"delta_hash": approval.delta_hash, "approval": approval.model_dump(mode="json"),
                                 "base_revision": delta.base_revision.model_dump(mode="json"),
                                 "final_revision": final.model_dump(mode="json")})
        receipt_id = store.put(record)
        result = OKFCommitResult(delta_id=delta.delta_id, approval_id=approval.approval_id,
                                 receipt_id=receipt_id, base_revision=delta.base_revision, final_revision=final)
        store.persist_graph_commit(bound, approval, receipt_id, result, delta)
        return result
