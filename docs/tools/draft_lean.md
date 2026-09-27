# Draft Lean

Select the informal target, definitions and evidence, then draft Lean modules in the configured environment. Use verifier diagnostics to revise the attempt without changing the target silently.

## What you provide and receive

You receive proposed formal code, dependency information and any scoped verification attempts. External LeanSearch is used only when enabled in your configuration.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `target`, `dependencies`, `proof_dag_record_id`, `prior_artifact_ids`, `source_region_ids`, `target_record_id`, `parent_record_id`, `environment_digest`, `targets`, `expected_declaration_type_fingerprints`, `initial_modules`, `initial_root_modules`, `declaration_check_obligations`, `allow_incomplete_workflow_obligations`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Draft Lean"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Translation is a mathematical task: a compiling theorem about the wrong statement does not settle the intended claim.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
