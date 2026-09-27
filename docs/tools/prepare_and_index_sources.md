# Prepare and Index Sources

Prepare a paper or your notes so you can retrieve exact passages. For individual local files, use the ingestion CLI described in the knowledge-base guide.

## What you provide and receive

You receive source and region identifiers, preparation receipts and the actual index status. PDFs use the advanced NIMA PDF normalizer; unavailable normalization produces an explicit failure. Lexical indexes need no embedding model; vector indexes require the configured embedding identity.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `sources`, `index_mode`, `operation_id`, `run_id`, `expected_store_revision`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Prepare and Index Sources"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview",
  "sources": [
    {
      "name": "paper.md",
      "text": "A source statement."
    }
  ]
}
```

## Why it works this way

We separate source preparation from claim extraction. Making a paper searchable does not commit its interpretation to your project graph.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
