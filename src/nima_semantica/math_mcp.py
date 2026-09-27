"""Local stdio MCP transport; all mathematical behavior lives in the service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import inspect
import json
import os
from pathlib import Path
import threading

from .math_mcp_contracts import TOOLS, invoke, operation_catalogue
from .math_session_service import MathServiceConfig, MathSessionService

_retrieval_lock = threading.RLock()
_canvas_services = {}
_canvas_lock = threading.RLock()


class ConfiguredMathService(MathSessionService):
    def _dispatch(self, graph, binding, name, arguments, *, research_store=None, worker=None):
        if name != "retrieve_context" or not binding.allow_retrieval or research_store is not None:
            return super()._dispatch(graph, binding, name, arguments, research_store=research_store, worker=worker)
        # Store lease is short-lived and operator selected, independent of Langflow.
        from .storage import GraphStore
        with _retrieval_lock:
            root = os.environ.get("NIMA_STORE_ROOT")
            if not root or not (Path(root) / "graph.sqlite3").is_file():
                raise ValueError("NIMA_STORE_ROOT must identify an existing corpus store")
            store = GraphStore(Path(root))
            try:
                return super()._dispatch(graph, binding, name, arguments, research_store=store, worker=worker)
            finally:
                store.close()


def load_service(config_path):
    return ConfiguredMathService(MathServiceConfig.model_validate_json(Path(config_path).read_text()))


def canvas_service():
    """One owner per configured database in the Langflow process, never model-selected."""
    import atexit
    path = os.environ.get("NIMA_MATH_CONFIG")
    if not path:
        raise ValueError("Set operator-owned NIMA_MATH_CONFIG before executing these canvases")
    path = str(Path(path).resolve())
    with _canvas_lock:
        fingerprint = Path(path).read_bytes()
        if path in _canvas_services:
            old, service, _ = _canvas_services[path]
            if old != fingerprint:
                raise ValueError("Math configuration changed; restart the canvas process")
            return service
        service = load_service(path)
        owner = exclusive_owner(service)
        owner.__enter__()
        atexit.register(owner.__exit__, None, None, None)
        _canvas_services[path] = (fingerprint, service, owner)
        return service


@contextmanager
def exclusive_owner(service):
    """Separate from short SQLite transactions; never recover another live owner."""
    with open(str(service.path) + ".owner.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        service.recover_interrupted()
        yield


def create_server(service):
    from mcp.server.fastmcp import FastMCP
    import anyio
    server = FastMCP("NIMA Calculate Mathematics · Candidate")

    def register(operation, schema, description):
        async def call(request):
            return await anyio.to_thread.run_sync(lambda: invoke(service, operation, request.model_dump(mode="json", exclude_unset=True)))
        call.__name__ = "nima_math_" + operation
        call.__annotations__ = {"request": schema, "return": dict}
        call.__signature__ = inspect.Signature([
            inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=schema)], return_annotation=dict)
        server.tool(name=call.__name__, description=description)(call)

    for operation, (schema, description) in TOOLS.items():
        register(operation, schema, description)

    @server.tool()
    def nima_math_operations() -> dict:
        """Discover exact atomic operation arities and a minimal calculation example."""
        return operation_catalogue()

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Trusted local JSON configuration; never a tool argument")
    args = parser.parse_args()
    service = load_service(args.config)
    with exclusive_owner(service):
        create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
