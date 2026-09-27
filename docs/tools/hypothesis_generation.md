# Hypothesis Generation

Describe your objective, known evidence, required assumptions and constraints. Ask for new hypotheses or a reformulation of an existing one.

## What you provide and receive

You receive grounded proposals, constraint responses, dependencies and unresolved checks. Saved proposals do not automatically become accepted graph facts.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `objective`, `graph_targets`, `source_region_ids`, `prior_hypothesis_ids`, `required_assumptions`, `constraints`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Hypothesis Generation"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Alternative explanations are useful when their assumptions are visible. Keep the evidence and possible falsifiers beside each proposal.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
