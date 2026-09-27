"""Standalone regressions for the maintained graph implementation."""
import json
import hashlib
from asyncio import CancelledError
from pathlib import Path

import pytest

from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_task_contract import MathTaskFact
from nima_semantica.models import identity
from nima_semantica.providers import Invocation, ModelManifest


MANIFEST = ModelManifest(provider="fixture", model="single-graph", revision="1", parameters={})


def execution(value=None, *, failed=False):
    return {"outcome": "code_failed" if failed else "executed", "exit_code": 1 if failed else 0,
        "stdout": "" if failed else json.dumps(value),
        "stderr": "ValueError: 1 is not in list" if failed else ""}


def test_atomic_graph_preserves_unresolved_root_and_exact_output_paths():
    graph = SingleCalculationGraph("derive a generic shell coefficient", (), ("beta",))
    placement = graph.add_step(kind="assumption", statement="There are two distinct placements.",
        value=2, depends_on=[])
    nodes = graph.record_execution("print(...)" , execution({"sum": {"beta": "4*rho*J**2"}}),
        "receipt-1", [placement["id"]])
    assert len(nodes) == 1 and nodes[0]["origin"]["output_path"] == "sum.beta"
    candidate = graph.submit({"beta": "4*rho*J**2"}, {"beta": nodes[0]["id"]})
    assert candidate["unresolved_root_ids"] == [placement["id"]]
    assert candidate["mathematically_verified"] is False
    with pytest.raises(ValueError, match="differs"):
        graph.submit({"beta": "2*rho*J**2"}, {"beta": nodes[0]["id"]})


def test_experiment_and_atomic_claim_values_form_a_dag_with_open_frontier():
    graph = SingleCalculationGraph("derive a coefficient", (), ("beta",))
    hypothesis = graph.add_step(kind="hypothesis", statement="The shell coefficient is two.",
        value=2, depends_on=[])
    derivation = graph.add_step(kind="derivation", statement="Conditional output formulas.",
        value={"beta": 2, "other": 4}, depends_on=[hypothesis["id"]])
    assert derivation["atomic_nodes"]["beta"] in graph.nodes
    beta_node = graph.nodes[derivation["atomic_nodes"]["beta"]]
    assert beta_node["depends_on"] == [derivation["id"]]
    assert beta_node["status"] == "provisional"
    assert beta_node["origin"]["kind"] == "agent_proposal"
    proposal = graph.submit({"beta": 2}, {"beta": derivation["id"]})
    assert proposal["support_nodes"]["beta"] == beta_node["id"]
    assert proposal["ready"] is False
    assert proposal["unresolved_root_ids"] == [hypothesis["id"], beta_node["id"]]
    assert graph.frontier()["open_obligations"][0]["root_id"] == hypothesis["id"]

    experiment = graph.record_execution("print(2)", execution({"shell_count": 2}),
        "independent-r1", purpose="Count independent electron and hole terms")
    observation = experiment[0]
    plan = graph.nodes[observation["depends_on"][0]]
    assert plan["kind"] == "experiment" and plan["value"]["purpose"].startswith("Count independent")
    assert plan["depends_on"] == ["task"]
    with pytest.raises(ValueError, match="downstream consequences"):
        graph.substantiate(hypothesis["id"], beta_node["id"], "Circular check")
    support = graph.substantiate(hypothesis["id"], observation["id"],
        "The independent count matches the proposed coefficient.")
    assert support["kind"] == "substantiation"
    assert graph.submit({"beta": 2}, {"beta": derivation["id"]})["unresolved_root_ids"] == [beta_node["id"]]
    graph.substantiate(beta_node["id"], observation["id"],
        "The separately observed scalar value matches this proposed output.")
    accepted = graph.submit({"beta": 2}, {"beta": derivation["id"]})
    assert accepted["ready"] is True and accepted["scientific_status"] == "agent_supported_not_verified"
    assert accepted["mathematically_verified"] is False


def test_composite_hypothesis_rejected_without_partial_graph_mutation():
    graph = SingleCalculationGraph("derive coefficients", (), ("beta",))
    with pytest.raises(ValueError, match="one scalar hypothesis"):
        graph.add_step(kind="hypothesis", statement="Two unrelated suppositions.",
            value={"a": 1, "b": 2}, depends_on=[])
    assert list(graph.nodes) == ["task"]


