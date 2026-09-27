"""Value access and symbolic scalar comparisons used by private calculation graphs."""
from __future__ import annotations
import ast
import re
from typing import Any
import sympy as sp

def path_value(value: Any, path: str):
    current = value
    for part in (() if path == "$" else path.split(".")):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current

def equivalent(left: Any, right: Any):
    if left == right:
        return True
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return all(equivalent(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
        return all(equivalent(left[key], right[key]) for key in left)
    if isinstance(left, (str, int, float)) and isinstance(right, (str, int, float)):
        try:
            names = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(left) + " " + str(right)))
            local = {name: sp.Symbol(name) for name in names}
            return sp.simplify(sp.sympify(str(left).replace("^", "**"), locals=local)
                - sp.sympify(str(right).replace("^", "**"), locals=local)) == 0
        except Exception:
            return str(left).replace(" ", "") == str(right).replace(" ", "")
    return False

def _scalar_expression(value: str) -> sp.Expr:
    """Parse elementary symbolic arithmetic without evaluating untrusted Python."""
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError("short scalar expression required")
    tree = ast.parse(value.replace("^", "**"), mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 48:
        raise ValueError("scalar expression too complex")

    def convert(node):
        if isinstance(node, ast.Expression):
            return convert(node.body)
        if isinstance(node, ast.Name) and len(node.id) <= 64:
            return sp.Symbol(node.id)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return sp.sympify(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = convert(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left, right = convert(node.left), convert(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow) and right.is_Integer and abs(int(right)) <= 6:
                return left ** right
        raise ValueError("unsupported scalar expression")

    return convert(tree)
