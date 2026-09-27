"""Read-only repair view over existing obligations and immutable outcomes."""


def repair_feedback(graph, attempts=(), *, support_nodes=None):
    attempts = list(attempts)
    attempted = {}
    for request_id, response in attempts:
        item = response.get("result", {}).get("repair_attempt")
        if item and item.get("performed") and response.get("status") not in {"rejected", "cancelled", "interrupted"}:
            attempted.setdefault(item["target_id"], []).append(request_id)
    progress = [{"target_id": identifier,
        "state": "resolved" if obligation["status"] != "open" else "attempted" if identifier in attempted else "recorded",
        "evidence_status": obligation["status"], "attempt_request_ids": attempted.get(identifier, [])}
        for identifier, obligation in graph.obligations.items()]
    unresolved = [item["target_id"] for item in progress if item["state"] != "resolved"]
    # Never combine output paths implicitly. Partial repairs do not erase a
    # complete candidate; an explicit submission selects its exact node set.
    required = set(graph.required_paths)
    candidates = [graph.nodes[identifier] for identifier in graph.order if (node := graph.nodes[identifier])["kind"] == "calculation_output"
        and node["origin"]["output_path"] in required]
    batches = {}
    for node in candidates:
        batches.setdefault(node["origin"]["receipt_id"], {})[node["origin"]["output_path"]] = node["id"]
    complete = [batch for batch in batches.values() if required <= batch.keys()]
    selection = "latest_required_output_batch"
    if support_nodes is not None:
        if (not isinstance(support_nodes, dict) or set(support_nodes) - required
                or any(not isinstance(identifier, str) or identifier not in graph.nodes for identifier in support_nodes.values())):
            raise ValueError("candidate selection requires exact local node IDs for required output paths")
        outputs = dict(support_nodes)
        selection = "explicit"
    elif complete:
        outputs = dict(complete[-1])
        selection = "latest_complete_output_batch"
    elif candidates:
        receipt = candidates[-1]["origin"]["receipt_id"]
        outputs = {node["origin"]["output_path"]: node["id"] for node in candidates
            if node["origin"]["receipt_id"] == receipt}
    else:
        outputs = {}
    # Durable chronological outcomes are the authority for submitted selection.
    # Only completed actions with actual local nodes participate; failed,
    # cancelled and rejected requests cannot change the working view.
    if support_nodes is None:
        for _, response in attempts:
            if response.get("status") != "completed":
                continue
            payload = response.get("result", {})
            batch = {path: node["id"] for path, node in payload.get("output_nodes", {}).items()
                if path in required and node.get("id") in graph.nodes}
            proposal = payload.get("proposal", {}).get("support_nodes", {})
            if required and set(batch) == required:
                outputs, selection = batch, "latest_complete_output_batch"
            if (required and set(proposal) == required
                    and all(isinstance(n, str) and n in graph.nodes for n in proposal.values())):
                outputs, selection = dict(proposal), "last_submitted_candidate"
    pending_updates = {path: node for batch in batches.values() for path, node in batch.items()
        if path not in outputs or graph.order.index(node) > graph.order.index(outputs[path])}
    pending_updates = {path: node for path, node in pending_updates.items() if outputs.get(path) != node}
    has_candidate = support_nodes is not None or bool(candidates)
    relevant = set().union(*(graph._roots(node) for node in outputs.values())) if outputs else set()
    current = [identifier for identifier in unresolved if identifier in relevant]
    historical = [identifier for identifier in unresolved if identifier not in relevant] if has_candidate else []
    for item in progress:
        item["scope"] = "current_candidate" if item["target_id"] in relevant else "audit_only" if has_candidate else "unselected"
    from .math_evidence_handoff import receipt_handoff
    available = [{key: value for key, value in receipt_handoff(graph, identifier).items()
        if key in {"receipt_id", "source_sha256", "execution_status", "exit_code"}} for identifier, receipt in graph.receipts.items()
        if receipt.get("kind") == "execution" and receipt.get("output", {}).get("outcome") == "executed"
        and receipt["output"].get("exit_code") == 0]
    base = {"repair_progress": progress,
        "current_candidate": {"selection": selection, "pending_output_updates": pending_updates,
            "support_nodes": outputs, "missing_required_paths": sorted(required - outputs.keys()),
            "open_obligation_ids": current, "audit_only_open_obligation_ids": historical,
            "authority": "This is dependency accounting, not scientific certification. Unrepresented physical claims are not validated by absence of open roots."},
        "evidence_handoff": {"available_successful_explorations": available,
            "relevance": "Available for inspection, not automatically relevant or qualifying support for this candidate.",
            "next_action": "Resolve current-candidate obligations using the existing substantiation tools, revise the candidate, or report the exact missing prerequisite. Audit-only obligations remain immutable but need not be resolved to submit a different candidate. When required outputs have adequate support, call submit with their exact support_nodes; do not infer that old frontier obligations forbid submission."}}
    selected = current if has_candidate else unresolved
    if not selected:
        return {**base, "repair_task": None}
    order = {identifier: index for index, identifier in enumerate(graph.order)}
    target = min(selected, key=lambda identifier: (
        0 if identifier in relevant else 1,
        0 if graph.nodes[identifier]["kind"] in {"assumption", "hypothesis"} else 1,
        order[identifier]))
    node = graph.nodes[target]
    affected = [path for path, identifier in outputs.items() if target in graph._roots(identifier)]
    dependents = [n for n in graph.nodes.values() if target in n.get("depends_on", [])]
    receipt_ids = sorted({n.get("origin", {}).get("receipt_id") for n in [node, *dependents]
        if n.get("origin", {}).get("receipt_id")})
    state = next(item["state"] for item in progress if item["target_id"] == target)
    related = [{"request_id": rid, **response["result"]["repair_attempt"]}
        for rid, response in attempts if response.get("result", {}).get("repair_attempt", {}).get("target_id") == target]
    return {**base, "repair_task": {
        "target_id": target, "state": state, "objection": graph.obligations[target]["reason"],
        "prior_attempt": {"node_id": target, "status": "untrusted previous attempt",
            "inspect": "Exact previous values, claims and receipts remain available through inspect; they are not targets to reproduce."},
        "affected_output_paths": affected,
        "context": {"parent_node_ids": node.get("depends_on", []),
            "dependent_node_ids": [n["id"] for n in dependents], "receipt_ids": receipt_ids,
            "related_attempts": related,
            "inspect": "Use inspect(node/receipt) for exact originating steps, generated code, inputs and outputs; they are attempts, not authority."},
        "next_task": "What is the smallest missing relation between the known definitions and the requested output? Identify its inputs, derive it separately, and explain how its output connects to the calculation. Retrieve that specific missing definition or general method if needed. Determine the relation before comparing with the untrusted previous attempt; do not assume its value. If they disagree, revise the candidate; agreement alone is not support. Identifying a missing prerequisite is a planning step, not automatically a reason to stop.",
        "does_not_resolve": "Recording or renaming an assumption, restating the answer, or checking only downstream consequences does not resolve this objection.",
        "support_action": ("substantiate_application: exact task/source applicability, or matching independent compiled operator output with an exact method-source quote on its own lineage; no open premises. A receipt link alone is insufficient."
            if node.get("application_reason") else "substantiate: cite an independent matching qualified source or compiled output, without this premise in its ancestry; exploratory stdout alone is insufficient."),
        "stopping_rule": "An unsuccessful search for a complete formula is not by itself a calculation blocker. If authorized computation is available, use it or identify the missing definition, method, capability, permission or resource that prevents independent work. Partial closure remains allowed: report that precise unresolved prerequisite, not a remembered formula as a derivation. Attempted does not mean independent or correct; the harness owns stopping.",
        "authority": "Advisory repair task; existing admission rules alone decide resolution. No reference answer is supplied."}}
