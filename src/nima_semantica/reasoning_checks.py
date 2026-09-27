"""Controller-only bridge from existing NIMA verification services to graph checks.

The agent cannot mint these receipts. Binding a native request does not establish
that the request faithfully represents the original natural-language problem.
"""
from .models import ConflictError, VerificationResult, identity
from .receipts import ExecutionReceipt
from .verification_service import VerificationServiceResult
from .lean_verification_service import LeanVerificationResult

PROTOCOL = "native-reasoning-check-v1"


def native_check_adapter(item, receipt):
    if receipt.stage != "reasoning_native_check" or receipt.tool_version != PROTOCOL:
        raise ValueError("not a native reasoning-check receipt")
    result = VerificationResult.model_validate(receipt.metadata["verification"])
    if result.correspondence_verified:
        raise ValueError("native execution cannot certify source correspondence")
    return result


def attach_native_check(state, *, base_revision, target, receipt_id, expected_request_hash=None):
    """Harness API; uses stored native results, never a model-authored verdict."""
    if not state.allow_writes:
        raise PermissionError("reasoning audit writes are disabled")
    with state.store.joined_transaction():
        view = state.view()
        if view["revision"] != base_revision:
            raise ConflictError("stale native-check revision")
        item = view["items"][target]
        receipt = state.receipts.get(receipt_id, **state.scope)
        if receipt is None or receipt.status not in ("completed", "partial"):
            raise ValueError("native receipt missing or unsuccessful")
        expected = expected_request_hash or item["facets"].get("verification_request_hash")
        if not expected or receipt.metadata.get("request_hash") != expected:
            raise ValueError("native request differs from the represented target")
        if receipt.stage == "verification":
            result = VerificationServiceResult.model_validate(receipt.metadata["result"])
            if result.status != "completed" or result.verification is None:
                raise ValueError("native verification has no result")
            verification = result.verification.model_copy(update={"target_id":state.fingerprint(target), "protocol":PROTOCOL,
                                                                 "correspondence_verified":False})
        elif receipt.stage == "lean_verification":
            result = LeanVerificationResult.model_validate(receipt.metadata["result"])
            if item["facets"].get("environment_manifest_hash") != identity(result.environment_manifest):
                raise ValueError("Lean environment differs from the represented target")
            flags = result.project_result
            accepted = result.status == "verified" and all(flags.get(k) is True for k in
                ("certification_verified", "axioms_accepted", "kernel_replay_succeeded"))
            verification = VerificationResult(target_id=state.fingerprint(target),protocol=PROTOCOL,
                outcome="verified" if accepted else "inconclusive",evidence_artifact=result.evidence_artifact,
                scope="exact native Lean request and environment only; source correspondence not established")
        else:
            raise ValueError("receipt is not from an allowed native verification service")
        if (result.receipt_id != receipt_id or result.operation_id != receipt.operation_id
                or result.corpus_id != state.scope["corpus_id"] or result.project_id != state.scope["project_id"]):
            raise ValueError("native result identity differs from receipt")
        fingerprint = state.fingerprint(target)
        rid = identity(dict(protocol=PROTOCOL,target=fingerprint,receipt=receipt_id,attempt=state.attempt_id,**state.scope))
        state.receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=state.attempt_id,
            stage="reasoning_native_check",**state.scope,status="completed",tool_version=PROTOCOL,
            input_ids=(receipt_id,),metadata={"reasoning_target":fingerprint,
                "verification":verification.model_dump(mode="json")}))
        # The controller chooses this adapter; it is never accepted from a patch.
        previous = state.validators.get(PROTOCOL)
        state.validators[PROTOCOL] = native_check_adapter
        try:
            return state.attach_check(base_revision=base_revision,target=target,receipt_id=rid,validator=PROTOCOL)
        finally:
            if previous is None:
                state.validators.pop(PROTOCOL,None)
            else:
                state.validators[PROTOCOL] = previous
