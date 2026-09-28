"""Operator-algebra and premise-admissibility regression born from the v4 gate."""
import json

import pytest
import sympy as sp

from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.symbolic_transport import configured_symbolic_worker

pytestmark = pytest.mark.symbolic


def _step(identifier, op, args=(), *, value=None, provenance=(), meaning=None):
    step = {"id": identifier, "op": op, "args": list(args),
        "provenance": list(provenance), "meaning": meaning or identifier.replace("_", " ")}
    if value is not None or op in {"integer", "symbol"}:
        step["value"] = value
    return step


def _exchange_steps(extra_factor=None):
    steps = [
        _step("zero", "integer", value=0),
        _step("one", "integer", value=1),
        _step("minus_one", "negative", ["one"]),
        _step("imaginary", "imaginary_unit"),
        _step("minus_imaginary", "negative", ["imaginary"]),
        _step("two", "add", ["one", "one"], meaning="Construct two arithmetically"),
        _step("half", "divide", ["one", "two"], meaning="Spin-half normalization"),
        _step("J", "symbol", value="J", provenance=["task"], meaning="Task-given exchange coupling"),
        _step("sigma_x", "matrix", ["zero", "one", "one", "zero"], value=[2, 2]),
        _step("sigma_y", "matrix", ["zero", "minus_imaginary", "imaginary", "zero"], value=[2, 2]),
        _step("sigma_z", "matrix", ["one", "zero", "zero", "minus_one"], value=[2, 2]),
        _step("Sx", "multiply", ["half", "sigma_x"]),
        _step("Sy", "multiply", ["half", "sigma_y"]),
        _step("Sz", "multiply", ["half", "sigma_z"]),
        _step("xx", "kronecker", ["Sx", "Sx"]),
        _step("yy", "kronecker", ["Sy", "Sy"]),
        _step("zz", "kronecker", ["Sz", "Sz"]),
        _step("xy", "add", ["xx", "yy"]),
        _step("exchange", "add", ["xy", "zz"]),
        _step("H", "multiply", ["J", "exchange"]),
        _step("H_squared", "multiply", ["H", "H"]),
        _step("trace_raw", "trace", ["H_squared"]),
        _step("trace", "simplify", ["trace_raw"]),
    ]
    if extra_factor == "literal_four":
        steps.append(_step("factor", "integer", value=4,
            meaning="Introduced physical multiplicity of four"))
    elif extra_factor == "constructed_two":
        steps.append(_step("factor", "add", ["one", "one"],
            meaning="Unsupported extra physical shell multiplier"))
    if extra_factor == "duplicate_sum":
        steps.append(_step("scaled_trace", "add", ["trace", "trace"],
            meaning="Unsupported duplicate of a derived physical contribution"))
    elif extra_factor == "reciprocal_divide":
        steps.append(_step("scaled_trace", "divide", ["trace", "half"],
            meaning="Unsupported physical factor expressed as division by one half"))
    elif extra_factor:
        steps.extend([
            _step("scaled_trace", "multiply", ["factor", "trace"],
                meaning="Apply the asserted extra physical multiplier"),
            _step("reconstructed_trace", "divide", ["scaled_trace", "factor"],
                meaning="Downstream reconstruction of the original trace"),
        ])
    return steps


def _run(extra_factor=None):
    graph = SingleCalculationGraph(
        "For two spin-half particles with exchange coupling J, compute the squared trace of J S1·S2.",
        (), ("trace",))
    output_id = "scaled_trace" if extra_factor else "trace"
    plan = compile_graph_program(graph, _exchange_steps(extra_factor), {"trace": output_id})
    staged = graph.stage_compiled_plan(plan, "operator-r1", "Two-spin operator calculation")
    assert len(staged) == len(plan["steps"])
    assert not any(node["kind"] == "calculation_output" for node in graph.nodes.values())
    result = configured_symbolic_worker().run(plan["source"], 20)
    assert result["outcome"] == "executed" and result["exit_code"] == 0
    payload = json.loads(result["stdout"])
    recorded = graph.record_compiled_execution(plan, result, "operator-r1")
    assert set(payload["steps"]) == set(staged)
    assert all(recorded["steps"][step_id]["value"] == value
        for step_id, value in payload["steps"].items())
    assert len([node for node in graph.nodes.values()
        if node["kind"] in {"calculation_input", "calculation_operation"}]) == len(plan["steps"])
    assert graph.receipts["operator-r1"]["source_sha256"] == plan["source_sha256"]
    return graph, recorded


def test_operator_algebra_matches_independent_spin_eigenvalue_oracle():
    graph, recorded = _run()
    value = recorded["outputs"]["trace"]["value"]
    J = sp.Symbol("J")
    assert sp.simplify(sp.sympify(value) - (3 * (J / 4)**2 + (-3 * J / 4)**2)) == 0
    status = graph.submit({"trace": value}, {"trace": recorded["outputs"]["trace"]["id"]})
    assert status["ready"] is True and status["mathematically_verified"] is False


