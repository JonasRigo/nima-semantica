"""Controlled calculus syntax; expressions are built only from graph operands.

Conditions are execution observations, never a certificate of applicability.
"""
ARITIES = {"integrate": (2, 4), "limit": (3, 3), "series": (4, 4),
    "truncate_series": (1, 1), "gamma": (1, 1), "log": (1, 1),
    "exp": (1, 1), "infinity": (0, 0), "piecewise_branch": (2, 2)}
DOMAINS = {"real", "positive", "nonnegative", "negative", "nonzero", "integer"}

def validate_options(step):
    op = step["op"]
    options = step.get("options")
    assumptions = step.get("symbol_assumptions")
    if assumptions is not None:
        if (op != "symbol" or not isinstance(assumptions, dict) or not assumptions
                or set(assumptions) - DOMAINS
                or any(type(v) is not bool or not v for v in assumptions.values())):
            raise ValueError("symbol_assumptions requires supported true domain flags on a symbol")
        if assumptions.get("negative") and (assumptions.get("positive") or assumptions.get("nonnegative")):
            raise ValueError("contradictory symbol domain assumptions")
    if options is not None:
        if (op not in {"limit", "series"} or not isinstance(options, dict)
                or set(options) != {"direction"} or options["direction"] not in {"+", "-", "+-"}
                or (op == "series" and options["direction"] == "+-")):
            raise ValueError("options requires an explicit supported limit/series direction")
    if op == "integrate" and len(step.get("args", [])) not in (2, 4):
        raise ValueError("integrate requires expression, variable, and optionally both bounds")
    if op in {"limit", "series"} and options is None:
        raise ValueError("limit and series require an explicit direction")

def expression(step, operands):
    op = step["op"]
    a = operands
    if op == "infinity": return "sp.oo"
    if op == "integrate":
        return f"_nima_integrate({', '.join(a)})"
    if op == "limit":
        return f"sp.limit({', '.join(a)}, dir={step['options']['direction']!r})"
    if op == "series":
        return f"_nima_series({', '.join(a)}, direction={step['options']['direction']!r})"
    if op == "piecewise_branch": return f"_nima_piecewise_branch({a[0]}, {a[1]})"
    if op == "truncate_series": return f"{a[0]}.removeO()"
    return f"sp.{op}({a[0]})"

WORKER_HELPERS = [
    "def _nima_integrate(expr, variable, *bounds):",
    "    if not isinstance(variable, sp.Symbol): raise ValueError('integration variable must be a symbol')",
    "    return sp.integrate(expr, (variable, *bounds), conds='piecewise') if bounds else sp.integrate(expr, variable)",
    "def _nima_series(expr, variable, point, order, direction):",
    "    if not isinstance(variable, sp.Symbol): raise ValueError('series variable must be a symbol')",
    "    if not isinstance(order, sp.Integer) or not 1 <= order <= 20: raise ValueError('series order must be an integer from 1 to 20')",
    "    return sp.series(expr, variable, point, int(order), dir=direction)",
    "def _nima_piecewise_branch(value, index):",
    "    if not isinstance(value, sp.Piecewise) or not isinstance(index, sp.Integer) or not 0 <= index < len(value.args): raise ValueError('piecewise_branch requires a Piecewise expression and a valid integer branch index')",
    "    return value.args[int(index)][0]",
    "def _nima_calculus_info(value):",
    "    return {'conditional': bool(value.has(sp.Piecewise)), 'unevaluated': bool(value.has(sp.Integral, sp.Limit, sp.Derivative)), 'nonfinite': bool(value.has(sp.oo, -sp.oo, sp.zoo, sp.nan)), 'remainder': str(value.getO()) if hasattr(value, 'getO') and value.getO() is not None else None}",
]
