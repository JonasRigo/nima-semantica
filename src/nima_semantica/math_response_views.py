"""Small model-facing views; durable service outcomes retain the full audit."""
import copy


def response_view(response, operation):
    if operation in {"inspect", "export"}:
        return response
    response = copy.deepcopy(response)
    result = response["result"]
    candidate = result.get("current_candidate", {})
    current = set(candidate.get("open_obligation_ids", []))
    qualification = operation in {"frontier", "submit"}
    history = candidate.pop("audit_only_open_obligation_ids", [])
    if qualification:
        result["repair_progress"] = [item for item in result.get("repair_progress", []) if item["target_id"] in current]
        result["open_obligations"] = [item for item in result.get("open_obligations", []) if item["root_id"] in current]
        if result.get("repair_task") and result["repair_task"]["target_id"] not in current:
            result["repair_task"] = None
        result["historical_open_count"] = len(history)
        result.pop("recent_nodes", None)
        result.pop("evidence_handoff", None)
    else:
        result["support_summary"] = {"current_open_count": len(current),
            "missing_required_paths": candidate.get("missing_required_paths", []),
            "selection": candidate.get("selection"),
            "pending_output_updates": candidate.get("pending_output_updates", {}),
            "authority": "Provisional work may continue. Counts are not certification; request frontier when ready to qualify selected outputs."}
        for key in ("repair_task", "repair_progress", "evidence_handoff", "current_candidate", "open_obligations"):
            result.pop(key, None)
    if "frontier" in result:
        result["frontier"] = {k: v for k, v in result["frontier"].items()
            if k in {"node_count", "required_paths", "indexed_sets"}}
    return response


def submission_support(graph, support):
    """Separate exact required bindings from explicit supplementary references."""
    required = set(graph.required_paths)
    missing = required - set(support)
    if missing:
        raise ValueError("missing required support paths: " + ", ".join(sorted(missing)))
    for path, identifier in support.items():
        if not isinstance(identifier, str) or identifier not in graph.nodes:
            raise ValueError("unknown local submission node for: " + path)
        if graph.nodes[identifier]["status"] in {"failed", "exploratory"}:
            raise ValueError("failed or exploratory submission support for: " + path)
    return ({key: value for key, value in support.items() if key in required},
        {key: value for key, value in support.items() if key not in required})


def assemble_answer(graph, support):
    """Copy selected graph values. Never infer support, expressions or list shape."""
    if set(support) != set(graph.required_paths):
        raise ValueError("every required output path needs one graph support node")
    values = {}
    for path, identifier in support.items():
        node = graph.nodes.get(identifier)
        if node is None:
            raise ValueError("unknown local submission node")
        if node.get("atomic_nodes"):
            node = graph.nodes[node["atomic_nodes"][path]]
        values[path] = copy.deepcopy(node["value"])
    if "$" in values:
        if len(values) != 1:
            raise ValueError("cannot assemble overlapping root and child output paths")
        return values["$"]
    answer, leaves = {}, set()
    for path in sorted(values, key=lambda p: len(p.split("."))):
        parts = path.split(".")
        if any(not part for part in parts):
            raise ValueError("cannot assemble empty output path segment")
        current = answer
        for index, part in enumerate(parts[:-1]):
            if tuple(parts[:index+1]) in leaves:
                raise ValueError("cannot assemble overlapping output paths")
            current = current.setdefault(part, {})
        current[parts[-1]] = values[path]
        leaves.add(tuple(parts))
    return answer