def test_literal_physical_multiplier_remains_open_despite_exact_python_and_reconstruction():
    graph, recorded = _run("literal_four")
    factor = recorded["steps"]["factor"]
    output = recorded["outputs"]["trace"]
    assert factor["id"] in graph.obligations
    assert graph.obligations[factor["id"]]["status"] == "open"
    status = graph.submit({"trace": output["value"]}, {"trace": output["id"]})
    assert status["ready"] is False and status["unresolved_root_ids"] == [factor["id"]]
    with pytest.raises(ValueError, match="downstream consequences"):
        graph.substantiate(factor["id"], recorded["steps"]["reconstructed_trace"]["id"],
            "The reconstructed trace agrees.")


def test_constructed_physical_multiplier_cannot_be_admitted_by_arithmetic_alone():
    graph, recorded = _run("constructed_two")
    output = recorded["outputs"]["trace"]
    status = graph.submit({"trace": output["value"]}, {"trace": output["id"]})
    assert status["ready"] is False
    root = recorded["steps"]["scaled_trace"]
    assert status["unresolved_root_ids"] == [root["id"]]
    assert root["application_reason"] == "structural_scalar_applied_to_task_quantity"
    with pytest.raises(ValueError, match="task/source-grounded"):
        graph.substantiate(root["id"], recorded["steps"]["reconstructed_trace"]["id"],
            "The downstream calculation reconstructs the original trace.")
    with pytest.raises(ValueError, match="task fact, source passage or independent calculation output"):
        graph.substantiate_application(root["id"], recorded["steps"]["reconstructed_trace"]["id"],
            "The downstream calculation reconstructs the original trace.")


def test_duplicate_derived_contribution_is_not_routine_algebraic_support():
    graph, recorded = _run("duplicate_sum")
    output = recorded["outputs"]["trace"]
    root = recorded["steps"]["scaled_trace"]
    status = graph.submit({"trace": output["value"]}, {"trace": output["id"]})
    assert root["application_reason"] == "duplicate_derived_contribution"
    assert status["ready"] is False and status["unresolved_root_ids"] == [root["id"]]


def test_reciprocal_division_does_not_bypass_physical_multiplier_obligation():
    graph, recorded = _run("reciprocal_divide")
    output = recorded["outputs"]["trace"]
    root = recorded["steps"]["scaled_trace"]
    status = graph.submit({"trace": output["value"]}, {"trace": output["id"]})
    assert root["application_reason"] == "structural_reciprocal_applied_to_task_quantity"
    assert status["ready"] is False and status["unresolved_root_ids"] == [root["id"]]


def test_explicit_physical_operation_needs_an_exact_harness_formula_not_a_consequence():
    from nima_semantica.math_task_contract import MathTaskFact
    fact = MathTaskFact(fact_id="square", kind="definition", quote="x**2",
        expression="x**2", required_paths=("result",))
    graph = SingleCalculationGraph("Compute x**2 for a task variable x.", (fact,), ("result",))
    plan = compile_graph_program(graph, [
        _step("x", "symbol", value="x", provenance=["task"]),
        {**_step("square", "multiply", ["x", "x"], meaning="Apply an asserted physical squaring rule"),
            "application": "physical_rule"},
    ], {"result": "square"})
    recorded = graph.record_compiled_execution(plan,
        configured_symbolic_worker().run(plan["source"], 20), "physical-rule-r1")
    root = recorded["steps"]["square"]
    answer = recorded["outputs"]["result"]
    assert graph.submit({"result": answer["value"]}, {"result": answer["id"]})["ready"] is False
    with pytest.raises(ValueError, match="task/source-grounded"):
        graph.substantiate(root["id"], answer["id"], "The computed consequence matches.")
    support = graph.substantiate_application(root["id"], "fact:square",
        "The harness supplied this exact formula for the result.")
    assert support["status"] == "agent_supported_not_verified"
    candidate = graph.submit({"result": answer["value"]}, {"result": answer["id"]})
    assert candidate["ready"] is True and candidate["mathematically_verified"] is False


def test_irrelevant_source_citation_is_visible_and_cannot_discharge_applicability():
    graph, recorded = _run("constructed_two")
    root = recorded["steps"]["scaled_trace"]
    answer = recorded["outputs"]["trace"]
    source = graph.record_source("unrelated-region", "This passage discusses an unrelated subject.", "rev-1")
    support = graph.substantiate_application(root["id"], source["id"],
        "Agent asserts that the cited passage licenses an extra physical multiplier.")
    candidate = graph.submit({"trace": answer["value"]}, {"trace": answer["id"]})
    assert support["origin"]["target_operation_id"] == root["id"]
    assert graph.obligations[root["id"]]["evidence_id"] == source["id"]
    assert support["status"] == "agent_cited_not_verified"
    assert candidate["ready"] is False and candidate["mathematically_verified"] is False
    assert root["id"] in candidate["unresolved_root_ids"]
