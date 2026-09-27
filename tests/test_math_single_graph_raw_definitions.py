"""Standalone regressions for the maintained graph implementation."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_indexed_count import IndexedSet
from nima_semantica.math_single_graph_state import SingleCalculationGraph


ROOT = Path(__file__).resolve().parents[1]




def test_exact_named_numeric_definition_is_grounded_but_physical_application_is_not():
    graph = SingleCalculationGraph("Let c = 2 and x be a task variable. Calculate c*x.", (), ("answer",))
    steps = [
        {"id": "c", "op": "integer", "args": [], "value": 2, "provenance": ["task"],
            "raw_definition": {"evidence_id": "task", "quote": "c = 2"}, "meaning": "Task-defined coefficient"},
        {"id": "x", "op": "symbol", "args": [], "value": "x", "provenance": ["task"],
            "meaning": "Task variable"},
        {"id": "product", "op": "multiply", "args": ["c", "x"], "provenance": [],
            "application": "physical_rule", "meaning": "Proposed physical application of c"},
    ]
    plan = compile_graph_program(graph, steps, {"answer": "product"})
    run = subprocess.run([sys.executable, "-c", plan["source"]], text=True,
        capture_output=True, check=True, timeout=20)
    result = graph.record_compiled_execution(plan, {"outcome": "executed", "exit_code": 0,
        "stdout": run.stdout, "stderr": run.stderr}, "definition-r1")
    assert result["steps"]["c"]["status"] == "observed"
    assert result["steps"]["c"]["raw_definition"] == {"evidence_id": "task", "quote": "c = 2"}
    assert result["steps"]["product"]["id"] in graph.obligations
    answer = result["outputs"]["answer"]
    assert graph.submit({"answer": answer["value"]}, {"answer": answer["id"]})["ready"] is False


@pytest.mark.parametrize("declaration", [
    {"evidence_id": "task", "quote": "x is a task variable"},
    {"evidence_id": "task", "quote": "x = 2"},
    {"evidence_id": "missing", "quote": "c = 2"},
])
def test_related_or_forged_text_cannot_define_a_numeric_input(declaration):
    graph = SingleCalculationGraph("Let c = 2 and x is a task variable.", (), ("answer",))
    steps = [{"id": "c", "op": "integer", "args": [], "value": 2,
        "provenance": ["task"], "raw_definition": declaration, "meaning": "Claimed c"}]
    with pytest.raises(ValueError, match="raw_definition|explicitly define"):
        compile_graph_program(graph, steps, {"answer": "c"})


def test_pure_arithmetic_literal_without_claimed_source_is_not_rejected():
    graph = SingleCalculationGraph("Compute 2 + 2.", (), ("answer",))
    steps = [{"id": "two", "op": "integer", "args": [], "value": 2,
        "provenance": [], "meaning": "Arithmetic literal"},
        {"id": "sum", "op": "add", "args": ["two", "two"],
         "provenance": [], "meaning": "Arithmetic addition"}]
    assert compile_graph_program(graph, steps, {"answer": "sum"})["outputs"] == {"answer": "sum"}


def test_numeric_prefix_of_a_longer_source_expression_is_not_a_definition():
    graph = SingleCalculationGraph("The task states c = 2 + 1.", (), ("answer",))
    steps = [{"id": "c", "op": "integer", "args": [], "value": 2,
        "provenance": ["task"],
        "raw_definition": {"evidence_id": "task", "quote": "c = 2 + 1"},
        "meaning": "Incorrectly truncated value"}]
    with pytest.raises(ValueError, match="does not explicitly define"):
        compile_graph_program(graph, steps, {"answer": "c"})
