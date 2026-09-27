"""Compile one batch of atomic SymPy operations from a private calculation DAG.

The compiler controls executable source; model-authored Python is not used here.
The operation graph exposes syntax and lineage, not physical applicability.
"""
from __future__ import annotations

import ast
import hashlib
import re


_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_ARITY = {"add": (2, 2), "subtract": (2, 2), "multiply": (2, 2),
    "divide": (2, 2), "power": (2, 2), "negative": (1, 1),
    "simplify": (1, 1), "differentiate": (2, 2),
    "kronecker": (2, 2), "trace": (1, 1), "transpose": (1, 1),
    "adjoint": (1, 1), "substitute": (3, 3), "sum": (1, 64),
    "matrix": (1, 64), "symbol": (0, 0), "integer": (0, 0),
    "rational": (0, 0), "imaginary_unit": (0, 0)}


def _input_step(step: dict) -> bool:
    return step["op"] in {"symbol", "integer", "rational", "imaginary_unit"}


def _raw_numeric_definition(graph, identifier, op, value, provenance, declaration):
    """Bind a claimed numeric definition to a literal named equation in one exact source.

    This checks the raw definition only. It does not license a later physical use
    of the number, which keeps its separate application obligation.
    """
    if declaration is None:
        return None
    if op not in {"integer", "rational"} or not isinstance(declaration, dict) or set(declaration) != {"evidence_id", "quote"}:
        raise ValueError(f"step {identifier}: raw_definition requires a numeric input, exact evidence ID and quote")
    evidence_id, quote = declaration["evidence_id"], declaration["quote"]
    if not isinstance(evidence_id, str) or evidence_id not in provenance or not isinstance(quote, str) or not quote.strip():
        raise ValueError(f"step {identifier}: raw_definition must cite one declared exact provenance reference")
    node = graph.nodes.get(evidence_id)
    if node is None or node["kind"] not in {"task", "task_fact", "source_text"}:
        raise ValueError(f"step {identifier}: raw_definition needs an exact task fact or source passage")
    body = node["value"]
    body = body.get("quote", "") if node["kind"] == "task_fact" else body
    if not isinstance(body, str) or quote not in body:
        raise ValueError(f"step {identifier}: raw_definition quote is not present in cited evidence")
    if op == "integer" and type(value) is not int:
        raise ValueError(f"step {identifier}: integer raw_definition needs an integer value")
    if op == "rational" and (not isinstance(value, list) or len(value) != 2 or
            any(type(item) is not int for item in value) or value[1] == 0):
        raise ValueError(f"step {identifier}: rational raw_definition needs a valid rational value")
    number = str(value) if op == "integer" else rf"{value[0]}\s*/\s*{value[1]}"
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(identifier)}\s*=\s*{number}(?![A-Za-z0-9_./]|\s*[+\-*/^])"
    if re.search(pattern, quote) is None:
        raise ValueError(f"step {identifier}: cited text does not explicitly define this named numeric value")
    return {"evidence_id": evidence_id, "quote": quote}


def _normalize_steps(steps: list[dict]) -> list[dict]:
    """Expand associative proposals without changing their factor order or claims."""
    if not isinstance(steps, list):
        return steps
    reserved = {step.get("id") for step in steps if isinstance(step, dict) and isinstance(step.get("id"), str)}
    normalized = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in {"add", "multiply"} or not isinstance(step.get("args"), list) or len(step["args"]) <= 2:
            normalized.append(step)
            continue
        args = step["args"]
        previous = args[0]
        for index, factor in enumerate(args[1:-1], 1):
            synthetic = f"NimaFold{len(normalized)}_{index}"
            while synthetic in reserved:
                synthetic += "_"
            reserved.add(synthetic)
            normalized.append({"id": synthetic, "op": step["op"], "args": [previous, factor],
                "provenance": [], "meaning": f"Partial {step['op']} for {step.get('id')} through input {index + 1}",
                "proposal_step_id": step.get("id")})
            previous = synthetic
        normalized.append({**step, "args": [previous, args[-1]]})
    return normalized


