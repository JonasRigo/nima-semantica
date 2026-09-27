# Research Run

Record the objective and progress of an investigation that spans several tools or chat sessions. Create a run, then record its transitions and inspect its receipts.

## What you provide and receive

You receive a persistent run record and its revision. Repeated requests retain their recorded identities; transitions must match the current run state.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `run_id`, `operation_id`, `creation`, `transition`, `expected_store_revision`, `limit`, `after_run_revision`, `after_receipt_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Research Run"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "mode": "inspect",
  "run_id": "inspection-run"
}
```

## Why it works this way

A run records your decisions. It does not choose the next experiment or schedule an autonomous research loop.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
