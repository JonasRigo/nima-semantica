"""Explicit, non-certifying exploration-to-atomic-plan handoff."""
import hashlib


def reconstruction_context(graph, receipt_id):
    receipt = graph.receipts.get(receipt_id)
    if receipt is None or receipt.get("kind") != "execution":
        raise ValueError("reconstruction requires an exploratory receipt in this session")
    output = receipt.get("output", {})
    if output.get("outcome") != "executed" or output.get("exit_code") != 0:
        raise ValueError("reconstruction requires a successfully executed exploration")
    digest = hashlib.sha256(receipt["source"].encode()).hexdigest()
    if receipt.get("source_sha256") != digest or (output.get("source_sha256") and output["source_sha256"] != digest):
        raise ValueError("reconstruction receipt source hash mismatch")
    return {"receipt_id": receipt_id, "source_sha256": digest,
        "authority": "Caller-declared method reconstruction, not verified equivalence or promotion of exploratory output. Only new compiled operations and their actual evidence lineage can qualify support."}


def receipt_handoff(graph, receipt_id):
    receipt = graph.receipts[receipt_id]
    output = receipt.get("output", {})
    successful = output.get("outcome") == "executed" and output.get("exit_code") == 0
    return {"receipt_id": receipt_id, "source_sha256": receipt.get("source_sha256"),
        "execution_status": output.get("outcome"), "exit_code": output.get("exit_code"),
        "reconstruction_available": receipt.get("kind") == "execution" and successful,
        "next_action": "Reuse the exact definitions, input origins and operations visible in this receipt in run_calculation_graph; set reconstructs_receipt to retain the audit connection. Recompute intermediates, do not copy stdout as a new fact. Decompose into small method calculations with non-answer output names when needed. Mark domain applications physical_rule and keep unsupported assumptions explicit.",
        "qualification": "A successful exploration remains exploratory. Cite the new compiled output for substantiate, or substantiate_application with its existing exact method-source quote and independence requirements. Inspect the current-candidate frontier; submit exact supported required outputs when ready. A missing method or unsupported operation remains a specific blocker, not permission to lower these checks."}
