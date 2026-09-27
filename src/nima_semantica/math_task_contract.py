"""Harness-authored task constraints and conservative mechanical checks.

Only explicitly supplied machine forms are executable. An exact quotation
authenticates wording, not an inferred equation, physical convention or unit.
"""
from __future__ import annotations

import ast
import re
from typing import Literal

from pydantic import Field
import sympy as sp

from .models import StrictModel
from .calculation_values import _scalar_expression


class MathTaskFact(StrictModel):
    fact_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    kind: Literal["definition", "domain", "range", "condition", "unit", "normalization", "output"]
    quote: str = Field(min_length=1, max_length=2000)
    symbol: str | None = Field(default=None, max_length=200)
    machine_symbol: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
        description="Code-side spelling of the quoted symbol, supplied by the harness.")
    expression: str | None = Field(default=None, max_length=1000)
    machine_expression: str | None = Field(default=None, max_length=160,
        description="Harness-normalized scalar definition; never parsed from prose by the worker.")
    dimension: dict[str, int] | None = Field(default=None,
        description="Harness-specified dimensional exponents for a symbol or required output.")
    machine_condition: str | None = Field(default=None, max_length=160,
        description="Optional scalar comparison, e.g. x > 0; checked only on bound scalar claims.")
    output_substitutions: dict[str, str] | None = Field(default=None,
        description="Harness-declared source-symbol substitutions required in each named output path.")
    required_paths: tuple[str, ...] = Field(min_length=1, max_length=32)


def validate_task_facts(task: str, paths: tuple[str, ...], facts: tuple[MathTaskFact, ...]) -> None:
    """Check exact prompt binding; semantic interpretation remains harness-owned."""
    if len({fact.fact_id for fact in facts}) != len(facts):
        raise ValueError("task fact IDs must be unique")
    for fact in facts:
        if fact.quote not in task:
            raise ValueError("task fact quotation not found in exact task: " + fact.fact_id)
        if fact.symbol is not None and fact.symbol not in fact.quote:
            raise ValueError("task fact symbol not found in quotation: " + fact.fact_id)
        if fact.expression is not None and fact.expression not in fact.quote:
            raise ValueError("task fact expression not found in quotation: " + fact.fact_id)
        if not set(fact.required_paths).issubset(paths):
            raise ValueError("task fact references unknown output path: " + fact.fact_id)
        if fact.machine_expression is not None:
            if fact.kind not in ("definition", "normalization") or not (fact.machine_symbol or fact.symbol):
                raise ValueError("machine definition requires an identifier symbol: " + fact.fact_id)
            if not (fact.machine_symbol or fact.symbol).isidentifier():
                raise ValueError("invalid machine definition symbol: " + fact.fact_id)
            _scalar_expression(fact.machine_expression)
        if fact.dimension is not None:
            if fact.kind != "unit" or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
                    or type(power) is not int or abs(power) > 12
                    for key, power in fact.dimension.items()):
                raise ValueError("unit dimension must be a bounded exponent vector: " + fact.fact_id)
        if fact.machine_condition is not None:
            if fact.kind not in ("condition", "domain", "range") or not (fact.machine_symbol or fact.symbol):
                raise ValueError("machine condition requires an identifier symbol: " + fact.fact_id)
            _parse_condition(fact.machine_condition, fact.machine_symbol or fact.symbol)
        if fact.output_substitutions is not None:
            if fact.kind != "condition" or not fact.output_substitutions or len(fact.output_substitutions) > 8:
                raise ValueError("output substitutions require a bounded task condition: " + fact.fact_id)
            for source, target in fact.output_substitutions.items():
                if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", source) or
                        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target) or
                        source == target or
                        re.search(r"\b" + re.escape(source) + r"\b", fact.quote) is None or
                        re.search(r"\b" + re.escape(target) + r"\b", fact.quote) is None):
                    raise ValueError("output substitution is not bound to exact task wording: " + fact.fact_id)


def task_fact_payload(facts: tuple[MathTaskFact, ...]) -> list[dict]:
    return [{key: value for key, value in fact.model_dump(mode="json").items()
        if key in ("fact_id", "kind", "quote", "symbol", "required_paths", "output_substitutions") and value is not None}
        for fact in facts]


