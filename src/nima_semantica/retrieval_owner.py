"""Route embedding attempts through the existing project research MCP owner."""
import asyncio
import json
import os
import re

from .models import canonical
from .tool_contracts import ToolResult


def retrieve_through_owner(store, request, context, *, timeout=1200):
    url = os.environ.get("NIMA_RESEARCH_MCP_URL")
    if not url:
        raise ValueError("retrieval.embedding_requires_corpus_owner: configure NIMA_RESEARCH_MCP_URL for this project")

    async def call():
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30, read=timeout)) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    matches = [tool for tool in (await client.list_tools()).tools if
                        re.sub(r"_\d+$", "", tool.name.casefold()).replace("_", "").replace(" ", "") == "retrieveresearchcontext"]
                    if len(matches) != 1:
                        raise ValueError("retrieval corpus owner operation is unavailable or ambiguous")
                    tool = matches[0]
                    fields = tool.inputSchema.get("properties", {})
                    arguments = ({"input_value": request.model_dump_json()} if "input_value" in fields else
                                 {"request": request.model_dump(mode="json")})
                    return await client.call_tool(tool.name, arguments)
    response = asyncio.run(asyncio.wait_for(call(), timeout=timeout))
    if response.isError:
        raise ValueError("retrieval corpus owner rejected the request")
    found = []
    def visit(value):
        if isinstance(value, dict):
            if value.get("operation") == "Retrieve Research Context" and "data" in value:
                found.append(ToolResult.model_validate(value)); return
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, str):
            try: visit(json.loads(value))
            except (ValueError, TypeError): pass
    if response.structuredContent:
        visit(response.structuredContent)
    for item in response.content:
        if item.type == "text": visit(item.text)
    if not found or any(canonical(r) != canonical(found[0]) for r in found):
        raise ValueError("retrieval corpus owner returned no unambiguous native result")
    result = found[0]
    if result.status in {"complete", "partial"}:
        data = result.data
        if (data["corpus_id"], data["project_id"], data["projection_id"]) != (
                context.corpus_id, context.project_id, request.projection_id):
            raise ValueError("retrieval corpus owner scope/projection mismatch")
    return result
