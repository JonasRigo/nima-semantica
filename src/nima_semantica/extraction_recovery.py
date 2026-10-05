"""Read-only recovery helpers for durable sequential extraction runners.

These helpers select saved failures and prepare new requests; they do not execute
model calls, replace historical receipts, or admit graph proposals.
"""
from pathlib import Path
import json

from .extraction_contracts import DeepExtractionRequest


def failed_plan_regions(plan, result_directory, pending_regions):
    """Select only still-pending regions of batches with saved terminal failures.

    An absent result is unknown delivery and must be reconciled separately.
    A partial response with a graph proposal is retained for coverage review.
    """
    pending = set(pending_regions)
    selected = []
    for batch in plan["data"]["batches"]:
        operation = batch["operation_id"]
        # Operation IDs come from the native request contract, never filesystem paths.
        request = DeepExtractionRequest.model_validate(batch)
        if request.mode != "regional" or Path(operation).name != operation or "\\" in operation:
            raise ValueError("Recovery requires a native regional batch with a safe operation ID")
        path = Path(result_directory) / (operation + "-result.json")
        if not path.exists():
            continue
        result = json.loads(path.read_text())
        if result["status"] == "failed" or not result.get("artifacts", {}).get("graph_proposal"):
            selected.extend(region for region in request.source_region_ids if region in pending)
    return list(dict.fromkeys(selected))


def recovery_plan_request(original, regions, *, attempt_id):
    """Validate a fresh plan that cannot expand the explicitly selected old scope."""
    request = DeepExtractionRequest.model_validate(original)
    if request.mode != "plan" or not request.source_region_ids:
        raise ValueError("Recovery requires an original plan with explicit region scope")
    if not attempt_id or attempt_id == request.plan_attempt_id:
        raise ValueError("Recovery requires a fresh plan_attempt_id")
    selected = tuple(regions)
    if not selected or not set(selected).issubset(request.source_region_ids):
        raise ValueError("Recovery regions must be a nonempty subset of the original plan")
    payload = request.model_dump(mode="json", exclude_none=True)
    payload.update(source_region_ids=selected, plan_attempt_id=attempt_id)
    return DeepExtractionRequest.model_validate(payload).model_dump(mode="json", exclude_none=True)


def failure_status(exc, *, previous=None, pending=None, pending_call=None):
    """Distinguish an in-flight tool call from a returned, saved failed result."""
    pending = pending or {}
    return {
        "status": "stopped_for_diagnosis",
        "error": type(exc).__name__,
        "message": str(exc),
        "last_status": previous or {},
        "pending_call": str(pending_call) if pending_call is not None else None,
        "delivery_uncertain": pending.get("status") == "in_flight",
        "last_result_status": pending.get("result_status"),
    }
