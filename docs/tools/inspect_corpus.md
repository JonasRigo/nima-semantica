# Inspect Corpus

Check which sources are visible in your configured corpus and project, and whether their preparation and indexes are ready.

## What you provide and receive

You receive a bounded, revision-aware inventory with readiness information. Project notes remain visible only within their authorized scope.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `offset`, `limit`, `expected_store_revision`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Inspect Corpus"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{}
```

## Why it works this way

Inspect coverage before searching: an empty answer is easier to interpret when you know which sources were available.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
