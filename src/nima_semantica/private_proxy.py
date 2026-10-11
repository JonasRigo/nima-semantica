"""Stdio client adapter to the operator's existing authenticated private owner."""
import argparse
import asyncio
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

from . import __version__
from .private_service import private_token


async def proxy(url, token_file):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    import httpx
    if urlsplit(url).hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("private owner proxy requires a loopback endpoint")
    token = private_token(token_file)
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(30, read=3600), trust_env=False))
        read, write, _ = await stack.enter_async_context(streamable_http_client(url, http_client=http))
        client = await stack.enter_async_context(ClientSession(read, write))
        await client.initialize()
        server = Server("NIMA private owner proxy", version=__version__)

        @server.list_tools()
        async def list_tools():
            return (await client.list_tools()).tools

        @server.call_tool()
        async def call_tool(name, arguments):
            return await client.call_tool(name, arguments)

        incoming, outgoing = await stack.enter_async_context(stdio_server())
        await server.run(incoming, outgoing, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    asyncio.run(proxy(args.url, args.token_file))


if __name__ == "__main__":
    main()