def _normalize_inline_references(steps: list[dict]):
    """Lift exact inline atomic steps and object-shaped ID references; no algebra changes."""
    if not isinstance(steps, list):
        return steps, []
    reserved = {step.get("id") for step in steps if isinstance(step, dict)}
    normalized, repairs = [], []

    def lift(raw, owner):
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            return raw
        if set(raw) == {"id"}:
            repairs.append({"proposal_step_id": owner,
                "kind": "object_reference_unwrapped", "atomic_step_ids": [raw["id"]]})
            return raw["id"]
        identifier = raw["id"]
        if identifier in reserved or "op" not in raw:
            return raw
        reserved.add(identifier)
        inline = {**raw, "args": [lift(arg, owner) for arg in raw.get("args", [])]}
        normalized.append(inline)
        repairs.append({"proposal_step_id": owner,
            "kind": "inline_atomic_step_lifted", "atomic_step_ids": [identifier]})
        return identifier

    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("args"), list):
            normalized.append(step)
            continue
        normalized.append({**step, "args": [lift(arg, step.get("id")) for arg in step["args"]]})
    return normalized, repairs


def _normalize_task_substitutions(graph, steps: list[dict], outputs: dict[str, str]):
    """Expand only an exact harness-declared output substitution, never infer one."""
    if not isinstance(steps, list) or not isinstance(outputs, dict):
        return steps, []
    reserved = {step.get("id") for step in steps if isinstance(step, dict)}
    normalized, repairs = [], []
    symbol_ids = {}
    for step in steps:
        if not isinstance(step, dict):
            normalized.append(step)
            continue
        if step.get("op") == "symbol" and isinstance(step.get("value"), str):
            symbol_ids[step["value"]] = step.get("id")
        if step.get("op") != "substitute" or not isinstance(step.get("args"), list):
            normalized.append(step)
            continue
        matches = []
        for evidence_id in step.get("provenance", []):
            fact = graph.nodes.get(evidence_id)
            if fact is None or fact["kind"] != "task_fact":
                continue
            mapping = fact["value"].get("output_substitutions") or {}
            if not mapping or not set(outputs).intersection(fact["value"].get("required_paths", ())):
                continue
            args, declared = step["args"], step.get("value")
            mapped = len(args) == 1 and declared == mapping
            listed_symbols = {next((value for value, name in symbol_ids.items() if name == arg),
                arg) for arg in args[1:]}
            listed = (len(args) == len(mapping) + 2 and declared is None and
                listed_symbols == {*mapping, *mapping.values()})
            if mapped or listed:
                matches.append((evidence_id, mapping))
        if len(matches) != 1:
            normalized.append(step)
            continue
        evidence_id, mapping = matches[0]
        previous = step["args"][0]
        for index, (source, target) in enumerate(mapping.items(), 1):
            for symbol in (source, target):
                if symbol not in symbol_ids:
                    synthetic = f"NimaSymbol{len(normalized)}"
                    while synthetic in reserved:
                        synthetic += "_"
                    reserved.add(synthetic)
                    normalized.append({"id": synthetic, "op": "symbol", "args": [],
                        "value": symbol, "provenance": [evidence_id],
                        "meaning": f"Task-declared substitution symbol {symbol}",
                        "proposal_step_id": step.get("id")})
                    symbol_ids[symbol] = synthetic
            final = index == len(mapping)
            synthetic = step["id"] if final else f"NimaSub{len(normalized)}"
            if not final:
                while synthetic in reserved:
                    synthetic += "_"
                reserved.add(synthetic)
            normalized.append({"id": synthetic, "op": "substitute",
                "args": [previous, symbol_ids[source], symbol_ids[target]],
                "provenance": [evidence_id],
                "meaning": f"Apply task condition {source}={target}",
                "proposal_step_id": step.get("id")})
            previous = synthetic
        repairs.append({"proposal_step_id": step["id"],
            "kind": "task_declared_substitution_expansion", "evidence_id": evidence_id,
            "atomic_step_ids": [item["id"] for item in normalized
                if item.get("proposal_step_id") == step["id"] and item.get("op") == "substitute"]})
    return normalized, repairs


