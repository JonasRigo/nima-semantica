"""Mechanical assistance only: no evidence promotion or mathematical inference."""
import ast
import copy
import hashlib
import json

MARKER = "__NIMA_OBSERVATIONS_V1__"
EXPANDED_STEP_LIMIT = 512


class ApplicationSupportError(ValueError):
    """Admission failure with a complete, read-only repair explanation."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.support_diagnostics = diagnostics


def application_support_diagnostics(graph, root_id, evidence_id, source_id, quote):
    """Explain existing checks and inherited support; never manufacture support."""
    from .calculation_values import equivalent

    root, evidence = graph.nodes[root_id], graph.nodes[evidence_id]
    source = graph.nodes.get(source_id)

    def ancestors(identifier):
        seen, pending = set(), [identifier]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(graph.nodes[current]["depends_on"])
        return seen

    lineage = ancestors(evidence_id)
    target_lineage = ancestors(root_id)
    issues = []
    exact_quote = (source is not None and source["kind"] == "source_text"
        and isinstance(quote, str) and len(quote.strip()) >= 12
        and quote in source["value"])
    if not exact_quote:
        issues.append("exact_method_source_quote_required")
    if source is None or source["kind"] != "source_text":
        issues.append("method_source_must_be_source_text_not_task_instruction")
    if source_id not in lineage:
        issues.append("method_source_missing_from_evidence_lineage")
    if not equivalent(root["value"], evidence["value"]):
        issues.append("evidence_and_target_are_different_quantities")
    if root_id in lineage:
        issues.append("evidence_depends_on_disputed_target")
    if evidence["status"] != "observed":
        issues.append("evidence_not_observed")
    if evidence["origin"].get("receipt_id") == root["origin"].get("receipt_id"):
        issues.append("independent_receipt_required")
    if not any(graph.nodes[n]["kind"] == "calculation_operation" and
            graph.nodes[n].get("operation") in {"matrix", "kronecker", "trace", "transpose", "adjoint"}
            for n in lineage):
        issues.append("operator_derivation_missing")
    if not any(graph.nodes[n]["kind"] == "calculation_input" and
            graph.nodes[n].get("operation") == "symbol" and
            any(graph.nodes[p]["kind"] in {"task", "task_fact"} for p in graph.nodes[n]["depends_on"])
            for n in lineage):
        issues.append("task_bound_symbol_missing")
    evidence_open = sorted(graph._open_roots(evidence_id))
    if evidence_open:
        issues.append("evidence_has_open_premises")
    inherited = evidence_id in target_lineage
    # Show only actual edges, not numerical coincidences between unrelated nodes.
    parents = [{"node_id": n, "value": graph.nodes[n]["value"],
        "status": graph.nodes[n]["status"], "open_obligations": sorted(graph._open_roots(n))}
        for n in root["depends_on"] if graph.nodes[n]["kind"] in
        {"calculation_input", "calculation_operation", "calculation_output"}]
    return {"issues": issues, "target": {"node_id": root_id, "value": root["value"]},
        "evidence": {"node_id": evidence_id, "value": evidence["value"],
            "receipt_id": evidence["origin"].get("receipt_id"),
            "open_obligations": evidence_open},
        "inherited_evidence": inherited, "operands": parents,
        "target_open_obligations": sorted(graph._open_roots(root_id)),
        "next_action": ("The target already retains this calculation in its ancestry. Keep that calculation; support the remaining application/weight separately, or derive the full target independently. Do not re-prove a coefficient merely to restate its multiplication."
            if inherited else "Evidence supports its own quantity, not automatically the target. Inspect the quantities and missing dependencies before retrieving more context."),
        "source_action": "Select an applicable source_text passage using method_source_id and method_span={start,end}; the controller extracts exact text and provenance. No retrieval or applicability inference is automatic.",
        "authority": "Observed algebra and inherited provenance are not certification of the physical application. All existing admission checks remain active."}


def source_span(graph, source_id, span):
    node = graph.nodes.get(source_id)
    if node is None or node["kind"] != "source_text":
        raise ValueError("source span requires a local exact source_text node")
    text = node["value"]
    if (not isinstance(span, dict) or set(span) != {"start", "end"}
            or any(type(span[k]) is not int for k in span)
            or not 0 <= span["start"] < span["end"] <= len(text)):
        raise ValueError("source span requires valid half-open Unicode character offsets")
    return {"source_id": source_id, **span, "quote": text[span["start"]:span["end"]],
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "provenance": copy.deepcopy(node["origin"])}


def expand_reuse(graph, arguments):
    """Recompile exact prior atomic dependency closures, never pasted result strings."""
    args = copy.deepcopy(arguments)
    if len(args["steps"]) > 128:
        raise ValueError("at most 128 new atomic steps allowed; reuse prior calculations")
    requested = args.pop("reuse", {})
    used = {s["id"] for s in args["steps"]} | {a["id"] for a in args.get("aggregations", [])}
    if used & requested.keys():
        raise ValueError("reused definition alias collides with a local step")
    used.update(requested)
    generated, memo, active, records, identities = [], {}, set(), [], {}

    def clone(node_id):
        if node_id in memo:
            return memo[node_id]
        if node_id in active:
            raise ValueError("cyclic reused definition")
        active.add(node_id)
        node = graph.nodes.get(node_id, {})
        origin = node.get("origin", {})
        rid = origin.get("receipt_id")
        receipt = graph.receipts.get(rid, {})
        staged = graph.compiled_plans.get(rid)
        if (node.get("kind") not in {"calculation_input", "calculation_operation", "calculation_output"}
                or not staged or receipt.get("kind") != "compiled_execution"
                or receipt.get("output", {}).get("outcome") != "executed"
                or receipt["output"].get("exit_code") != 0
                or hashlib.sha256(receipt["source"].encode()).hexdigest() != receipt.get("source_sha256")):
            raise ValueError("reuse requires an executed local compiled definition, not exploratory stdout")
        plan = staged["plan"]
        if plan["source_sha256"] != receipt["source_sha256"] or plan["source"] != receipt["source"]:
            raise ValueError("reused plan and execution source differ")
        sid = origin.get("operation_id") or plan["outputs"].get(origin.get("output_path"))
        step = next(s for s in plan["steps"] if s["id"] == sid)
        observations = {n["origin"]["operation_id"]: n["id"] for n in graph.nodes.values()
            if n["kind"] in {"calculation_input", "calculation_operation"} and n["origin"].get("receipt_id") == rid}
        observations.update(staged.get("observed_nodes", {}))
        if node["kind"] == "calculation_output":
            canonical = observations[sid]
            if graph.nodes[canonical]["value"] != node["value"]:
                raise ValueError("reused output differs from its executed operation")
            root = clone(canonical)
            memo[node_id] = root
            active.remove(node_id)
            return root
        operands = [clone(observations[a]) for a in step["args"]]
        index = len(generated)
        while f"reuse_{index}" in used:
            index += 1
        alias = f"reuse_{index}"
        used.add(alias)
        item = {k: copy.deepcopy(step[k]) for k in ("op", "meaning", "application", "value", "raw_definition", "index_scope", "index_sum") if k in step}
        if step["op"] == "matrix":
            item["value"] = step["shape"]
        if step.get("declared_physical_input_premise"):
            item["application"] = "physical_rule"
        counts = step.get("index_count_of", [])
        if counts:
            item["index_count_of"] = counts[0]
        # Keep the old node in ancestry: unresolved premises cannot be laundered.
        item.update(id=alias, args=operands, provenance=list(step["provenance"]))
        generated.append(item)
        identities[alias] = node_id
        if len(generated) > EXPANDED_STEP_LIMIT:
            raise ValueError("controller-expanded closure exceeds 512 atomic steps; split the calculation")
        memo[node_id] = alias
        active.remove(node_id)
        return alias

    for alias, node_id in requested.items():
        root = clone(node_id)
        generated.append(dict(id=alias, op="simplify", args=[root], provenance=[node_id], meaning="Reuse exact prior atomic definition"))
        records.append({"alias": alias, "node_id": node_id, "authority": "Dependency reuse, not new certification"})
    args["steps"] = generated + args["steps"]
    if len(args["steps"]) > EXPANDED_STEP_LIMIT:
        raise ValueError("controller-expanded closure exceeds 512 atomic steps; split the calculation")
    args["_reuse_identities"] = identities
    return args, records


def instrument_exploration(source):
    """Capture named top-level mathematical values in the same isolated execution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source  # Let the isolated worker record the genuine failed attempt.
    names = sorted({t.id for s in tree.body if isinstance(s, (ast.Assign, ast.AnnAssign))
        for t in (s.targets if isinstance(s, ast.Assign) else [s.target]) if isinstance(t, ast.Name)})
    suffix = '''
import json as _nima_json
import sympy as _nima_sp
_nima_values = {}
for _nima_name in NAMES:
    _nima_value = globals().get(_nima_name)
    if isinstance(_nima_value, (int, float, complex, _nima_sp.Basic, _nima_sp.MatrixBase)):
        _nima_text = str(_nima_value)
        if len(_nima_text) <= 32000:
            _nima_values[_nima_name] = _nima_text
print(MARKER + _nima_json.dumps(_nima_values))
'''.replace("NAMES", repr(names)).replace("MARKER", repr(MARKER))
    instrumented = source + "\n" + suffix
    return instrumented if len(instrumented) <= 30000 else source


