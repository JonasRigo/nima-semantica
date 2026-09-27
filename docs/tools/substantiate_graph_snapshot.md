# Substantiate Graph Snapshot

Check whether selected claims and relations faithfully represent the available source evidence. Bind the check to an exact graph revision and target set.

## What you provide and receive

You receive attributed supporting or contradicting passages, target assessments, unresolved searches and possible corrections. No correction is applied automatically.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `targets`, `source_region_ids`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Substantiate Graph Snapshot"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Failure to locate support is a finding about the searched scope. It should not become an unrestricted assertion that no support exists.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