def _normalize_structural_roles(graph, steps: list[dict], indexed_sets):
    """Make unambiguous scalar syntax and indexed provenance controller-owned."""
    reserved = {step.get("id") for step in steps if isinstance(step, dict)}
    normalized, repairs = [], []
    sets = {item.set_id: item for item in indexed_sets}
    for step in steps:
        if not isinstance(step, dict):
            normalized.append(step)
            continue
        identifier = step.get("id")
        if (step.get("op") == "power" and isinstance(step.get("args"), list)
                and len(step["args"]) == 1 and type(step.get("value")) is int
                and 0 <= step["value"] <= 16):
            synthetic = f"NimaExponent{len(normalized)}"
            while synthetic in reserved:
                synthetic += "_"
            reserved.add(synthetic)
            normalized.append({"id": synthetic, "op": "integer", "args": [],
                "value": step["value"], "provenance": [],
                "meaning": f"Structural exponent for {identifier}",
                "proposal_step_id": identifier})
            rewritten = {**step, "args": [step["args"][0], synthetic],
                "proposal_step_id": identifier}
            rewritten.pop("value", None)
            normalized.append(rewritten)
            repairs.append({"proposal_step_id": identifier,
                "kind": "structural_exponent_expansion", "atomic_step_ids": [synthetic, identifier]})
            continue
        count_of = step.get("index_count_of")
        spec = sets.get(count_of) if isinstance(count_of, str) else None
        if (spec is not None and step.get("op") == "integer"
                and step.get("value") == len(spec.members)
                and f"index:{count_of}" in step.get("provenance", ())
                and f"fact:{spec.fact_id}" not in step.get("provenance", ())
                and f"index:{count_of}" in graph.nodes):
            normalized.append({**step,
                "provenance": [*step["provenance"], f"fact:{spec.fact_id}"]})
            repairs.append({"proposal_step_id": identifier,
                "kind": "indexed_fact_provenance_inherited",
                "evidence_id": f"fact:{spec.fact_id}"})
            continue
        normalized.append(step)
    return normalized, repairs


