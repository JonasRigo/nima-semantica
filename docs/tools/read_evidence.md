# Read Evidence

Read a region returned by retrieval, follow an exact evidence reference, or inspect a saved artifact by its identifier.

## What you provide and receive

You receive scope-checked source text or artifact content with provenance. Hash, revision and size checks prevent silently substituting unrelated or changed material.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `artifact_id`, `region_id`, `reference`, `encoding`, `render`, `max_bytes`, `max_links`, `expected_store_revision`, `expected_source_revision`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Read Evidence"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "region_id": "region-id-from-retrieval"
}
```

## Why it works this way

A citation should lead back to inspectable evidence. This tool is also the readback step after saving an analysis.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
