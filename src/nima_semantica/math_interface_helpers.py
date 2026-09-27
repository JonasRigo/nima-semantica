"""Domain-independent representation helpers; no mathematical inference."""
import copy
import re


def normalize_support_nodes(arguments):
    """Unwrap only unambiguous singleton references; preserve original caller data."""
    args = copy.deepcopy(arguments)
    repairs = []
    for path, value in args.get("support_nodes", {}).items():
        if isinstance(value, list):
            if len(value) != 1 or not isinstance(value[0], (str, dict)):
                raise ValueError("support_nodes requires one exact reference per output path")
            args["support_nodes"][path] = value[0]
            repairs.append({"output_path": path, "basis": "singleton_support_reference"})
    return args, repairs


def bind_input_origins(graph, arguments):
    """Require explicit roots, with only exact task spelling and arithmetic origins automated."""
    args = copy.deepcopy(arguments)
    bindings, missing = [], []
    for step in args["steps"]:
        assumption = step.pop("assumption", None)
        op, value = step.get("op"), step.get("value")
        if op == "symbol" and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value)):
            raise ValueError("symbol input must be a simple name")
        if op not in {"symbol", "integer", "rational", "imaginary_unit"}:
            if assumption:
                raise ValueError("assumption belongs on an input; derive operations from operands")
            continue
        provenance = step.setdefault("provenance", [])
        if assumption:
            if provenance:
                raise ValueError("input must choose cited provenance or an explicit assumption, not both")
            # Scalar canonical encoding for rational hypotheses; never trusted evidence.
            premise_value = f"{value[0]}/{value[1]}" if op == "rational" and isinstance(value, list) and len(value) == 2 else value
            node = graph.add_step(kind="assumption", statement=assumption, value=premise_value, depends_on=[])
            provenance.append(node["id"])
            bindings.append({"step_id": step["id"], "node_id": node["id"], "basis": "explicit_unresolved_assumption"})
        elif not provenance and op == "symbol" and isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
            exact = [node["id"] for node in graph.nodes.values() if node["kind"] == "task_fact"
                and value in {node["value"].get("symbol"), node["value"].get("machine_symbol")}]
            if not exact and re.search(r"\b" + re.escape(value) + r"\b", graph.nodes["task"]["value"]):
                exact = ["task"]
            if exact:
                provenance.extend(exact)
                bindings.append({"step_id": step["id"], "node_ids": exact, "basis": "exact_task_symbol"})
        elif not provenance and (op == "imaginary_unit" or (op == "integer" and type(value) is int and value in (0, 1))):
            node = graph._put("arithmetic_literal", "I" if op == "imaginary_unit" else value,
                "observed", [], {"kind": "controller_arithmetic", "authority": "Arithmetic literal only; not evidence for a domain coefficient or cancellation."})
            provenance.append(node["id"])
            bindings.append({"step_id": step["id"], "node_id": node["id"], "basis": "arithmetic_literal_only"})
        if not provenance:
            missing.append(step["id"])
    if missing:
        raise ValueError("input provenance required for: " + ", ".join(missing) + ". Cite exact definitions or declare assumption; do not invent evidence.")
    return args, bindings


def expand_aggregations(arguments, indexed_sets, *, step_limit=128):
    args = copy.deepcopy(arguments)
    aggregations = args.pop("aggregations", [])
    steps = args["steps"]
    declared = [step["id"] for step in steps] + [item["id"] for item in aggregations]
    if len(set(declared)) != len(declared):
        raise ValueError("duplicate step or aggregation ID")
    available = set(declared)
    specs = {spec.set_id: spec for spec in indexed_sets}
    repairs = []
    for aggregation in aggregations:
        identifier, set_id = aggregation["id"], aggregation["set_id"]
        terms = aggregation["contributions"]
        if set_id not in specs:
            raise ValueError("aggregation requires a declared indexed set: " + set_id)
        spec = specs[set_id]
        if set(terms) != set(spec.members):
            raise ValueError("aggregation must supply each declared member exactly once: " + set_id)
        if any(value not in available for value in terms.values()):
            raise ValueError("aggregation ID collision or unknown contribution step")
        members = []
        for member in spec.members:
            # Internal IDs are deterministic and collision-free; keep caller IDs intact.
            serial = 0
            while f"aggregate_member_{serial}" in available or f"aggregate_member_{serial}" == identifier:
                serial += 1
            child = f"aggregate_member_{serial}"
            available.add(child)
            members.append(child)
            steps.append({"id": child, "op": "simplify", "args": [terms[member]],
                "meaning": "Caller-supplied member contribution",
                "index_scope": {"set_id": set_id, "member": member}})
        steps.append({"id": identifier, "op": "sum", "args": members,
            "meaning": "Sum each supplied member once", "index_sum": set_id})
        available.add(identifier)
        repairs.append({"aggregation_id": identifier, "set_id": set_id,
            "contributions": terms, "member_steps": dict(zip(spec.members, members))})
    if len(steps) > step_limit:
        raise ValueError(f"expanded calculation exceeds {step_limit} atomic steps")
    return args, repairs


def order_dependencies(arguments, graph_node_ids=()):
    """Stable topological ordering, without changing operands or their order."""
    args = copy.deepcopy(arguments)
    steps = args["steps"]
    identifiers = [step["id"] for step in steps]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate atomic step ID")
    local, external = set(identifiers), set(graph_node_ids)
    dependencies = {}
    for step in steps:
        operands = step.get("args", [])
        if any(not isinstance(item, str) or item not in local for item in operands):
            raise ValueError(f"step {step['id']}: unknown atomic operand reference")
        provenance = step.get("provenance", [])
        if any(not isinstance(item, str) for item in provenance):
            raise ValueError("malformed provenance reference")
        if any(item in local and item in external for item in provenance):
            raise ValueError("ambiguous local/graph provenance reference")
        dependencies[step["id"]] = set(operands) | (set(provenance) & local)
    ordered, emitted = [], set()
    while len(ordered) < len(steps):
        next_step = next((step for step in steps if step["id"] not in emitted
            and dependencies[step["id"]] <= emitted), None)
        if next_step is None:
            raise ValueError("cyclic atomic/aggregation dependencies")
        ordered.append(next_step)
        emitted.add(next_step["id"])
    args["steps"] = ordered
    order = [step["id"] for step in ordered]
    return args, ([] if order == identifiers else [{"basis": "explicit_dependency_order",
        "original_order": identifiers, "compiled_order": order}])


def resolve_references(arguments, resolve):
    """Only explicit reference slots are resolved; mathematical JSON stays opaque."""
    args = copy.deepcopy(arguments)
    resolutions = []
    def one(value):
        if not isinstance(value, dict):
            return value
        if set(value) != {"request_id", "output_path"}:
            raise ValueError("named reference requires request_id and output_path only")
        node = resolve(value["request_id"], value["output_path"])
        resolutions.append({"reference": value, "node_id": node})
        return node
    for field in ("depends_on",):
        if field in args:
            args[field] = [one(value) for value in args[field]]
    if "reuse" in args:
        args["reuse"] = {key: one(value) for key, value in args["reuse"].items()}
    for field in ("supersedes", "hypothesis_id", "evidence_id", "operation_id", "method_source_id", "repair_target"):
        if field in args:
            args[field] = one(args[field])
    if "support_nodes" in args:
        args["support_nodes"] = {path: one(value) for path, value in args["support_nodes"].items()}
    for step in args.get("steps", []):
        if "provenance" in step:
            step["provenance"] = [one(value) for value in step["provenance"]]
    return args, resolutions
