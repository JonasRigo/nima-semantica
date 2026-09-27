"""Fixed-checker and contract tests; no generated code runs on the host."""

import runpy
from pathlib import Path

import pytest

from nima_semantica.calculation import CalculationTask, SymbolicCheckInput


def check(**updates):
    candidate = updates.pop("candidate")
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "src/nima_semantica/symbolic_checker.py")
    )
    return module["check"](
        SymbolicCheckInput(task=CalculationTask(**updates), candidate=candidate).model_dump(
            mode="json"
        )
    )


@pytest.mark.parametrize(
    "operation,expression,candidate",
    [
        ("integrate", "x**2", "x**3/3"),
        ("differentiate", "sin(x)", "cos(x)"),
        ("series", "exp(x)", "1+x+x**2/2+x**3/6+x**4/24"),
        ("simplify", "(x+1)**2", "x**2+2*x+1"),
        ("solve", "x**2-4", "2"),
    ],
)
def test_independent_symbolic_checks(operation, expression, candidate):
    value = check(operation=operation, expression=expression, candidate=candidate)
    assert value["outcome"] == "check_passed"
    assert "source correspondence" in value["scope"]


def test_wrong_answer_is_not_checked():
    assert check(candidate="x**2/2")["outcome"] == "inconclusive"


@pytest.mark.parametrize(
    "candidate",
    ["__import__('os').system('true')", "x.__class__", "[x for x in ()]", "sin(x, evaluate=False)"],
)
def test_checker_never_evaluates_arbitrary_python(candidate):
    with pytest.raises(ValueError):
        check(candidate=candidate)


def test_execution_limits_and_unknown_fields_are_strict():
    for kwargs in ({"max_repairs": 2}, {"check_plan_json": "{}"}, {"replan_retrieval": False},
                   {"timeout_seconds": 121}, {"operation_name": "dispatch"}):
        with pytest.raises(ValueError):
            CalculationTask(**kwargs)
