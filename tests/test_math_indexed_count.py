"""Standalone regressions for the maintained graph implementation."""
import hashlib
import json
from pathlib import Path

import pytest

from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_indexed_count import IndexedSet, validate_indexed_sets
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_task_contract import MathTaskFact
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _contract(paths=("beta",), members=("left", "right")):
    fact = MathTaskFact(fact_id="branches", kind="condition", quote="both left and right branches",
        required_paths=list(paths))
    spec = IndexedSet(set_id="branches", fact_id="branches", members=members,
        required_paths=paths)
    return SingleCalculationGraph("sum both left and right branches", (fact,), paths, (spec,)), spec


def _step(identifier, op, args=(), *, value=None, provenance=(), **extra):
    item = {"id": identifier, "op": op, "args": list(args), "provenance": list(provenance),
        "meaning": identifier, **extra}
    if value is not None:
        item["value"] = value
    return item


def test_global_count_cannot_be_applied_inside_each_indexed_member():
    graph, spec = _contract()
    steps = [_step("count", "integer", value=2, provenance=("fact:branches",), index_count_of="branches"),
        _step("x", "symbol", value="x"),
        _step("left", "multiply", ("count", "x"),
            index_scope={"set_id": "branches", "member": "left"}),
        _step("right", "multiply", ("count", "x"),
            index_scope={"set_id": "branches", "member": "right"}),
        _step("total", "add", ("left", "right"), index_sum="branches")]
    with pytest.raises(ValueError, match="global cardinality.*inside member left"):
        compile_graph_program(graph, steps, {"beta": "total"}, (spec,))
    steps[0].pop("index_count_of")
    with pytest.raises(ValueError, match="possible cardinality.*needs an explicit count role"):
        compile_graph_program(graph, steps, {"beta": "total"}, (spec,))


def test_correct_member_sum_compiles_and_retains_typed_lineage():
    from nima_semantica.symbolic_transport import configured_symbolic_worker

    graph, spec = _contract()
    steps = [_step("x", "symbol", value="x"), _step("one", "integer", value=1),
        _step("left", "multiply", ("one", "x"),
            index_scope={"set_id": "branches", "member": "left"}),
        _step("right", "multiply", ("one", "x"),
            index_scope={"set_id": "branches", "member": "right"}),
        _step("total", "add", ("left", "right"), index_sum="branches")]
    plan = compile_graph_program(graph, steps, {"beta": "total"}, (spec,))
    assert plan["steps"][-1]["index_sum"] == "branches"
    assert plan["steps"][2]["index_scope"] == {"set_id": "branches", "member": "left"}
    assert "index:branches" in graph.nodes
    assert graph.frontier()["indexed_sets"][0]["members"] == ["left", "right"]
    graph.stage_compiled_plan(plan, "indexed-r1")
    executed = configured_symbolic_worker().run(plan["source"], 20)
    recorded = graph.record_compiled_execution(plan, executed, "indexed-r1")
    assert recorded["outputs"]["beta"]["value"] == "2*x"
    assert recorded["steps"]["total"]["origin"]["index_sum"] == "branches"


def test_missing_duplicate_and_forged_index_annotations_fail_closed():
    graph, spec = _contract()
    base = [_step("x", "symbol", value="x"),
        _step("left", "simplify", ("x",), index_scope={"set_id": "branches", "member": "left"}),
        _step("right", "simplify", ("x",), index_scope={"set_id": "branches", "member": "right"}),
        _step("total", "add", ("left", "right"), index_sum="branches")]
    with pytest.raises(ValueError, match="required indexed sum.*missing"):
        compile_graph_program(graph, base[:-1], {"beta": "left"}, (spec,))
    duplicate = [dict(step) for step in base]
    duplicate[2] = {**duplicate[2], "index_scope": {"set_id": "branches", "member": "left"}}
    with pytest.raises(ValueError, match="each declared member exactly once"):
        compile_graph_program(graph, duplicate, {"beta": "total"}, (spec,))
    forged = [dict(step) for step in base]
    forged[1] = {**forged[1], "index_scope": {"set_id": "other", "member": "left"}}
    with pytest.raises(ValueError, match="invalid indexed member scope"):
        compile_graph_program(graph, forged, {"beta": "total"}, (spec,))


def test_count_annotation_requires_exact_fact_and_cardinality_not_an_incidental_two():
    graph, spec = _contract()
    bad = [_step("count", "integer", value=3, provenance=("fact:branches",),
        index_count_of="branches")]
    with pytest.raises(ValueError, match="invalid indexed cardinality declaration"):
        compile_graph_program(graph, bad, {"beta": "count"}, (spec,))
    bad[0]["value"] = 2
    bad[0]["provenance"] = []
    with pytest.raises(ValueError, match="invalid indexed cardinality declaration"):
        compile_graph_program(graph, bad, {"beta": "count"}, (spec,))
    # An unrelated two is not classified as the global branch count.
    independent = [_step("two", "integer", value=2), _step("x", "symbol", value="x"),
        _step("left", "multiply", ("two", "x"),
            index_scope={"set_id": "branches", "member": "left"}),
        _step("right", "multiply", ("two", "x"),
            index_scope={"set_id": "branches", "member": "right"}),
        _step("total", "add", ("left", "right"), index_sum="branches")]
    assert compile_graph_program(graph, independent, {"beta": "total"}, (spec,))["outputs"] == {"beta": "total"}


def test_harness_set_must_bind_to_request_fact_and_required_output():
    spec = IndexedSet(set_id="branches", fact_id="branches", members=("left", "right"),
        required_paths=("beta",))
    request = SimpleNamespace(task_facts=(), required_output_paths=("beta",))
    with pytest.raises(ValueError, match="exact harness task fact"):
        validate_indexed_sets((spec,), request)
    request = SimpleNamespace(task_facts=(MathTaskFact(
        fact_id="branches", kind="condition", quote="both left and right branches",
        required_paths=["other"]),), required_output_paths=("other",))
    with pytest.raises(ValueError, match="unknown output paths"):
        validate_indexed_sets((spec,), request)
