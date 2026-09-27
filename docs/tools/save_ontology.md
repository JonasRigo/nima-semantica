# Save Ontology

Validate your own vocabulary, then save an immutable version for subsequent extraction and graph operations. Start with validation before enabling a write.

## What you provide and receive

You receive validation diagnostics and, after saving, an immutable ontology identity. Saving requires the configured write permission and an operation ID.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `profile`, `operation_id`, `run_id`, `expected_store_revision`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Save Ontology"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "mode": "validate",
  "profile": {
    "name": "custom",
    "version": "1.0.0",
    "node_types": [
      {
        "name": "claim",
        "description": "A claim"
      }
    ]
  }
}
```

## Why it works this way

Version the vocabulary when its meaning changes. A new ontology does not reinterpret or verify existing claims automatically.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
