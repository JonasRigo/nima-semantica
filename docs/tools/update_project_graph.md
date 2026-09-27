# Update Project Graph

Review a proposed graph change or progress record, then approve the exact change against the current project revision. Use preview before requesting execution.

## What you provide and receive

You receive validation results and, after an authorized commit, a new revision and commit receipt. Stale revisions, mismatched approvals and invalid evidence are rejected.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `delta`, `artifact_id`, `progress_proposal_ids`, `record_historical_progress`, `correction_bindings`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Update Project Graph"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

We keep admission explicit because a useful proposal and an accepted research record have different roles. Approval applies to the selected change, not to future agent actions.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
