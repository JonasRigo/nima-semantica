"""Machine-checkable harness conditions and exact physical-applicability citations."""
import subprocess
import sys

import pytest

from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_task_contract import MathTaskFact, validate_task_facts


def _run(graph, steps, output):
    plan = compile_graph_program(graph, steps, {"beta_isotropic": output})
    run = subprocess.run([sys.executable, "-c", plan["source"]], text=True,
        capture_output=True, check=True, timeout=20)
    return graph.record_compiled_execution(plan, {"outcome": "executed", "exit_code": 0,
        "stdout": run.stdout, "stderr": run.stderr}, "condition-r1")


def _step(identifier, op, args=(), value=None, provenance=()):
    item = {"id": identifier, "op": op, "args": list(args),
        "provenance": list(provenance), "meaning": identifier}
    if op in {"symbol", "integer"}:
        item["value"] = value
    return item


def _graph():
    task = "Use the isotropic condition L_z=L_perp=L to report beta_isotropic."
    fact = MathTaskFact(fact_id="isotropy", kind="condition", quote="L_z=L_perp=L",
        output_substitutions={"L_z": "L", "L_perp": "L"},
        required_paths=("beta_isotropic",))
    validate_task_facts(task, ("beta_isotropic",), (fact,))
    return SingleCalculationGraph(task, (fact,), ("beta_isotropic",))


def test_missing_isotropic_substitution_is_an_open_output_obligation():
    graph = _graph()
    steps = [_step("L_perp", "symbol", value="L_perp", provenance=("task",)),
        _step("square", "multiply", ("L_perp", "L_perp"))]
    result = _run(graph, steps, "square")
    output = result["outputs"]["beta_isotropic"]
    assert output["value"] == "L_perp**2"
    assert output["missing_substitutions"] == [{"fact_id": "isotropy",
        "source": "L_perp", "target": "L"}]
    assert output["id"] in graph.submit({"beta_isotropic": "L_perp**2"},
        {"beta_isotropic": output["id"]})["unresolved_root_ids"]


def test_explicit_isotropic_substitution_removes_only_the_output_condition():
    graph = _graph()
    steps = [_step("L_perp", "symbol", value="L_perp", provenance=("task",)),
        _step("L", "symbol", value="L", provenance=("task",)),
        _step("square", "multiply", ("L_perp", "L_perp")),
        _step("isotropic", "substitute", ("square", "L_perp", "L"))]
    result = _run(graph, steps, "isotropic")
    output = result["outputs"]["beta_isotropic"]
    assert output["value"] == "L**2"
    assert output["missing_substitutions"] == []
    assert output["id"] not in graph.obligations


def test_condition_must_be_explicitly_bound_to_task_wording():
    fact = MathTaskFact(fact_id="bad", kind="condition", quote="L_z=L_perp=L",
        output_substitutions={"rho": "L"}, required_paths=("beta_isotropic",))
    with pytest.raises(ValueError, match="not bound to exact task wording"):
        validate_task_facts("Use L_z=L_perp=L.", ("beta_isotropic",), (fact,))


def test_broad_task_citation_cannot_resolve_a_physical_operation():
    graph = SingleCalculationGraph("For x, calculate x squared.", (), ("result",))
    steps = [_step("x", "symbol", value="x", provenance=("task",)),
        {**_step("square", "multiply", ("x", "x")), "application": "physical_rule"}]
    plan = compile_graph_program(graph, steps, {"result": "square"})
    run = subprocess.run([sys.executable, "-c", plan["source"]], text=True,
        capture_output=True, check=True, timeout=20)
    result = graph.record_compiled_execution(plan, {"outcome": "executed", "exit_code": 0,
        "stdout": run.stdout, "stderr": run.stderr}, "physical-r1")
    root = result["steps"]["square"]
    citation = graph.substantiate_application(root["id"], "task", "The task mentions a square.")
    assert citation["status"] == "agent_cited_not_verified"
    assert graph.obligations[root["id"]]["status"] == "open"


def test_exact_path_scoped_harness_formula_closes_only_its_operation_root():
    task = "For x, compute x**2."
    fact = MathTaskFact(fact_id="square", kind="definition", quote="x**2",
        expression="x**2", required_paths=("result",))
    validate_task_facts(task, ("result",), (fact,))
    graph = SingleCalculationGraph(task, (fact,), ("result",))
    steps = [_step("x", "symbol", value="x", provenance=("task",)),
        {**_step("square", "multiply", ("x", "x")), "application": "physical_rule"}]
    plan = compile_graph_program(graph, steps, {"result": "square"})
    run = subprocess.run([sys.executable, "-c", plan["source"]], text=True,
        capture_output=True, check=True, timeout=20)
    result = graph.record_compiled_execution(plan, {"outcome": "executed", "exit_code": 0,
        "stdout": run.stdout, "stderr": run.stderr}, "physical-r1")
    root = result["steps"]["square"]
    citation = graph.substantiate_application(root["id"], "fact:square", "Exact harness formula.")
    assert citation["status"] == "agent_supported_not_verified"
    assert graph.obligations[root["id"]]["status"] == "agent_supported_not_verified"


@pytest.mark.parametrize("formula,scope", [("x", "result"), ("x**2", "other")])
def test_unmatched_or_wrong_path_harness_formula_cannot_resolve_operation(formula, scope):
    task = "Compute x**2 for x; also record x."
    fact = MathTaskFact(fact_id="formula", kind="definition", quote=formula,
        expression=formula, required_paths=(scope,))
    graph = SingleCalculationGraph(task, (fact,), ("result", "other"))
    steps = [_step("x", "symbol", value="x", provenance=("task",)),
        {**_step("square", "multiply", ("x", "x")), "application": "physical_rule"}]
    plan = compile_graph_program(graph, steps, {"result": "square"})
    run = subprocess.run([sys.executable, "-c", plan["source"]], text=True,
        capture_output=True, check=True, timeout=20)
    result = graph.record_compiled_execution(plan, {"outcome": "executed", "exit_code": 0,
        "stdout": run.stdout, "stderr": run.stderr}, "physical-r1")
    root = result["steps"]["square"]
    citation = graph.substantiate_application(root["id"], "fact:formula", "A possible formula.")
    assert citation["status"] == "agent_cited_not_verified"
    assert graph.obligations[root["id"]]["status"] == "open"
