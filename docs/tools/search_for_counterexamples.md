# Search for Counterexamples

Select a claim, its assumptions, domain and quantifier. Search for a witness that would contradict that precise statement.

## What you provide and receive

You receive candidate witnesses and the results of the represented checks, with receipts and unresolved restrictions. Execution uses the configured isolated worker.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `statement`, `assumptions`, `domain`, `quantifier`, `target_record_id`, `parent_record_id`, `graph_revision`, `context_region_ids`, `encoding`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Search for Counterexamples"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

A checked witness can refute the statement it actually tests. An unsuccessful bounded search leaves the general question open.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
