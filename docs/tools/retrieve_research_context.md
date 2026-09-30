# Retrieve Research Context

Search for passages relevant to a focused question using lexical retrieval or a configured vector projection. Include graph context when relationships help orient the investigation.

## What you provide and receive

You receive bounded passages, exact evidence references and scoped graph context. Projection and source revisions identify the material searched.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `query`, `mode`, `projection_id`, `expected_store_revision`, `limit`, `max_hops`, `max_nodes`, `max_edges`, `max_neighbors`, `max_results`, `max_chars`, `operation_id`, `run_id`.
`include_provisional` defaults to `true`; set it to `false` to exclude fast-prepared
regions and provisional graph context. Included fast evidence carries its quality
label in the returned region and citation locator; graph context retains evidence labels.
Use [Tool Guide](tool_guide.md) with `{"tool": "Retrieve Research Context"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "query": "Find the stated assumptions",
  "mode": "lexical"
}
```

## Why it works this way

Retrieval finds candidate evidence. Read consequential passages in their exact source context before drawing a conclusion.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