def test_model_only_claim_cannot_become_supported_answer_by_relabelling():
    graph = SingleCalculationGraph("derive a coefficient", (), ("beta",))
    claim = graph.add_step(kind="claim", statement="The coefficient is four.",
        value=4, depends_on=["task"])
    proposal = graph.submit({"beta": 4}, {"beta": claim["id"]})
    assert proposal["ready"] is False
    assert proposal["unresolved_root_ids"] == [claim["id"]]
    assert proposal["missing_evidence_node_ids"] == [claim["id"]]
    assert proposal["mathematically_verified"] is False


def test_evidence_ancestor_does_not_upgrade_an_unchecked_model_transformation():
    graph = SingleCalculationGraph("calculate a new value", (), ("$",))
    observation = graph.record_execution("print(2)", execution(2), "value-r1")[0]
    claim = graph.add_step(kind="derivation", statement="Twice the observed value is five.",
        value=5, depends_on=[observation["id"]])
    proposal = graph.submit(5, {"$": claim["id"]})
    assert proposal["ready"] is False
    assert proposal["unresolved_root_ids"] == [claim["id"]]
    with pytest.raises(ValueError, match="differs"):
        graph.substantiate(claim["id"], observation["id"], "The parent is not this value.")


def test_bundled_kondo_guess_stays_provisional_per_output_despite_task_fact_links():
    graph = SingleCalculationGraph("derive shell beta functions", (), ("beta_z", "beta_perp"))
    bundle = graph.add_step(kind="derivation", statement="Assume both shells double this coefficient.",
        value={"beta_z": "4*rho*L_perp**2", "beta_perp": "4*rho*L_z*L_perp"},
        depends_on=["task"])
    children = [graph.nodes[bundle["atomic_nodes"][path]] for path in graph.required_paths]
    assert bundle["status"] == "provisional" and bundle["origin"]["kind"] == "agent_proposal"
    assert all(child["status"] == "provisional" for child in children)
    assert all(child["origin"]["proposal_id"] == bundle["id"] for child in children)
    assert all(graph.obligations[child["id"]]["status"] == "open" for child in children)
    with pytest.raises(ValueError, match="atomic child"):
        graph.add_step(kind="claim", statement="Use the bundled result.", value=1,
            depends_on=[bundle["id"]])
    answer = {"beta_z": "4*rho*L_perp**2", "beta_perp": "4*rho*L_z*L_perp"}
    initial = graph.submit(answer, {path: bundle["id"] for path in graph.required_paths})
    assert initial["ready"] is False
    assert set(initial["unresolved_root_ids"]) == {child["id"] for child in children}

    # This reproduces the paid trace's printed, non-JSON consequence check.
    attempt = graph.record_execution("print(0)", {"outcome": "executed", "exit_code": 0,
        "stdout": "0\n4*L**2*rho\n", "stderr": ""}, "consequence-receipt")
    assert attempt[0]["status"] == "exploratory"
    with pytest.raises(ValueError, match="failed or still conditional"):
        graph.substantiate(children[0]["id"], attempt[0]["id"], "This is only a consequence.")
    assert graph.submit(answer, {path: bundle["id"] for path in graph.required_paths})["ready"] is False




def test_independent_scalar_observations_can_support_proposal_children_separately():
    graph = SingleCalculationGraph("calculate a and b", (), ("a", "b"))
    bundle = graph.add_step(kind="derivation", statement="Proposed two outputs.",
        value={"a": 2, "b": 3}, depends_on=["task"])
    evidence = graph.record_execution("print(json.dumps({'a': 2, 'b': 3}))",
        execution({"a": 2, "b": 3}), "independent-receipt")
    graph.substantiate(bundle["atomic_nodes"]["a"], evidence[0]["id"],
        "The independent calculation gives a=2.")
    partial = graph.submit({"a": 2, "b": 3}, {"a": bundle["id"], "b": bundle["id"]})
    assert partial["unresolved_root_ids"] == [bundle["atomic_nodes"]["b"]]
    graph.substantiate(bundle["atomic_nodes"]["b"], evidence[1]["id"],
        "The independent calculation gives b=3.")
    accepted = graph.submit({"a": 2, "b": 3}, {"a": bundle["id"], "b": bundle["id"]})
    assert accepted["ready"] is True
    assert accepted["mathematically_verified"] is False


