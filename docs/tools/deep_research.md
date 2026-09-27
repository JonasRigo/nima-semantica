# Deep Research

Use bounded literature passes to investigate a question, search phrases and coverage facets. Continue from a saved review graph when you want to pursue its remaining gaps.

## What you provide and receive

The current implementation searches configured providers, selects literature, reads bounded passages, proposes regional graphs and consolidates a provisional review graph. Inspect the reported coverage: a selected passage does not imply that the whole PDF was read.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `research`, `search_phrases`, `coverage_facets`, `input_graph_artifact_id`, `focus_question`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Deep Research"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "research": {
    "mode": "preview"
  }
}
```

## Why it works this way

Fast reading helps you decide what deserves closer examination. Prepare selected sources through the canonical ingestion path and reconcile exact evidence before proposing project admission.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
