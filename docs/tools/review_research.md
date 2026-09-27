# Review Research

Pin the claim, argument or artifact you want reviewed. Ask for a focused assessment of its evidence, assumptions and stated conclusions.

## What you provide and receive

You receive structured assessments and supporting records. An adverse missing-support finding needs a matching authenticated substantiation result before publication as a supported conclusion.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `graph_revision`, `artifact_id`, `targets`, `source_region_ids`, `target_record_id`, `parent_record_id`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Review Research"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Review is most useful when you can follow each objection to the precise target and evidence. Unresolved objections and failed checks remain part of the record.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