def test_controller_compiles_atomic_code_and_exposes_guessed_multiplier_as_root():
    graph = SingleCalculationGraph("Find beta from rho and L_perp.", (), ("beta",))
    plan = compile_graph_program(graph, [
        {"id": "rho", "op": "symbol", "args": [], "value": "rho", "provenance": ["task"],
            "meaning": "Per-spin density of states"},
        {"id": "coupling", "op": "symbol", "args": [], "value": "L_perp", "provenance": ["task"],
            "meaning": "Transverse coupling"},
        {"id": "factor", "op": "integer", "args": [], "value": 4, "provenance": [],
            "meaning": "Proposed shell multiplicity"},
        {"id": "square", "op": "multiply", "args": ["coupling", "coupling"],
            "provenance": [], "meaning": "Square the transverse coupling"},
        {"id": "density_term", "op": "multiply", "args": ["rho", "square"],
            "provenance": [], "meaning": "Apply density of states"},
        {"id": "beta", "op": "multiply", "args": ["factor", "density_term"],
            "provenance": [], "meaning": "Apply proposed multiplicity"},
    ], {"beta": "beta"})
    assert "4*rho*L_perp" not in plan["source"]
    staged = graph.stage_compiled_plan(plan, "compiled-r1", "Inspect multiplicity")
    assert len(staged) == 6 and all(node["kind"] == "calculation_plan_step" for node in staged.values())
    assert not any(node["kind"] == "calculation_output" for node in graph.nodes.values())
    executed = CompiledWorker().run(plan["source"], 10)
    recorded = graph.record_compiled_execution(plan, executed, "compiled-r1", "Inspect multiplicity")
    factor = recorded["steps"]["factor"]
    assert factor["origin"]["kind"] == "controller_compiled"
    assert graph.obligations[factor["id"]]["status"] == "open"
    output = recorded["outputs"]["beta"]
    assert output["origin"]["source_sha256"] == plan["source_sha256"]
    assert len([node for node in graph.nodes.values() if node["kind"] == "calculation_operation"]) == 3
    proposal = graph.submit({"beta": "4*rho*L_perp**2"}, {"beta": output["id"]})
    assert proposal["ready"] is False and proposal["unresolved_root_ids"] == [factor["id"]]
    with pytest.raises(ValueError, match="downstream consequences"):
        graph.substantiate(factor["id"], output["id"], "A consequence cannot certify its input.")


def test_controller_expands_associative_product_without_certifying_physical_input():
    graph = SingleCalculationGraph("Find beta from rho and L.", (), ("beta",))
    plan = compile_graph_program(graph, [
        {"id": "rho", "op": "symbol", "args": [], "value": "rho", "provenance": ["task"],
            "meaning": "Density of states"},
        {"id": "L", "op": "symbol", "args": [], "value": "L", "provenance": ["task"],
            "meaning": "Coupling"},
        {"id": "two", "op": "integer", "args": [], "value": 2, "provenance": [],
            "meaning": "Claimed shell factor", "application": "physical_rule"},
        {"id": "beta", "op": "multiply", "args": ["two", "rho", "L", "L"],
            "provenance": [], "meaning": "Claimed beta coefficient"},
    ], {"beta": "beta"})
    assert [step["id"] for step in plan["steps"]][-3:] == ["NimaFold3_1", "NimaFold4_2", "beta"]
    assert all(len(step["args"]) == 2 for step in plan["steps"] if step["op"] == "multiply")
    assert plan["steps"][2]["declared_physical_input_premise"] is True
    assert all(step["proposal_step_id"] == "beta" for step in plan["steps"][-3:])
    result = graph.record_compiled_execution(plan, CompiledWorker().run(plan["source"], 10), "factor-r1")
    factor = result["steps"]["two"]
    assert factor["status"] == "provisional"
    assert graph.obligations[factor["id"]]["status"] == "open"
    assert graph.submit({"beta": "2*L**2*rho"}, {"beta": result["outputs"]["beta"]["id"]})["ready"] is False


