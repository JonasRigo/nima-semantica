"""Standalone regressions for the maintained graph implementation."""
import copy
import json
from pathlib import Path
import runpy

import pytest
from nima_semantica.math_interface_helpers import expand_aggregations, order_dependencies
from nima_semantica.math_indexed_count import IndexedSet
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService


def test_order_preserves_operand_order_and_provenance():
    original = {"steps": [
        {"id": "product", "op": "multiply", "args": ["b", "a"], "provenance": ["p"]},
        {"id": "a", "op": "symbol"}, {"id": "b", "op": "symbol"},
        {"id": "p", "op": "symbol", "provenance": ["task"]}]}
    saved = copy.deepcopy(original)
    result, repairs = order_dependencies(original, ["task"])
    assert original == saved
    assert result["steps"][-1] == original["steps"][0]
    assert repairs[0]["compiled_order"] == ["a", "b", "p", "product"]
    assert order_dependencies(result, ["task"])[1] == []


@pytest.mark.parametrize("steps,match", [
    ([{"id": "a", "args": ["b"]}], "unknown"),
    ([{"id": "a", "args": ["b"]}, {"id": "b", "args": ["a"]}], "cyclic"),
    ([{"id": "a"}, {"id": "a"}], "duplicate"),
    ([{"id": "a", "provenance": ["a"]}], "cyclic"),
])
def test_bad_dependencies_fail(steps, match):
    with pytest.raises(ValueError, match=match):
        order_dependencies({"steps": steps})


def test_graph_local_ambiguity_rejected():
    with pytest.raises(ValueError, match="ambiguous"):
        order_dependencies({"steps": [{"id": "n1"}, {"id": "b", "provenance": ["n1"]}]}, ["n1"])


def test_aggregation_forward_reference_and_cycle():
    spec = IndexedSet(set_id="branches", fact_id="branches", members=("left", "right"), required_paths=("answer",))
    original = dict(steps=[{"id": "scaled", "op": "multiply", "args": ["total", "x"]},
        {"id": "x", "op": "symbol"}], aggregations=[dict(id="total", set_id="branches",
        contributions={"left": "x", "right": "x"})])
    expanded, _ = expand_aggregations(original, (spec,))
    ordered, _ = order_dependencies(expanded)
    assert ordered["steps"][-1]["id"] == "scaled"
    original["aggregations"][0]["contributions"]["left"] = "scaled"
    expanded, _ = expand_aggregations(original, (spec,))
    with pytest.raises(ValueError, match="cyclic"):
        order_dependencies(expanded)
