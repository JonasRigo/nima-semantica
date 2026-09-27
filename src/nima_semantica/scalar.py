"""Bounded exact scalar checks. No string evaluation or model-generated code."""
from itertools import product
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from .models import StrictModel


class ScalarExpression(StrictModel):
    op: Literal["constant", "variable", "add", "sub", "mul", "div", "sqrt", "floor", "choose"]
    value: StrictInt | None = None
    name: str | None = None
    left: "ScalarExpression | None" = None
    right: "ScalarExpression | None" = None


class IntegerDomain(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,15}$")
    lower: StrictInt = Field(ge=-1000, le=1000)
    upper: StrictInt = Field(ge=-1000, le=1000)


class ScalarCheck(StrictModel):
    statement: str
    variables: tuple[IntegerDomain, ...] = Field(max_length=4)
    left: ScalarExpression
    right: ScalarExpression
    relation: Literal["eq", "le", "lt"]
    assumptions: tuple[str, ...] = Field(default=(), description="Any additional assumptions not encoded in independent variable ranges. If nonempty, a witness is only a candidate pending assumption checking.")

    @model_validator(mode="after")
    def bounded(self):
        names = {v.name for v in self.variables}
        if len(names) != len(self.variables) or any(v.lower > v.upper for v in self.variables):
            raise ValueError("invalid variable domain")
        count = 0
        def walk(e, depth=0):
            nonlocal count
            count += 1
            if depth > 8 or count > 64:
                raise ValueError("scalar expression exceeds bounds")
            if e.op == "constant":
                if e.value is None or abs(e.value) > 1000 or e.name is not None or e.left or e.right:
                    raise ValueError("invalid scalar constant")
            elif e.op == "variable":
                if e.name not in names or e.value is not None or e.left or e.right:
                    raise ValueError("invalid scalar variable")
            else:
                if e.name is not None or e.value is not None or e.left is None:
                    raise ValueError("invalid scalar operation")
                if (e.op in ("sqrt", "floor")) != (e.right is None):
                    raise ValueError("invalid scalar arity")
                walk(e.left, depth + 1)
                if e.right:
                    walk(e.right, depth + 1)
        walk(self.left)
        walk(self.right)
        return self


def value(e, variables):
    import sympy as s
    if e.op == "constant":
        return s.Integer(e.value)
    if e.op == "variable":
        return s.Integer(variables[e.name])
    a = value(e.left, variables)
    if e.op == "sqrt":
        if a.is_nonnegative is not True:
            raise ValueError("negative or unknown radicand")
        result = s.sqrt(a)
    elif e.op == "floor":
        result = s.floor(a)
    else:
        b = value(e.right, variables)
        if e.op == "div" and b.is_zero is not False:
            raise ValueError("zero or unknown denominator")
        if e.op == "choose":
            if a.is_Integer is not True or b.is_Integer is not True or not (0 <= a <= 1000 and 0 <= b <= 1000):
                raise ValueError("unsupported binomial domain")
            result = s.binomial(a, b)
        else:
            result = {"add": lambda: a+b, "sub": lambda: a-b, "mul": lambda: a*b, "div": lambda: a/b}[e.op]()
    if len(str(result)) > 4096:
        raise ValueError("scalar result exceeds bounds")
    return result


def check_boundaries(check):
    import sympy as s
    samples = []
    for variable in check.variables:
        samples.append(sorted({variable.lower, variable.upper, *range(variable.lower, min(variable.upper, variable.lower+2)+1)}))
    tested, skipped, failures, domain_errors = 0, 0, [], []
    for point in product(*samples):
        witness = dict(zip((v.name for v in check.variables), point))
        try:
            a, b = value(check.left, witness), value(check.right, witness)
            comparison = {"eq": s.Eq, "le": s.Le, "lt": s.Lt}[check.relation](a, b)
            if comparison not in (s.true, s.false):
                skipped += 1
                continue
            tested += 1
            if comparison == s.false:
                failures.append({"witness": witness, "left": str(a), "right": str(b)})
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            skipped += 1
            domain_errors.append({"witness": witness, "error": str(exc)})
    return {"statement": check.statement, "encoding": check.model_dump(mode="json"), "tested": tested, "skipped": skipped,
        "failures": failures, "domain_errors": domain_errors,
        "outcome": "counterexample_to_encoded_statement" if failures and not check.assumptions else "candidate_witness" if failures else "domain_error" if domain_errors else "inconclusive" if skipped or not tested else "no_counterexample_in_samples",
        "correspondence_verified": False, "scope": "Model-proposed scalar translation only; finite samples never establish a universal theorem."}
