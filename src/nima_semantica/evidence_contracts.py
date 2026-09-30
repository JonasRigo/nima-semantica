"""Evidence binding and project-to-corpus promotion contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from .models import ConflictError, StrictModel, identity, now
from .source_quality import evidence_locator, preparation_quality, provisional_object, provisional_nodes, PROVISIONAL
from .okf_contracts import Digest, EvidenceReference, GraphIdentifier, GraphRevision, OKFDelta, OKFEdge, OKFNode, OKFSnapshot, PromotionOrigin

if TYPE_CHECKING:
    from .graph_commit import OKFCommitResult


class EvidenceValidationIssue(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    message: str = Field(min_length=1, max_length=2_000)
    target_id: GraphIdentifier
    evidence_id: GraphIdentifier | None = None


class EvidenceValidationReport(StrictModel):
    valid: bool
    issues: tuple[EvidenceValidationIssue, ...] = ()
    checked_references: int = Field(ge=0)


class PromotionProposal(StrictModel):
    """An auditable request to copy project knowledge into corpus scope."""

    schema_version: Literal[2] = 2
    proposal_id: GraphIdentifier
    source_project_id: GraphIdentifier
    target_corpus_id: GraphIdentifier
    source_snapshot_id: GraphIdentifier
    source_graph_revision: GraphRevision
    node_ids: tuple[GraphIdentifier, ...] = Field(min_length=1, max_length=100_000)
    edge_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=200_000)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=100_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    status: Literal["proposed"] = "proposed"
    approval_required: Literal[True] = True


class PromotionApproval(StrictModel):
    proposal_hash: Digest
    approved_at: str = Field(default_factory=now)
    proposal_id: GraphIdentifier
    approval_id: GraphIdentifier
    approved_by: GraphIdentifier
    rationale: str = Field(min_length=1, max_length=4_000)


def validate_reference(store, reference, *, corpus_id, project_id, target_id):
    """The single exact-source validator used by admission and evidence services."""
    import hashlib
    from .source_corpus import SourceRegion, SourceCorpusService

    def failure(code, message):
        return [EvidenceValidationIssue(code=code, message=message, target_id=target_id, evidence_id=reference.region_id)]

    if reference.corpus_id != corpus_id or reference.project_id not in (None, project_id):
        return failure("evidence.scope_mismatch", "Evidence is outside the owning graph scope.")
    record = store.get(reference.region_id, corpus_id=corpus_id)
    if record is None or record.kind != "SourceRegion":
        return failure("evidence.region_missing", "Exact source region is unavailable.")
    if record.project_id not in (None, project_id) or record.project_id != reference.project_id:
        return failure("evidence.scope_mismatch", "Stored region scope differs from reference.")
    try:
        region = SourceRegion.model_validate(record.content)
        if preparation_quality(region) == PROVISIONAL and reference.locator.get("preparation_quality") != PROVISIONAL:
            return failure("evidence.quality_label_missing", "Fast evidence must retain its provisional preparation label.")
        source = SourceCorpusService(store).get_source(region.source_id, corpus_id=corpus_id, project_id=project_id)
        if source is None or source.source_revision != region.source_revision or source.artifact_id != (region.source_artifact_id or region.artifact_id):
            return failure("evidence.source_mismatch", "Region does not bind the registered source revision.")
        if hashlib.sha256(store.read_artifact(source.artifact_id)).hexdigest() != source.artifact_id:
            return failure("evidence.hash_mismatch", "Original source bytes have changed.")
        if region.artifact_id != source.artifact_id:
            from .artifact_service import ArtifactService
            normalized = ArtifactService(store).resolve(region.artifact_id, corpus_id=corpus_id, project_id=region.project_id)
            if source.artifact_id not in normalized.source_artifact_ids or normalized.source_revision != source.source_revision:
                return failure("evidence.source_mismatch", "Normalized artifact lacks its original source binding.")
        if region.corpus_id != corpus_id or region.project_id != record.project_id:
            return failure("evidence.scope_mismatch", "Source region payload scope differs.")
        data = store.read_artifact(reference.artifact_id)
        if hashlib.sha256(data).hexdigest() != reference.content_hash:
            return failure("evidence.hash_mismatch", "Immutable source bytes have changed.")
        if region.artifact_id != reference.artifact_id or region.source_revision != reference.source_revision:
            return failure("evidence.revision_mismatch", "Region belongs to another source revision.")
        text = data.decode("utf-8")
        if not (0 <= region.start < region.end <= len(text)) or text[region.start:region.end] != region.text:
            return failure("evidence.region_mismatch", "Source region does not match immutable bytes.")
        if reference.quotation is not None and reference.quotation != region.text:
            return failure("evidence.quotation_mismatch", "Quotation differs from exact source region.")
        for key, value in reference.locator.items():
            if key not in {"start", "end", "ordinal", *region.metadata}:
                return failure("evidence.locator_mismatch", "Unsupported source locator.")
            actual = getattr(region, key) if key in {"start", "end", "ordinal"} else region.metadata[key]
            if value != actual:
                return failure("evidence.locator_mismatch", "Locator differs from source region.")
    except Exception:
        return failure("evidence.invalid_region", "Source region or artifact could not be validated.")
    return []


def require_source_region(store, region_id, *, corpus_id, project_id):
    """Resolve a worker input through the same exact-source checks as admission."""
    from .source_corpus import SourceRegion

    record = store.get(region_id, corpus_id=corpus_id, project_id=project_id)
    if record is None or record.kind != "SourceRegion" or record.project_id not in (None, project_id):
        raise ConflictError("source region is missing or outside the authorized scope")
    region = SourceRegion.model_validate(record.content)
    reference = EvidenceReference(
        corpus_id=corpus_id, project_id=record.project_id, region_id=region_id,
        artifact_id=region.artifact_id, content_hash=region.artifact_id,
        source_revision=region.source_revision, quotation=None, locator=evidence_locator(region),
    )
    if validate_reference(store, reference, corpus_id=corpus_id, project_id=project_id, target_id=region_id):
        raise ConflictError("source region failed exact-source validation")
    return record


def _validate_item_evidence(store, items, *, corpus_id):
    """Check each exact binding once per invocation, never cache across calls.

    Large graphs repeat the same immutable region on many nodes and edges.
    Scope, quotation and every locator field remain part of the cache key;
    failures are still reported separately against every affected graph object.
    """
    issues: list[EvidenceValidationIssue] = []
    checked = 0
    cache = {}
    for item in items:
        for reference in item.evidence:
            checked += 1
            target_id = item.node_id if isinstance(item, OKFNode) else item.edge_id
            key = (corpus_id, item.project_id, identity(reference))
            if key not in cache:
                cache[key] = validate_reference(store, reference, corpus_id=corpus_id,
                    project_id=item.project_id, target_id=target_id)
            issues.extend(issue.model_copy(update={"target_id": target_id}) for issue in cache[key])
    return EvidenceValidationReport(valid=not issues, issues=tuple(issues), checked_references=checked)


def validate_snapshot_evidence(store, snapshot: OKFSnapshot) -> EvidenceValidationReport:
    """Validate all node and edge evidence bindings against immutable storage."""
    return _validate_item_evidence(store, (*snapshot.nodes, *snapshot.edges), corpus_id=snapshot.corpus_id)


def validate_delta_evidence(store, delta: OKFDelta) -> EvidenceValidationReport:
    """Validate evidence attached to a proposed delta before graph admission."""
    issues: list[EvidenceValidationIssue] = []
    existing = store.read_okf_snapshot(corpus_id=delta.corpus_id, project_id=delta.project_id)
    nodes = {node.ref: node for node in existing.nodes}
    nodes.update({node.ref: node for node in delta.upsert_nodes})
    affected = provisional_nodes(nodes.values())
    for node in delta.upsert_nodes:
        if node.ref in affected and not provisional_object(node):
            issues.append(EvidenceValidationIssue(code="evidence.inherited_quality_missing",
                message="A claim derived from fast evidence must retain preparation_quality=fast_provisional.",
                target_id=node.node_id))
    evidence = _validate_item_evidence(store, (*delta.upsert_nodes, *delta.add_edges), corpus_id=delta.corpus_id)
    issues.extend(evidence.issues)
    return EvidenceValidationReport(valid=not issues, issues=tuple(issues), checked_references=evidence.checked_references)


def build_promotion_proposal(
    snapshot: OKFSnapshot,
    *,
    proposal_id: str,
    target_corpus_id: str,
    rationale: str,
) -> PromotionProposal:
    """Create a provenance-bearing proposal without changing corpus state."""
    if snapshot.project_id is None:
        raise ValueError("only project-scoped snapshots can be promoted")
    items = tuple(item for item in (*snapshot.nodes, *snapshot.edges) if item.project_id == snapshot.project_id)
    if any(not item.evidence for item in items):
        raise ValueError("promotion requires evidence for every promoted node and edge")
    evidence = tuple(reference for item in items for reference in item.evidence)
    return PromotionProposal(
        proposal_id=proposal_id,
        source_project_id=snapshot.project_id,
        target_corpus_id=target_corpus_id,
        source_snapshot_id=snapshot.snapshot_id,
        source_graph_revision=snapshot.graph_revision,
        node_ids=tuple(node.node_id for node in snapshot.nodes if node.project_id == snapshot.project_id),
        edge_ids=tuple(edge.edge_id for edge in snapshot.edges if edge.project_id == snapshot.project_id),
        evidence=evidence,
        rationale=rationale,
    )


def build_promotion_delta(
    snapshot: OKFSnapshot,
    proposal: PromotionProposal,
    *,
    base_revision: GraphRevision,
) -> OKFDelta:
    """Translate an approved proposal into a corpus-scoped delta with origin links."""
    if proposal.source_snapshot_id != snapshot.snapshot_id or proposal.source_graph_revision != snapshot.graph_revision:
        raise ConflictError("promotion proposal does not match the supplied project snapshot")
    if proposal.source_project_id != snapshot.project_id or snapshot.project_id is None:
        raise ConflictError("promotion proposal source project does not match the snapshot")
    if proposal.target_corpus_id != snapshot.corpus_id:
        raise ConflictError("promotion target corpus must match the source snapshot corpus")
    origin = lambda source_id: PromotionOrigin(
        proposal_id=proposal.proposal_id,
        source_project_id=proposal.source_project_id,
        source_snapshot_id=proposal.source_snapshot_id,
        source_graph_revision=proposal.source_graph_revision,
        source_id=source_id,
    )
    selected_nodes = [n for n in snapshot.nodes if n.node_id in proposal.node_ids and n.project_id == snapshot.project_id]
    selected_edges = [e for e in snapshot.edges if e.edge_id in proposal.edge_ids and e.project_id == snapshot.project_id]
    if len(selected_nodes) != len(set(proposal.node_ids)) or len(selected_edges) != len(set(proposal.edge_ids)):
        raise ConflictError("promotion selection is missing, duplicated, or not project-owned")
    from .okf_contracts import GraphIdentity
    remap = {n.ref: GraphIdentity(corpus_id=snapshot.corpus_id, local_id="promoted-" + identity([proposal.proposal_id, n.ref])[:32]) for n in selected_nodes}
    def endpoint(ref):
        if ref.project_id is None:
            return ref
        if ref not in remap:
            raise ConflictError("promotion has an unselected private dependency")
        return remap[ref]
    nodes = tuple(n.model_copy(update={"node_id": remap[n.ref].local_id, "project_id": None,
                  "parents": tuple(endpoint(p) for p in n.parents), "promotion_origin": origin(n.node_id)}) for n in selected_nodes)
    edges = tuple(e.model_copy(update={"edge_id": "promoted-" + identity([proposal.proposal_id, e.ref])[:32],
                  "project_id": None, "source_id": endpoint(e.source_id), "target_id": endpoint(e.target_id),
                  "promotion_origin": origin(e.edge_id)}) for e in selected_edges)
    return OKFDelta(
        delta_id=f"promotion-{proposal.proposal_id}",
        base_revision=base_revision,
        corpus_id=proposal.target_corpus_id,
        ontology_profile=snapshot.ontology_profile,
        upsert_nodes=nodes,
        add_edges=edges,
        reason=proposal.rationale,
    )


def commit_promotion(
    store,
    snapshot: OKFSnapshot,
    proposal: PromotionProposal,
    approval: PromotionApproval,
) -> OKFCommitResult:
    """Validate and commit an explicitly approved project-to-corpus promotion."""
    from .graph_commit import OKFCommitApproval, commit_okf_delta

    if approval.proposal_id != proposal.proposal_id or approval.proposal_hash != identity(proposal):
        raise ConflictError("approval does not authorize this promotion proposal")
    evidence_report = validate_snapshot_evidence(store, snapshot)
    if not evidence_report.valid:
        raise ConflictError("promotion contains invalid evidence bindings")
    original = store.read_okf_snapshot(corpus_id=snapshot.corpus_id, project_id=snapshot.project_id, revision=snapshot.graph_revision)
    if original != snapshot:
        raise ConflictError("promotion requires the exact persisted project snapshot")
    previous = store.get_okf_operation("promotion-" + proposal.proposal_id, corpus_id=snapshot.corpus_id)
    if previous is None and store.graph_revision(snapshot.corpus_id, snapshot.project_id) != snapshot.graph_revision:
        raise ConflictError("project snapshot is stale and cannot be promoted")
    delta = build_promotion_delta(snapshot, proposal, base_revision=GraphRevision(corpus_id=snapshot.corpus_id, corpus_revision=snapshot.graph_revision.corpus_revision))
    return commit_okf_delta(
        store,
        delta,
        OKFCommitApproval.for_delta(
            delta,
            approval_id=approval.approval_id,
            approved_by=approval.approved_by,
            rationale=approval.rationale,
            approved_at=approval.approved_at,
        ),
    )


__all__ = [
    "EvidenceValidationIssue",
    "EvidenceValidationReport",
    "PromotionApproval",
    "PromotionProposal",
    "build_promotion_delta",
    "build_promotion_proposal",
    "commit_promotion",
    "validate_delta_evidence",
    "validate_snapshot_evidence",
]
