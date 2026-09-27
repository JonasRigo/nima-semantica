# Compare Research Objects

Choose hypotheses, mathematical objects or exact project targets and state the comparison criteria. Supply explicit content for objects that have no canonical graph identity.

## What you provide and receive

You receive criterion-specific comparisons with supporting evidence and unresolved differences. Proof-specific target modes require the corresponding exact project records.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `target`, `objects`, `criteria`, `objective`, `source_region_ids`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Compare Research Objects"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Comparison should expose why alternatives differ. A ranking or resemblance does not supply a missing proof or justify graph admission.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