def compile_graph_program(graph, steps: list[dict], outputs: dict[str, str], indexed_sets=(), *, diagnostics=False, step_limit=128):
    """Return controlled source and a canonical plan; do not execute any model source."""
    if step_limit not in (128, 512) or not isinstance(steps, list) or not 1 <= len(steps) <= step_limit:
        raise ValueError(f"one to {step_limit} atomic calculation steps required")
    if any(isinstance(step, dict) and "proposal_step_id" in step for step in steps):
        raise ValueError("proposal step lineage belongs to the controller")
    steps, controller_repairs = _normalize_inline_references(steps)
    steps, substitution_repairs = _normalize_task_substitutions(graph, steps, outputs)
    controller_repairs.extend(substitution_repairs)
    steps, structural_repairs = _normalize_structural_roles(graph, steps, indexed_sets)
    controller_repairs.extend(structural_repairs)
    steps = _normalize_steps(steps)
    if len(steps) > step_limit:
        raise ValueError(f"normalized calculation exceeds {step_limit} atomic steps")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("at least one named calculation output required")
    names, ancestry, normalized, lines = {}, {}, [], ["import json", "import sympy as sp"]
    if diagnostics:
        lines.extend([
        "def _nima_eval(step, op, inputs, compute):",
        "    try:", "        return compute()", "    except Exception as error:",
        "        print(json.dumps({'operation_error': {'step_id': step, 'operation': op, 'inputs': {k:str(v) for k,v in inputs.items()}, 'error': type(error).__name__, 'diagnostic': str(error)[:1000]}}))",
        "        raise"])
    sets = {item.set_id: item for item in indexed_sets}
    counts, suspected_counts, member_scopes, sums = {}, {}, {}, {}
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict) or set(raw) - {"id", "op", "args", "value", "provenance", "meaning", "application", "proposal_step_id", "index_scope", "index_sum", "index_count_of", "raw_definition"}:
            raise ValueError("invalid atomic calculation step fields")
        identifier, op = raw.get("id"), raw.get("op")
        args, provenance = raw.get("args", []), raw.get("provenance", [])
        if not isinstance(identifier, str) or not _NAME.fullmatch(identifier) or identifier in names:
            raise ValueError("atomic step needs a unique simple ID")
        if not isinstance(op, str) or op not in _ARITY or not isinstance(args, list) or not isinstance(provenance, list):
            raise ValueError("unknown operation or malformed atomic inputs")
        lower, upper = _ARITY[op]
        if not lower <= len(args) <= upper:
            expected = str(lower) if lower == upper else f"{lower}–{upper}"
            if op == "matrix" and not args:
                raise ValueError(f"step {identifier}: matrix requires {expected} input(s); received 0. "
                    "Declare each entry as an earlier atomic step, pass those step IDs in row-major args, "
                    "and set value to [rows, columns]; a literal matrix in value is not an atomic derivation")
            raise ValueError(f"step {identifier}: {op} requires {expected} input(s); received {len(args)}")
        if any(not isinstance(arg, str) or arg not in names for arg in args):
            raise ValueError(f"step {identifier}: inputs must cite earlier atomic step IDs")
        if any(not isinstance(item, str) for item in provenance) or len(set(provenance)) != len(provenance):
            raise ValueError(f"step {identifier}: malformed or duplicate provenance reference")
        inherited_ids = set(args)
        for arg in args:
            inherited_ids.update(ancestry[arg])
        graph_provenance = []
        for item in provenance:
            if item in names:
                if item not in inherited_ids:
                    raise ValueError(f"step {identifier}: local provenance {item} is not an input dependency")
            elif item in graph.nodes:
                graph_provenance.append(item)
            else:
                raise ValueError(f"step {identifier}: unknown provenance reference {item}")
        meaning = raw.get("meaning")
        if not isinstance(meaning, str) or not meaning.strip() or len(meaning) > 300:
            raise ValueError("each atomic operation needs a concise mathematical meaning")
        application = raw.get("application", "algebraic")
        if not isinstance(application, str) or application not in {"algebraic", "physical_rule"}:
            raise ValueError("operation application must be algebraic or an explicit physical rule")
        physical_input_premise = _input_step(raw) and application == "physical_rule"
        scope = raw.get("index_scope")
        sum_set = raw.get("index_sum")
        if scope is not None:
            if (not isinstance(scope, dict) or set(scope) != {"set_id", "member"} or
                    not isinstance(scope["set_id"], str) or not isinstance(scope["member"], str) or
                    scope["set_id"] not in sets or scope["member"] not in sets[scope["set_id"]].members or
                    _input_step(raw) or sum_set is not None):
                raise ValueError(f"step {identifier}: invalid indexed member scope")
            for dependency in inherited_ids:
                if scope["set_id"] in counts.get(dependency, set()):
                    raise ValueError(f"step {identifier}: global cardinality of {scope['set_id']} used inside member {scope['member']}; count is already supplied by the indexed sum")
                if scope["set_id"] in suspected_counts.get(dependency, set()):
                    raise ValueError(f"step {identifier}: integer cited as possible cardinality of {scope['set_id']} inside member {scope['member']} needs an explicit count role or independent provenance")
            member_scopes[identifier] = (scope["set_id"], scope["member"])
        if sum_set is not None:
            if not isinstance(sum_set, str) or sum_set not in sets or op not in {"add", "sum"} or len(args) < 2 or scope is not None:
                raise ValueError(f"step {identifier}: invalid indexed sum")
            actual = [member_scopes.get(arg) for arg in args]
            expected = [(sum_set, member) for member in sets[sum_set].members]
            if len(actual) != len(expected) or sorted(actual, key=str) != sorted(expected, key=str):
                raise ValueError(f"step {identifier}: indexed sum {sum_set} must contain each declared member exactly once")
            sums[identifier] = {sum_set}
        else:
            sums[identifier] = set().union(*(sums.get(arg, set()) for arg in args))
        local_counts = set().union(*(counts.get(arg, set()) for arg in args))
        declared_count = raw.get("index_count_of")
        if declared_count is not None and (not isinstance(declared_count, str) or declared_count not in sets or op != "integer" or
                f"fact:{sets[declared_count].fact_id}" not in graph_provenance or
                raw.get("value") != len(sets[declared_count].members)):
            raise ValueError(f"step {identifier}: invalid indexed cardinality declaration")
        if declared_count is not None:
            local_counts.add(declared_count)
        counts[identifier] = local_counts
        suspected_counts[identifier] = set().union(*(suspected_counts.get(arg, set()) for arg in args))
        if op == "integer" and declared_count is None:
            suspected_counts[identifier].update(spec.set_id for spec in indexed_sets
                if f"fact:{spec.fact_id}" in graph_provenance and raw.get("value") == len(spec.members))
        if not _input_step(raw) and op != "matrix" and "value" in raw:
            raise ValueError("computed operation values belong to the controller, not the agent")
        value = raw.get("value")
        raw_definition = _raw_numeric_definition(graph, identifier, op, value, graph_provenance,
            raw.get("raw_definition"))
        possible_index_count = op == "integer" and any(
            f"fact:{spec.fact_id}" in graph_provenance and value == len(spec.members)
            for spec in indexed_sets)
        # Explicit assumptions supply traceability, never authoritative raw definitions.
        assumption_value = f"{value[0]}/{value[1]}" if op == "rational" and isinstance(value, list) and len(value) == 2 else value
        explicit_assumption = bool(graph_provenance) and all(
            graph.nodes[parent].get("kind") == "assumption" and
            graph.nodes[parent].get("status") == "provisional" and
            graph.nodes[parent].get("value") == assumption_value
            for parent in graph_provenance)
        if (op in {"integer", "rational"} and value not in (0, 1) and
                graph_provenance and declared_count is None and not possible_index_count and
                raw_definition is None and not explicit_assumption):
            raise ValueError(f"step {identifier}: cited numeric input needs an exact raw_definition; a related normalization fact is not its definition")
        if op == "symbol":
            if not isinstance(value, str) or not _NAME.fullmatch(value):
                raise ValueError("symbol input must be a simple name")
            expression = f"sp.Symbol({value!r})"
        elif op == "integer":
            if type(value) is not int or abs(value) > 10**9:
                raise ValueError("integer input out of range")
            expression = f"sp.Integer({value})"
        elif op == "rational":
            if (not isinstance(value, list) or len(value) != 2 or
                    any(type(item) is not int or abs(item) > 10**9 for item in value) or value[1] == 0):
                raise ValueError("rational input requires two bounded integers and nonzero denominator")
            expression = f"sp.Rational({value[0]}, {value[1]})"
        elif op == "imaginary_unit":
            if value is not None:
                raise ValueError("imaginary unit takes no value")
            expression = "sp.I"
        else:
            a = [names[item] for item in args]
            expression = {
                "add": lambda: f"({a[0]} + {a[1]})",
                "subtract": lambda: f"({a[0]} - {a[1]})",
                "multiply": lambda: f"({a[0]} * {a[1]})",
                "divide": lambda: f"({a[0]} / {a[1]})",
                "power": lambda: f"({a[0]} ** {a[1]})",
                "negative": lambda: f"(-{a[0]})",
                "simplify": lambda: f"sp.simplify({a[0]})",
                "differentiate": lambda: f"sp.diff({a[0]}, {a[1]})",
                "kronecker": lambda: f"sp.kronecker_product({a[0]}, {a[1]})",
                "trace": lambda: f"sp.trace({a[0]})",
                "transpose": lambda: f"sp.transpose({a[0]})",
                "adjoint": lambda: f"sp.adjoint({a[0]})",
                "substitute": lambda: f"{a[0]}.subs({a[1]}, {a[2]})",
                "sum": lambda: "(" + " + ".join(a) + ")",
            }
            if op == "matrix":
                if (not isinstance(value, list) or len(value) != 2 or
                        any(type(item) is not int or not 1 <= item <= 8 for item in value) or
                        value[0] * value[1] != len(a)):
                    raise ValueError("matrix needs bounded [rows, columns] matching its atomic entries")
                expression = f"sp.Matrix({value[0]}, {value[1]}, [{', '.join(a)}])"
            else:
                expression = expression[op]()
        local = f"v{index}"
        actual_inputs = ", ".join(f"{arg!r}: {names[arg]}" for arg in args)
        lines.append(f"{local} = _nima_eval({identifier!r}, {op!r}, {{{actual_inputs}}}, lambda: {expression})" if diagnostics else f"{local} = {expression}")
        names[identifier] = local
        ancestry[identifier] = inherited_ids
        normalized.append({"id": identifier, "op": op, "args": list(args),
            "provenance": graph_provenance, "declared_provenance": list(provenance),
            "meaning": meaning.strip(),
            "application": "algebraic" if physical_input_premise else application,
            "declared_physical_input_premise": physical_input_premise,
            "proposal_step_id": raw.get("proposal_step_id", identifier),
            **({"index_scope": dict(scope)} if scope is not None else {}),
            **({"index_sum": sum_set} if sum_set is not None else {}),
            **({"index_count_of": sorted(local_counts)} if op == "integer" and local_counts else {}),
            **({"raw_definition": raw_definition} if raw_definition is not None else {}),
            **({"value": value} if _input_step(raw) else {}),
            **({"shape": value} if op == "matrix" else {})})
    if (any(not isinstance(key, str) or not key or not isinstance(value, str) or value not in names
            for key, value in outputs.items()) or len(outputs) > 64):
        raise ValueError("outputs must name existing atomic step IDs")
    for spec in indexed_sets:
        for path in spec.required_paths:
            if path not in outputs:
                continue  # Local method outputs are not a final-answer submission.
            if path not in outputs or spec.set_id not in sums.get(outputs[path], set()):
                raise ValueError(f"output {path}: required indexed sum {spec.set_id} is missing")
    # One JSON result binds every intermediate value to the operation that produced it.
    step_values = ", ".join(f"{identifier!r}: str({local})" for identifier, local in names.items())
    output_values = ", ".join(f"{key!r}: str({names[value]})" for key, value in outputs.items())
    lines.append(f"print(json.dumps({{'steps': {{{step_values}}}, 'outputs': {{{output_values}}}}}))")
    source = "\n".join(lines) + "\n"
    if len(source) > 32000:
        raise ValueError("compiled calculation exceeds 32000 source characters; split the calculation")
    ast.parse(source)
    return {"source": source, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "steps": normalized, "outputs": dict(outputs), "controller_repairs": controller_repairs}
