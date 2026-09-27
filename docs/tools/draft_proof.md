# Draft Proof

Use the Draft Proof skill to compare strategies, identify useful lemmas and record obstacles in a private proof session.

## What you provide and receive

You receive a durable graph of targets, assumptions, strategies and open obligations. A draft can close with explicitly unresolved work and later be inspected or continued.

This capability is a skill over the private proof MCP service, not an additional Langflow workflow. The example below is a `nima_proof_open` request. See [private graph sessions](../PRIVATE_GRAPHS.md).

Request fields: `request_id`, `target`, `mode`, `assumptions`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Draft Proof"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "request_id": "proof-1",
  "target": "Prove the specified lemma",
  "mode": "draft"
}
```

## Why it works this way

Keep exploratory arguments outside project knowledge until you decide what to retain. You can use comparison, retrieval, extraction, counterexamples and the rest of the toolbox while developing the draft.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