def _parse_condition(text: str, symbol: str):
    tree = ast.parse(text, mode="eval").body
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1 or len(tree.comparators) != 1:
        raise ValueError("machine condition must be one scalar comparison")
    if not isinstance(tree.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        raise ValueError("unsupported machine condition relation")
    left, right = _scalar_expression(ast.unparse(tree.left)), _scalar_expression(ast.unparse(tree.comparators[0]))
    if sp.Symbol(symbol) not in left.free_symbols | right.free_symbols:
        raise ValueError("condition does not mention its bound symbol")
    return left, tree.ops[0], right


def _source_definitions(source: str) -> dict[str, list[sp.Expr]]:
    """Read simple assignments only; exploratory/symbolic code stays unknown."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    definitions: dict[str, list[sp.Expr]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                value = _scalar_expression(ast.unparse(node.value))
            except (ValueError, SyntaxError, TypeError):
                continue
            definitions.setdefault(node.targets[0].id, []).append(value)
    return definitions


def _dimension_of(expression: sp.Expr, dimensions: dict[str, dict[str, int]]):
    if expression.is_Number:
        return {}
    if expression.is_Symbol:
        return dimensions.get(str(expression))
    if expression.is_Add:
        parts = [_dimension_of(item, dimensions) for item in expression.args]
        if any(part is None for part in parts):
            return None
        if any(part != parts[0] for part in parts[1:]):
            raise ValueError("dimensionally_inhomogeneous_sum")
        return parts[0]
    if expression.is_Mul:
        result: dict[str, int] = {}
        for item in expression.args:
            part = _dimension_of(item, dimensions)
            if part is None:
                return None
            for key, power in part.items():
                result[key] = result.get(key, 0) + power
        return {key: power for key, power in result.items() if power}
    if expression.is_Pow and expression.exp.is_Rational:
        base = _dimension_of(expression.base, dimensions)
        if base is None:
            return None
        powers = {key: power * expression.exp for key, power in base.items()}
        return {key: int(power) for key, power in powers.items() if power} if all(
            power.is_Integer for power in powers.values()) else None
    return None


def check_task_facts(facts: tuple[MathTaskFact, ...], *, source: str,
        claims: dict) -> list[dict]:
    """Check only observable contradictions; preserve unknown lineage as unknown."""
    assignments = _source_definitions(source)
    dimensions = {fact.machine_symbol or fact.symbol: fact.dimension for fact in facts
        if fact.kind == "unit" and (fact.machine_symbol or fact.symbol) and fact.dimension is not None}
    dimension_conflicts = set()
    for _ in range(len(facts)):
        changed = False
        for fact in facts:
            if fact.machine_expression is None:
                continue
            symbol = fact.machine_symbol or fact.symbol
            try:
                inferred = _dimension_of(_scalar_expression(fact.machine_expression), dimensions)
            except ValueError:
                continue
            if inferred is not None:
                if symbol in dimensions and dimensions[symbol] != inferred:
                    dimension_conflicts.add(symbol)
                elif symbol not in dimensions:
                    dimensions[symbol] = inferred
                    changed = True
        if not changed:
            break
    report = []
    scalar_claims = []
    for value in claims.values():
        if isinstance(value, (str, int, float)):
            try:
                scalar_claims.append(_scalar_expression(str(value)))
            except (ValueError, SyntaxError, TypeError):
                pass
    for fact in facts:
        status, diagnostic = "unresolved", "no mechanically bound scalar observation"
        if fact.machine_expression is not None:
            expected = _scalar_expression(fact.machine_expression)
            symbol = fact.machine_symbol or fact.symbol
            bound_in_claim = symbol in claims or any(sp.Symbol(symbol) in expr.free_symbols
                for expr in scalar_claims)
            assigned = assignments.get(symbol, []) if bound_in_claim else []
            if assigned:
                wrong = [str(value) for value in assigned if sp.simplify(value - expected) != 0]
                status, diagnostic = (("contradicted", "source redefines task symbol: " + ", ".join(wrong))
                    if wrong else ("checked", "source assignments agree with task definition"))
            else:
                status, diagnostic = ("inherited", "definition retained; no conflicting bound assignment found")
        elif fact.dimension is not None:
            symbol = fact.machine_symbol or fact.symbol
            if symbol in dimension_conflicts:
                status, diagnostic = "contradicted", "declared unit conflicts with derived definition"
            targets = []
            for path in fact.required_paths:
                value = ((next(iter(claims.values())) if len(claims) == 1 else None)
                    if path == "$" else claims.get(path))
                if isinstance(value, (str, int, float)):
                    try:
                        targets.append(_scalar_expression(str(value)))
                    except (ValueError, SyntaxError, TypeError):
                        pass
            if targets and status != "contradicted":
                try:
                    observed = [_dimension_of(expr, dimensions) for expr in targets]
                    if fact.symbol is None and any(item is not None and item != fact.dimension for item in observed):
                        status, diagnostic = "contradicted", "output dimension differs from task fact"
                    elif all(item is not None for item in observed):
                        status, diagnostic = "checked", "output dimensions checked"
                except ValueError as exc:
                    status, diagnostic = "contradicted", str(exc)
        elif fact.machine_condition is not None:
            symbol = fact.machine_symbol or fact.symbol
            left, operation, right = _parse_condition(fact.machine_condition, symbol)
            values = [claims.get(symbol)] if symbol in claims else []
            for value in values:
                try:
                    scalar = _scalar_expression(str(value))
                    if scalar.free_symbols:
                        continue
                    lhs = left.subs(symbol, scalar)
                    rhs = right.subs(symbol, scalar)
                    relations = {ast.Eq: sp.Eq, ast.NotEq: sp.Ne, ast.Lt: sp.Lt,
                        ast.LtE: sp.Le, ast.Gt: sp.Gt, ast.GtE: sp.Ge}
                    verdict = relations[type(operation)](lhs, rhs)
                    if verdict in (sp.true, sp.false):
                        status = "checked" if verdict == sp.true else "contradicted"
                        diagnostic = "bound scalar claim satisfies condition" if verdict == sp.true else "bound scalar claim violates condition"
                except (ValueError, SyntaxError, TypeError):
                    pass
        report.append({"fact_id": fact.fact_id, "kind": fact.kind,
            "required_paths": list(fact.required_paths), "status": status,
            "diagnostic": diagnostic})
    return report
