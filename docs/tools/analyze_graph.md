# Analyze Graph

Inspect the structure of the current graph or a selected graph candidate. Choose strict analysis or explicitly hypothetical reasoning and pin the revision.

## What you provide and receive

You receive translated premises, dependency findings, bounded inference results and provenance for the derivations. Resource limits or missing vocabulary can leave the analysis partial.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Analyze Graph"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Logical consequences are conditional on the encoded premises. A recorded verification status is metadata, not a fresh proof of the claim.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
