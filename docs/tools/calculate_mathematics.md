# Calculate Mathematics

Use the Calculate Mathematics skill to derive a quantity in a private calculation graph. Open a session, record the task and assumptions, retrieve methods, and execute selected calculations.

## What you provide and receive

You receive revisioned steps, dependency links, worker receipts, unresolved obligations and an inspectable submission. The stdio MCP service exposes the individual operations; optional Langflow canvases expose the same service.

Open the session through `nima_math_open`; inspect the available operations with `nima_math_operations`. See [private graph sessions](../PRIVATE_GRAPHS.md).

Request fields: `request_id`, `task`, `required_paths`, `task_facts`, `indexed_sets`, `session_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Calculate Mathematics"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This example opens a private calculation session.

```json
{
  "request_id": "calculation-1",
  "task": "Compute an antiderivative of x**2.",
  "required_paths": [
    "answer"
  ]
}
```

## Why it works this way

Your coding agent controls the mathematical strategy. NIMA retains the derivation and execution evidence, so a change of strategy or a new chat does not erase earlier work.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)


## Normalized calculus

The atomic interface supports `integrate`, `limit`, `series`, `truncate_series`, `gamma`, `log`, `exp` and the controlled constant `infinity`.
These execute through the same isolated worker as the existing algebraic operations.
They do not accept executable expression strings or arbitrary function names.

| Operation | Operands | Options |
|---|---|---|
| `integrate` | expression, variable; optionally lower, upper | Both bounds together; an unbounded antiderivative determines no arbitrary constant |
| `limit` | expression, variable, approach point | Explicit `options={"direction":"+"}`, `"-"` or `"+-"` |
| `series` | expression, variable, point, integer order | Explicit direction `"+"` or `"-"`; order 1–20 |
| `truncate_series` | series expression | Retains discarded remainder in the graph; creates an approximation obligation |
| `gamma`, `log`, `exp` | one expression | No options |
| `infinity` | none | Positive infinity; use `negative` for the negative endpoint |

Variables, bounds, expansion points and orders are operand references, with the usual input provenance.
A symbol may declare true `symbol_assumptions` flags from `real`, `positive`, `nonnegative`, `negative`, `nonzero`, `integer`.
Those declarations remain explicit support obligations; a spelling match in a task does not establish a domain restriction.
The same symbol cannot be redeclared with different assumptions in one batch.
Convergence conditions and unevaluated expressions remain visible and open; nonfinite computed results require review.
Infinity used as an integration endpoint is a structural constant, not a failed finite result.
Receipts retain conditional/unevaluated/nonfinite diagnostics and the series remainder, and exact reuse retains operation options and domains.
Analytic continuation and convergence are different claims.

For the pseudogap threshold integral, build `x**(1-epsilon)/(x+a)**2`, then use `integrate` with bounds `0` and `infinity`.
For positive `a` and `epsilon`, SymPy returns a conditional expression including `epsilon < 2`.
Keep that condition; do not replace a conditional integral with an unconditional Gamma expression without justification.
The Gamma product at `a=1` has Laurent series `1/epsilon - 1 + pi**2*epsilon/6 + O(epsilon**2)`.
Multiplication by `-epsilon` followed by the right limit at zero gives pole residue `-1`.
Use the new graph operations for this calculation, rather than recording its answer as a premise.