def test_declared_physical_zero_is_not_treated_as_structural_zero():
    graph = SingleCalculationGraph("Determine the physical zero flow.", (), ("flow",))
    plan = compile_graph_program(graph, [{"id": "zero", "op": "integer", "args": [],
        "value": 0, "provenance": [], "meaning": "Claimed cancellation of flow",
        "application": "physical_rule"}], {"flow": "zero"})
    result = graph.record_compiled_execution(plan, CompiledWorker().run(plan["source"], 10), "zero-r1")
    assert result["steps"]["zero"]["status"] == "provisional"
    assert graph.submit({"flow": "0"}, {"flow": result["outputs"]["flow"]["id"]})["ready"] is False


def test_incomplete_power_gets_precise_local_diagnostic():
    graph = SingleCalculationGraph("Compute x squared.", (), ("result",))
    with pytest.raises(ValueError, match="step squared: power requires 2 input\\(s\\); received 1"):
        compile_graph_program(graph, [
            {"id": "x", "op": "symbol", "args": [], "value": "x", "provenance": ["task"],
                "meaning": "Task variable"},
            {"id": "squared", "op": "power", "args": ["x"], "provenance": [],
                "meaning": "Proposed square"},
    ], {"result": "squared"})


def test_literal_matrix_gets_atomic_repair_diagnostic():
    graph = SingleCalculationGraph("Build the Pauli x matrix.", (), ("result",))
    with pytest.raises(ValueError, match="Declare each entry as an earlier atomic step"):
        compile_graph_program(graph, [
            {"id": "pauli_x", "op": "matrix", "args": [], "value": "[[0,1],[1,0]]",
                "provenance": ["task"], "meaning": "Proposed Pauli matrix"},
        ], {"result": "pauli_x"})


def test_computed_provenance_inherits_input_ids_but_does_not_certify_source_claim():
    graph = SingleCalculationGraph("Calculate x squared.", (), ("result",))
    source = graph.record_source("method:1", "A source discusses squaring a variable.", "rev1")
    plan = compile_graph_program(graph, [
        {"id": "x", "op": "symbol", "args": [], "value": "x", "provenance": ["task"],
            "meaning": "Task variable"},
        {"id": "square", "op": "multiply", "args": ["x", "x"],
            "provenance": ["x", source["id"]], "meaning": "Proposed physical square",
            "application": "physical_rule"},
    ], {"result": "square"})
    assert plan["steps"][1]["provenance"] == [source["id"]]
    assert plan["steps"][1]["declared_provenance"] == ["x", source["id"]]
    result = graph.record_compiled_execution(plan, CompiledWorker().run(plan["source"], 10), "source-r1")
    square = result["steps"]["square"]
    assert graph.obligations[square["id"]]["status"] == "open"
    assert graph.submit({"result": "x**2"}, {"result": result["outputs"]["result"]["id"]})["ready"] is False


def test_computed_provenance_cannot_add_unrelated_local_step_as_support():
    graph = SingleCalculationGraph("Compute x squared.", (), ("result",))
    with pytest.raises(ValueError, match="local provenance y is not an input dependency"):
        compile_graph_program(graph, [
            {"id": "x", "op": "symbol", "args": [], "value": "x", "provenance": ["task"],
                "meaning": "Task x"},
            {"id": "y", "op": "symbol", "args": [], "value": "y", "provenance": ["task"],
                "meaning": "Unrelated task y"},
            {"id": "square", "op": "multiply", "args": ["x", "x"],
                "provenance": ["y"], "meaning": "Proposed square"},
        ], {"result": "square"})


