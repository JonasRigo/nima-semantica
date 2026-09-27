"""Fixed checker program, executed only inside the isolated symbolic worker.

REQUEST is injected as a Python literal by the parent. Expressions use a small
AST grammar, never eval/sympify of arbitrary strings. Successful checks apply to
the encoded expression and domain, not all prose assumptions or source claims.
"""

import ast
import json
import sympy as s


def check(request):
    task = request["task"]
    if task["operation"] == "calculate":
        return {
            "outcome": "inconclusive",
            "reason": "no independent checker for general calculation; use explicitly scoped executable checks and advisory replanning",
        }
    domain = task["symbol_domain"]
    x = s.Symbol(
        task["variable"],
        **(
            {"real": True}
            if domain == "real"
            else {"positive": True} if domain == "positive" else {"complex": True}
        )
    )
    functions = {
        n: getattr(s, n)
        for n in (
            "sin",
            "cos",
            "tan",
            "exp",
            "log",
            "sqrt",
            "Abs",
            "sinh",
            "cosh",
            "atan",
            "asin",
            "acos",
            "factorial",
        )
    }
    names = {task["variable"]: x, "pi": s.pi, "E": s.E, "I": s.I}

    def expression(text):
        tree = ast.parse(text, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise ValueError("expression too complex")

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if (
                isinstance(node, ast.Constant)
                and type(node.value) is int
                and abs(node.value) <= 10**12
            ):
                return s.Integer(node.value)
            if isinstance(node, ast.Name) and node.id in names:
                return names[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add):
                    return a + b
                if isinstance(node.op, ast.Sub):
                    return a - b
                if isinstance(node.op, ast.Mult):
                    return a * b
                if isinstance(node.op, ast.Div):
                    return a / b
                if isinstance(node.op, ast.Pow) and b.is_number and abs(b) <= 100:
                    return a**b
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in functions
                and len(node.args) == 1
                and not node.keywords
            ):
                return functions[node.func.id](visit(node.args[0]))
            raise ValueError("unsupported expression syntax")

        return visit(tree)

    original = expression(task["expression"])
    candidate = expression(request["candidate"])
    operation = task["operation"]
    if operation == "integrate":
        residual = s.simplify(s.diff(candidate, x) - original)
        method = "differentiate proposed antiderivative"
    elif operation == "differentiate":
        residual = s.simplify(candidate - s.diff(original, x))
        method = "compare symbolic derivative"
    elif operation == "simplify":
        residual = s.simplify(candidate - original)
        method = "simplify difference; singularities and domain correspondence remain obligations"
    elif operation == "solve":
        residual = s.simplify(original.subs(x, candidate))
        method = "substitute one proposed root; completeness and domain membership not established"
    elif operation == "series":
        residual = s.simplify(
            candidate
            - s.series(original, x, task["expansion_point"], task["expansion_order"]).removeO()
        )
        method = "compare truncated formal series; convergence and remainder bounds not established"
    else:
        return {
            "outcome": "inconclusive",
            "reason": "no independent checker for general calculation",
        }
    return {
        "outcome": "check_passed" if residual == 0 else "check_failed" if residual.is_zero is False else "inconclusive",
        "residual": str(residual),
        "method": method,
        "scope": "encoded expression only; prose assumptions, exceptional points, and source correspondence require review",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(check(REQUEST)))
    except Exception as error:
        print(
            json.dumps(
                {
                    "outcome": "inconclusive",
                    "reason": type(error).__name__ + ": " + str(error)[:500],
                }
            )
        )
