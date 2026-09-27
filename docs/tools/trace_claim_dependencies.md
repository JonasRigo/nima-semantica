# Trace Claim Dependencies

Select a claim and trace the premises represented in its current graph or a proposed candidate. Use the result to find cycles, shared assumptions and unfinished dependencies.

## What you provide and receive

You receive a scoped dependency trace and diagnostics at the selected revision. Proposed relationships remain distinguishable from admitted ones.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `claim`, `artifact_id`, `include_proposed`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Trace Claim Dependencies"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

A visible dependency makes an argument easier to inspect. Connectivity alone cannot establish that one scientific statement entails another.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
