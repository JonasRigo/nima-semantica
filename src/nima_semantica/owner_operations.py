"""Explicit maintenance/artifact calls through existing native project MCP."""
import asyncio
import json
import re
from .models import canonical
from .tool_contracts import ToolResult


def call_owner(url, operation, request, *, timeout=1200):
    if not url:
        raise ValueError("Configured corpus owner endpoint is unavailable")
    async def call():
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30, read=timeout)) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    normalize = lambda name: re.sub(r"[^a-z]", "", re.sub(r"_\d+$", "", name.casefold()))
                    matches = [t for t in (await client.list_tools()).tools if normalize(t.name) == normalize(operation)]
                    if len(matches) != 1:
                        raise ValueError("Owner operation is unavailable or ambiguous")
                    tool = matches[0]
                    fields = tool.inputSchema.get("properties", {})
                    args = {"input_value": json.dumps(request)} if "input_value" in fields else {"request": request}
                    return await client.call_tool(tool.name, args)
    try:
        response = asyncio.run(asyncio.wait_for(call(), timeout))
    except BaseExceptionGroup as group:
        leaves=[]
        def collect(error):
            if isinstance(error,BaseExceptionGroup):
                for child in error.exceptions:collect(child)
            else:leaves.append(error)
        collect(group)
        if len(leaves)==1 and isinstance(leaves[0],Exception):raise leaves[0] from group
        raise
    return native_result(response, operation)


def native_result(response, operation):
    """Read exact native objects from structured or fenced Langflow outputs."""
    if response.isError:
        raise ValueError("Owner rejected the maintenance/artifact request")
    results = []
    def visit(value):
        if isinstance(value, dict):
            if value.get("operation") == operation and "data" in value:
                results.append(ToolResult.model_validate(value)); return
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, str):
            value = value.strip()
            fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n\s*```", value, re.DOTALL)
            if fenced:
                value = fenced.group(1)
            try: visit(json.loads(value))
            except (ValueError, TypeError): pass
    visit(response.structuredContent)
    for item in response.content:
        if item.type == "text": visit(item.text)
    if not results or any(canonical(result) != canonical(results[0]) for result in results):
        raise ValueError("Owner returned no unambiguous native result")
    return results[0]
