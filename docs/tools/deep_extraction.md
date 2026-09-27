# Deep Extraction

Select prepared source regions and an ontology, then ask for entities, claims and relations relevant to your question. Inspect the source coverage before treating the proposal as a document-level account.

## What you provide and receive

You receive regional extraction records, reconciled graph proposals, exact source references and unresolved issues. The proposal remains separate from accepted project knowledge.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `question`, `source_region_ids`, `source_id`, `ontology_profile`, `graph_revision`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Deep Extraction"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Extraction makes an interpretation explicit enough to challenge. Use the source passages and graph inspection tools to review it before admission.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
