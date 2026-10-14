"""Validate scoped observations from existing calls; never certify semantics."""
from .models import ConflictError, identity
from .vocabulary_contracts import VocabularyExtension, VocabularyFinding, VocabularyObservation
from .evidence_contracts import validate_reference


def assess_vocabulary(store, packet, *, corpus_id, project_id, target_id, argument,
                      observations=(), extensions=(), assessment=None):
    if packet.get("status") != "active":
        return {"status": packet["status"], "findings": [], "extensions": [], "semantic_verification": False}
    from .vocabulary_context import load_snapshot
    from .vocabulary_contracts import VocabularyPolicy
    snapshot = load_snapshot(store, VocabularyPolicy.model_validate(packet["policy"]),
        corpus_id=corpus_id, project_id=project_id, current=False)
    ids = {x.entry_id for x in snapshot.entries}
    findings, proposals = [], []
    scope = dict(corpus_id=corpus_id, project_id=project_id)
    for value in observations:
        observation = VocabularyObservation.model_validate(value)
        if not set(observation.entry_ids) <= ids:
            raise ConflictError("vocabulary finding references an unknown entry")
        for ref in observation.evidence:
            if validate_reference(store, ref, **scope, target_id=target_id):
                raise ConflictError("vocabulary finding exact evidence is invalid")
        payload = dict(**scope, snapshot_hash=snapshot.content_hash, target_id=target_id,
            argument_hash=identity(argument), **observation.model_dump(mode="json"))
        findings.append(VocabularyFinding(finding_id=identity(payload), **payload).model_dump(mode="json"))
    for value in extensions:
        proposal = VocabularyExtension.model_validate(value)
        if (proposal.corpus_id, proposal.project_id, proposal.base_snapshot_hash) != (corpus_id, project_id, snapshot.content_hash):
            raise ConflictError("vocabulary extension scope or snapshot mismatch")
        if not set(proposal.related_entry_ids) <= ids:
            raise ConflictError("extension references unknown vocabulary entries")
        for ref in proposal.evidence:
            if validate_reference(store, ref, **scope, target_id=target_id):
                raise ConflictError("vocabulary extension exact evidence is invalid")
        proposals.append(proposal.model_dump(mode="json"))
    return {"status": "assessed" if assessment else "unresolved", "assessment": assessment,
        "snapshot_hash": snapshot.content_hash, "argument_hash": identity(argument),
        "findings": findings, "extensions": proposals, "authority": "advisory",
        "semantic_verification": False, "scientific_admission": False}