def test_atomic_compiler_supports_generic_matrix_work_without_model_python():
    graph = SingleCalculationGraph("Compute the trace of a two by two diagonal matrix.", (), ("trace",))
    plan = compile_graph_program(graph, [
        {"id": "one", "op": "integer", "args": [], "value": 1, "provenance": [], "meaning": "Unit"},
        {"id": "zero", "op": "integer", "args": [], "value": 0, "provenance": [], "meaning": "Zero"},
        {"id": "minus_one", "op": "negative", "args": ["one"], "provenance": [],
            "meaning": "Negative unit"},
        {"id": "matrix", "op": "matrix", "args": ["one", "zero", "zero", "minus_one"],
            "value": [2, 2], "provenance": [], "meaning": "Explicit two by two matrix"},
        {"id": "trace", "op": "trace", "args": ["matrix"], "provenance": [],
            "meaning": "Trace the matrix"},
    ], {"trace": "trace"})
    result = graph.record_compiled_execution(plan, CompiledWorker().run(plan["source"], 10), "matrix-r1")
    assert result["outputs"]["trace"]["value"] == "0"
    assert graph.submit({"trace": "0"}, {"trace": result["outputs"]["trace"]["id"]})["ready"] is True


def test_controller_compiled_source_executes_in_the_isolated_symbolic_worker():
    from nima_semantica.symbolic_transport import configured_symbolic_worker

    graph = SingleCalculationGraph("Simplify (x+x)/2", (), ("result",))
    plan = compile_graph_program(graph, [
        {"id": "x", "op": "symbol", "args": [], "value": "x", "provenance": ["task"],
            "meaning": "Task variable"},
        {"id": "one", "op": "integer", "args": [], "value": 1, "provenance": [],
            "meaning": "Unit"},
        {"id": "two", "op": "add", "args": ["one", "one"], "provenance": [],
            "meaning": "Construct two arithmetically"},
        {"id": "sum", "op": "add", "args": ["x", "x"], "provenance": [],
            "meaning": "Add x to itself"},
        {"id": "quotient", "op": "divide", "args": ["sum", "two"], "provenance": [],
            "meaning": "Divide the sum by two"},
    ], {"result": "quotient"})
    execution_result = configured_symbolic_worker().run(plan["source"], 20)
    assert execution_result["outcome"] == "executed" and execution_result["exit_code"] == 0
    result = graph.record_compiled_execution(plan, execution_result, "isolated-compiled-r1")
    assert result["outputs"]["result"]["value"] == "x"
    assert graph.submit({"result": "x"}, {"result": result["outputs"]["result"]["id"]})["ready"] is True


def test_atomic_compiler_rejects_hidden_values_and_unbound_references():
    graph = SingleCalculationGraph("Compute x", (), ("$",))
    with pytest.raises(ValueError, match="simple name"):
        compile_graph_program(graph, [{"id": "x", "op": "symbol", "args": [],
            "value": "x);__import__('os')", "provenance": ["task"], "meaning": "Bad input"}], {"$": "x"})
    with pytest.raises(ValueError, match="belong to the controller"):
        compile_graph_program(graph, [
            {"id": "a", "op": "integer", "args": [], "value": 1,
                "provenance": [], "meaning": "First unit"},
            {"id": "b", "op": "integer", "args": [], "value": 1,
                "provenance": [], "meaning": "Second unit"},
            {"id": "x", "op": "add", "args": ["a", "b"], "value": 42,
                "provenance": [], "meaning": "Guessed result"}], {"$": "x"})


@pytest.mark.parametrize("field, malformed", [
    ("op", []), ("args", [[]]), ("provenance", [{}]), ("application", []),
])
def test_atomic_compiler_rejects_malformed_structured_fields(field, malformed):
    graph = SingleCalculationGraph("Compute one", (), ("$",))
    step = {"id": "one", "op": "integer", "args": [], "value": 1,
        "provenance": [], "meaning": "Unit"}
    step[field] = malformed
    with pytest.raises(ValueError):
        compile_graph_program(graph, [step], {"$": "one"})


def test_malformed_compiled_output_records_receipt_but_cannot_create_evidence():
    graph = SingleCalculationGraph("Compute one", (), ("$",))
    plan = compile_graph_program(graph, [{"id": "one", "op": "integer", "args": [],
        "value": 1, "provenance": [], "meaning": "Unit"}], {"$": "one"})
    forged = {"outcome": "executed", "exit_code": 0,
        "stdout": json.dumps({"steps": {"one": "1"}, "outputs": {"$": "2"}}), "stderr": ""}
    nodes = graph.record_compiled_execution(plan, forged, "malformed-r1")
    assert nodes[0]["status"] == "exploratory"
    assert graph.receipts["malformed-r1"]["output"] == forged
    assert not any(node["kind"] == "calculation_output" for node in graph.nodes.values())






