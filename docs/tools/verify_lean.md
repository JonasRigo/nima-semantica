# Verify Lean

Submit the selected Lean modules, declaration targets and pinned environment for isolated verification. Preserve the intended declaration types when iterating.

## What you provide and receive

You receive compiler diagnostics, execution receipts and scoped verification results. Missing workers, rejected axioms or incomplete proofs remain explicit outcomes.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `sources`, `module_order`, `targets`, `imports`, `expected_declaration_type_fingerprints`, `proof_target_id`, `parent_node_ids`, `definition_node_ids`, `source_region_ids`, `graph_revision`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Verify Lean"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

The kernel checks the formal statement. You still need to establish that this statement expresses the scientific or mathematical claim you intended.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
