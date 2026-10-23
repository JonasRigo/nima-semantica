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


## Input capacity and traversal budgets

The operator settings distinguish loading a complete scoped snapshot from traversing one claim's reachable dependencies.
The default input allowance is 16,000 nodes and 24,000 edges.
It does not change the public graph's admission capacity, the graph reasoner, retrieval settings, artifact/status rules or scientific certification.

The default reachable traversal allows 4,096 visited nodes, 8,192 traversed edges, 512 returned paths, depth 32 and 50,000 steps, with a 30-second traversal timeout.
Operators may lower these settings or raise traversal settings up to 16,000 visited nodes, 16,384 traversed edges, 2,048 paths, depth 64, 100,000 steps and 120 seconds.
Reaching a budget still returns explicit partial coverage and boundaries; it never turns an unexplored branch into a leaf or a complete cycle search.

These settings belong to the operator component, not the public request.
Tool Guide reports the defaults and ceilings; workflow preview reports its actual configured limits.
For an existing installation, update the native package, the Trace Claim Dependencies component and its saved operator values together, then reconnect clients after the approved service reload.
An old failed operation retains its receipt: use a fresh operation ID for a deliberate retry under the new configuration.
