# Save Research Analysis

Save an analysis you have assembled, including its sections, citations, artifacts, receipts and limitations. Supply the current graph revision and a unique operation ID.

## What you provide and receive

You receive immutable JSON and Markdown artifacts, an outcome and receipts. Read Evidence can open the returned IDs. Reusing an identical operation returns its saved result; changed input under the same ID is rejected.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `bundle`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Save Research Analysis"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Saving is deterministic and uses no LLM. It preserves your analysis without deciding which conclusions you should accept; recording graph progress is a separate approved update.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
