"""Bounded deterministic search and dependency traversal, without model calls."""

from .addition_contracts import SearchCounterexamplesInput, TraceDependenciesInput
from .models import identity
from .verification import holds


def _independent_value(expression, assignment):
    """Second evaluator used only by witness verification.

    Keep this implementation structurally separate from ``verification.evaluate``
    so a shared evaluator defect cannot make a discovered witness self-certify.
    """
    if expression.op == "constant":
        return expression.value
    if expression.op == "variable":
        return assignment[expression.name]
    left = _independent_value(expression.left, assignment)
    right = _independent_value(expression.right, assignment)
    if expression.op == "add":
        return left + right
    if expression.op == "sub":
        return left - right
    if expression.op == "mul":
        return left * right
    raise ValueError("unsupported encoded expression")


def _independently_refutes(claim, assignment):
    left = _independent_value(claim.left, assignment)
    right = _independent_value(claim.right, assignment)
    if claim.relation == "eq":
        holds_claim = left == right
    elif claim.relation == "le":
        holds_claim = left <= right
    elif claim.relation == "lt":
        holds_claim = left < right
    else:
        raise ValueError("unsupported encoded relation")
    return not holds_claim, left, right


def search_integer_witness(payload):
    """Enumerate a declared integer box; never infer universal truth from absence."""
    task = SearchCounterexamplesInput.model_validate(payload)
    count = (task.upper - task.lower + 1) ** len(task.claim.variables)
    tried = 0
    witness = None
    width = task.upper - task.lower + 1
    for index in range(min(count, task.max_evaluations)):
        tried += 1
        assignment = {}
        for variable in reversed(task.claim.variables):
            index, digit = divmod(index, width)
            assignment[variable] = task.lower + digit
        if not holds(task.claim, assignment):
            witness = assignment
            break
    return {
        "task": task.model_dump(mode="json"),
        "claim_id": identity(task.claim),
        "witness": witness,
        "evaluations": tried,
        "box_size": count,
        "outcome": "candidate_found" if witness is not None else "none_found_in_budget",
        "box_exhausted": witness is None and tried == count,
        "truncated": witness is None and tried < count,
        "scope": "Encoded universally quantified integer polynomial claim only; no extra prose assumptions are applied.",
        "source_correspondence_verified": False,
    }


def check_integer_witness(search):
    """Re-evaluate the proposed data against the unchanged encoded claim."""
    task = SearchCounterexamplesInput.model_validate(search["task"])
    if identity(task.claim) != search["claim_id"]:
        raise ValueError("encoded claim identity changed")
    witness = search["witness"]
    if witness is None:
        return {
            **search,
            "outcome": "none_found_in_budget",
            "verified_refutation": False,
            "findings": [
                {
                    "finding": "No witness found in the evaluated search region; this is not a proof."
                }
            ],
        }
    if set(witness) != set(task.claim.variables) or any(
        type(v) is not int or not task.lower <= v <= task.upper
        for v in witness.values()
    ):
        raise ValueError("witness outside declared integer search domain")
    refutes, left, right = _independently_refutes(task.claim, witness)
    return {
        **search,
        "outcome": "encoded_claim_refuted" if refutes else "witness_failed_check",
        "verified_refutation": refutes,
        "mathematically_verified": False,
        "findings": [
            {
                "witness": witness,
                "left": left,
                "right": right,
                "refutes_encoded_claim": refutes,
            }
        ],
    }


def trace_dependencies(payload):
    """Trace declared directed dependencies, preserving paths and cycle evidence.

    Relations are declarations, not proved implications. Alternative branches
    remain separate, and depth/path bounds are explicit in the returned table.
    """
    task = TraceDependenciesInput.model_validate(payload)
    nodes = {n.id: n for n in task.nodes}
    if len(nodes) != len(task.nodes) or len(set(task.targets)) != len(task.targets):
        raise ValueError("duplicate node or target IDs")
    if not set(task.targets) <= nodes.keys():
        raise ValueError("unknown dependency target")
    adjacency = {key: [] for key in nodes}
    for edge in task.edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError("nonexistent dependency endpoint")
        adjacency[edge.source].append(edge.target)
    frontier = [(target, [target]) for target in task.targets]
    paths, truncated = [], False
    while frontier and len(paths) < task.max_paths:
        current, path = frontier.pop()
        cycle = current in path[:-1]
        depth_cut = (
            not cycle and bool(adjacency[current]) and len(path) - 1 >= task.max_hops
        )
        paths.append(
            {
                "path": path,
                "node_id": current,
                "statement": nodes[current].statement,
                "status": nodes[current].status,
                "cycle": cycle,
                "leaf": not adjacency[current],
                "depth_limited": depth_cut,
            }
        )
        truncated |= depth_cut
        if not cycle and not depth_cut:
            frontier.extend(
                (child, [*path, child]) for child in reversed(adjacency[current])
            )
    return {
        "findings": paths,
        "truncated": truncated or bool(frontier),
        "cycles": [p["path"] for p in paths if p["cycle"]],
        "scope": "Declared dependencies only; leaves are not automatically assumptions and edges are not proved implications.",
        "mathematically_verified": False,
        "source_correspondence_verified": False,
    }
