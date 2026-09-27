# Tool Guide

Find the exact contract before asking your agent to call an unfamiliar tool. An empty request lists the capabilities; select a name to inspect its schema and examples.

## What you provide and receive

You receive the input and output schemas, transport information and the scope of the operation. This lookup neither opens your store nor executes the selected tool.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `tool`.
Use [Tool Guide](tool_guide.md) with `{"tool": "Tool Guide"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

```json
{}
```

## Why it works this way

Discovery is deterministic so that your agent can inspect the installed interface without relying on remembered instructions.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
