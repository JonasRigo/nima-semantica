# Load Ontology

List the available ontologies or select a specific name, version and digest before extraction or graph inspection.

## What you provide and receive

You receive the immutable profile and its exact identity. Loading a profile does not modify your graph.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `name`, `version`, `digest`, `offset`, `limit`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Load Ontology"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "mode": "list"
}
```

## Why it works this way

An explicit vocabulary lets you inspect what an extracted relation means and reproduce the interpretation later.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
