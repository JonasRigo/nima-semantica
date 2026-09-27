# Conduct Proof

Use the Conduct Proof skill to develop a selected argument, record its steps and address the represented obligations in a private proof session.

## What you provide and receive

You receive an inspectable argument, supporting receipts and the remaining frontier. Submission requires the current represented obligations to be addressed. Optional Lean evidence retains its precise formal scope.

This capability is a skill over the private proof MCP service, not an additional Langflow workflow. The example below is a `nima_proof_open` request. See [private graph sessions](../PRIVATE_GRAPHS.md).

Request fields: `request_id`, `target`, `mode`, `assumptions`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Conduct Proof"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{
  "request_id": "proof-1",
  "target": "Prove the specified lemma",
  "mode": "conduct"
}
```

## Why it works this way

A complete graph can still omit an assumption. Your agent must examine the argument itself and use the specialist tools when they help; project admission remains a separate decision.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