def test_failed_execution_is_retained_and_local_repair_keeps_same_graph():
    graph = SingleCalculationGraph("calculate x", (), ("$",))
    failed = graph.record_execution("bad", execution(failed=True), "failed-receipt")
    repaired = graph.record_execution("good", execution(2), "repaired-receipt")
    assert failed[0]["status"] == "failed"
    assert repaired[0]["status"] == "observed"
    assert len(graph.receipts) == 2
    assert graph.submit(2, {"$": repaired[0]["id"]})["scientific_status"] == "evidence_linked_not_verified"
    with pytest.raises(ValueError, match="unknown or failed"):
        graph.submit(2, {"$": failed[0]["id"]})


def test_foreign_dependency_and_unknown_supersession_fail_closed():
    graph = SingleCalculationGraph("calculate x", (), ("$",))
    with pytest.raises(ValueError, match="cross-graph"):
        graph.add_step(kind="claim", statement="x=2", value=2, depends_on=["foreign:n1"])
    with pytest.raises(ValueError, match="superseded"):
        graph.add_step(kind="assumption", statement="a", value=1, depends_on=[], supersedes="n999")
    assert len(graph.nodes) == 1


def test_invalid_dependencies_and_oversized_outputs_do_not_mutate_graph():
    graph = SingleCalculationGraph("generic calculation", (), ("$",))
    with pytest.raises(ValueError, match="dependencies must be"):
        graph.add_step(kind="assumption", statement="x", value=1, depends_on="task")
    with pytest.raises(ValueError, match="too many"):
        graph.record_execution("print(...)" , execution(list(range(129))), "large-receipt")
    assert len(graph.nodes) == 1 and not graph.receipts


def test_generic_algebra_and_non_kondo_physics_are_not_special_cased():
    algebra = SingleCalculationGraph("factor a quadratic", (), ("factorization",))
    factor = algebra.record_execution("sympy.factor(x*x-1)",
        execution({"factorization": "(x - 1)*(x + 1)"}), "algebra-r1")[0]
    assert algebra.submit({"factorization": "(x - 1)*(x + 1)"},
        {"factorization": factor["id"]})["mathematically_verified"] is False
    oscillator = SingleCalculationGraph("calculate oscillator energy", (), ("energy",))
    premise = oscillator.add_step(kind="assumption", statement="The frequency equals two reciprocal seconds.",
        value=2, depends_on=[])
    energy = oscillator.record_execution("print(E)", execution({"energy": "hbar"}),
        "oscillator-r1", [premise["id"]])[0]
    candidate = oscillator.submit({"energy": "hbar"}, {"energy": energy["id"]})
    assert candidate["unresolved_root_ids"] == [premise["id"]]


def test_conflicting_observations_remain_distinct_without_false_adjudication():
    graph = SingleCalculationGraph("estimate a parameter", (), ("estimate",))
    first = graph.record_execution("print(2)", execution({"estimate": 2}), "r1")[0]
    second = graph.record_execution("print(3)", execution({"estimate": 3}), "r2")[0]
    assert first["id"] != second["id"]
    assert first["value"] == 2 and second["value"] == 3
    assert graph.submit({"estimate": 2}, {"estimate": first["id"]})["mathematically_verified"] is False






class Worker:
    def __init__(self):
        self.calls = []

    def run(self, source, timeout):
        self.calls.append(source)
        return execution(failed=source == "bad") if source == "bad" else execution({"answer": 2})


class CompiledWorker(Worker):
    def run(self, source, timeout):
        self.calls.append(source)
        if source == "bad":
            return execution(failed=True)
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(source, "<controller-compiled-test>", "exec"), {})
        return {"outcome": "executed", "exit_code": 0,
            "stdout": output.getvalue(), "stderr": ""}


class RaisingWorker(Worker):
    def run(self, source, timeout):
        self.calls.append(source)
        if source == "bad":
            raise RuntimeError("isolated transport unavailable")
        return execution({"answer": 2})


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        action = next(self.actions)
        return Invocation(result=action, manifest=MANIFEST, input_tokens=10,
            output_tokens=10, finish_reason="tool_calls").model_dump(mode="json")