def observations(receipt):
    if not receipt.get("observations_instrumented"):
        return {}
    lines = receipt.get("output", {}).get("stdout", "").splitlines()
    try:
        value = json.loads(lines[-1].removeprefix(MARKER)) if lines and lines[-1].startswith(MARKER) else {}
        return value if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()) else {}
    except ValueError:
        return {}


def compare_reconstruction(graph, result, receipt_id, bindings):
    prior = observations(graph.receipts[receipt_id])
    ids = result.get("step_nodes", {})
    selected = bindings if bindings is not None else {k: k for k in ids if k in prior}
    comparisons = []
    for sid in ids:  # compiled topological order, not caller binding order
        if sid not in selected:
            continue
        name = selected[sid]
        actual = graph.nodes[ids[sid]]["value"]
        expected = prior.get(name)
        comparisons.append({"step_id": sid, "variable": name, "prior": expected, "actual": actual,
            "status": "unavailable" if expected is None else "identical" if expected == actual else "different_representation"})
    return {"comparisons": comparisons,
        "first_difference": next((c for c in comparisons if c["status"] != "identical"), None),
        "unmatched_bindings": sorted(set(selected) - ids.keys()),
        "authority": "Exact observed representation comparison only; different expressions may be equivalent. Matching neither validates definitions nor certifies physical applicability. Only listed correspondences were checked."}


def execution_diagnostics(plan, execution):
    try:
        payload = json.loads(execution.get("stdout", ""))
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    if "operation_error" in payload:
        return [payload["operation_error"]]
    values = payload.get("steps", {})
    if not isinstance(values, dict):
        return []
    import re
    result = []
    for step in plan["steps"]:
        value = values.get(step["id"], "")
        inputs = {arg: values.get(arg) for arg in step["args"]}
        zero_denominator = step["op"] == "divide" and inputs.get(step["args"][1]) == "0"
        if zero_denominator or re.search(r"\b(?:zoo|nan|oo)\b", value):
            result.append({"step_id": step["id"], "operation": step["op"], "inputs": inputs,
                "result": value, "issue": "zero_denominator" if zero_denominator else "nonfinite_result",
                "authority": "Inspect operands and definitions; this is not a missing-tool diagnosis."})
    return result[:16]
