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
