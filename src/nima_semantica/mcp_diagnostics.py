"""Bounded MCP readiness checks, independent of authentication discovery."""
from __future__ import annotations

import asyncio
import fcntl
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

from . import __version__


def redacted_error(exc):
    children = getattr(exc, "exceptions", ())
    if children:
        leaves = [redacted_error(child) for child in children[:4]]
        return {"error": type(exc).__name__, "causes": leaves}
    text = str(exc)[:4096]
    for key, value in os.environ.items():
        if value and len(value) >= 8 and any(word in key.upper() for word in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+|(?:token|api[_-]?key|password)\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    return {"error": type(exc).__name__, "message": text[:1024]}


def owner_diagnostic(database):
    path = Path(str(database) + ".owner.lock")
    if not path.exists():
        return {"state": "unoccupied"}
    try:
        with path.open("r") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raw = stream.read(4096)
                try:
                    owner = json.loads(raw)
                    owner = {key: owner[key] for key in ("pid", "uid", "nima_version") if key in owner}
                except (ValueError, TypeError):
                    owner = {"identity": "not recorded by this legacy owner"}
                return {"state": "occupied", "owner": owner,
                    "next_action": "Connect through the managed owner; standalone stdio cannot open a second owner."}
            finally:
                # Closing releases only a lock acquired by this diagnostic process.
                pass
        return {"state": "unoccupied"}
    except OSError as exc:
        return {"state": "inaccessible", **redacted_error(exc)}


def authentication_discovery(response):
    """A web application fallback is not OAuth resource metadata."""
    if not response.is_success:
        return "unsupported"
    try:
        metadata = response.json()
    except ValueError:
        return "unsupported"
    if not isinstance(metadata, dict) or not isinstance(metadata.get("resource"), str):
        return "unsupported"
    servers = metadata.get("authorization_servers")
    return "available" if isinstance(servers, list) and servers and all(isinstance(url, str) for url in servers) else "unsupported"


async def initialize_server(spec, *, timeout=20):
    from mcp import ClientSession, StdioServerParameters
    from contextlib import AsyncExitStack
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    errors = tempfile.TemporaryFile(mode="w+b")
    async def work():
        async with AsyncExitStack() as stack:
            if "url" in spec:
                http = await stack.enter_async_context(httpx.AsyncClient(headers=spec.get("headers", {}), trust_env=False))
                read, write, _ = await stack.enter_async_context(streamable_http_client(spec["url"], http_client=http))
            else:
                read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=spec["command"], args=spec["args"], env={**os.environ, **spec.get("env", {})}), errlog=errors))
            client = await stack.enter_async_context(ClientSession(read, write))
            result = await client.initialize()
            tools = (await client.list_tools()).tools
            return {"transport": "verified", "discovery": "verified", "server_version": result.serverInfo.version,
                    "tool_count": len(tools), "tools": [tool.name for tool in tools]}
    try:
        return await asyncio.wait_for(work(), timeout)
    except Exception as exc:
        errors.flush(); errors.seek(0, 2)
        size = errors.tell(); errors.seek(max(0, size - 4096))
        startup = errors.read(4096).decode("utf-8", errors="replace")
        return {"transport": "failed", "discovery": "unverified", **redacted_error(exc),
                "startup_error": redacted_error(RuntimeError(startup)) if startup else None}
    finally:
        errors.close()


def scoped_mcp_diagnostics(binding, *, root=".", installation_path=None, initialize=True):
    from .config_reconciliation import harness_servers
    try:
        sdk = version("mcp")
    except PackageNotFoundError:
        sdk = None
    result = {"nima_version": __version__, "mcp_sdk_version": sdk,
        "scope": {"corpus_id": binding["corpus_id"], "project_id": binding["project_id"]}, "servers": {}}
    for kind in ("proof", "math"):
        path = Path(binding[kind + "_config"])
        entry = {"configuration": "configured", "authentication_discovery": "not_applicable", "transport": "unverified"}
        try:
            config = json.loads(path.read_text())
            info = path.stat()
            entry["file_access"] = {"state": "readable", "uid": info.st_uid, "mode": oct(info.st_mode & 0o777)}
            entry["scope"] = {"corpus_id": config.get("corpus_id", "papers"), "project_id": config["project_id"]}
            entry["binding"] = "verified" if entry["scope"] == result["scope"] else "mismatch"
            entry["ownership"] = owner_diagnostic(config["database_path"])
            projection = config.get("projection_id", config.get("retrieval_projection_id"))
            if config.get("allow_retrieval"):
                from .corpus_snapshot import CorpusSnapshot
                from .research_retrieval import _projection, ResearchRetrievalRequest, ResearchRetrievalContext
                with CorpusSnapshot(binding["store_root"], **result["scope"]) as store:
                    try:
                        pin = _projection(store, ResearchRetrievalRequest(query="readiness", projection_id=projection),
                                          ResearchRetrievalContext(**result["scope"]))
                        entry["retrieval"] = {"state": "ready", "projection_id": pin.projection_id}
                    except Exception as exc:
                        entry["retrieval"] = {"state": "unavailable", **redacted_error(exc)}
            else:
                entry["retrieval"] = {"state": "disabled"}
            service = binding.get("private_services", {}).get(kind)
            if initialize and entry["binding"] == "verified" and (service or entry["ownership"]["state"] == "unoccupied"):
                if service:
                    from .private_service import private_token
                    spec = {"url": service["url"], "headers": {"Authorization": "Bearer " + private_token(service["token_file"])}}
                    import httpx
                    from urllib.parse import urlsplit, urlunsplit
                    parsed = urlsplit(service["url"])
                    health_url = urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
                    health = httpx.get(health_url, headers=spec["headers"], timeout=5, trust_env=False)
                    health.raise_for_status()
                    actual = health.json()
                    entry["owner_scope"] = actual.get("scope")
                    if actual.get("scope") != result["scope"]:
                        entry.update(binding="mismatch", transport="scope_mismatch")
                        raise ValueError("managed owner serves a different corpus/project scope")
                    entry["managed_owner"] = {"state": "verified", "pid": actual.get("owner_pid"),
                        "uid": actual.get("owner_uid"), "nima_version": actual.get("nima_version")}
                else:
                    spec = {"command": sys.executable, "args": ["-m", "nima_semantica." + kind + "_mcp", "--config", str(path)],
                            "env": {"NIMA_STORE_ROOT": binding["store_root"], **({"NIMA_CONFIG": str(installation_path)} if installation_path else {})}}
                entry.update(asyncio.run(initialize_server(spec)))
            elif entry["ownership"]["state"] == "occupied" and not service:
                entry.update(transport="owner_occupied", discovery="unverified")
        except PermissionError as exc:
            entry.update(configuration="inaccessible", file_access={"state": "permission_denied"}, **redacted_error(exc))
        except Exception as exc:
            entry.update(configuration="failed", **redacted_error(exc))
        result["servers"][kind] = entry
    url = binding.get("mcp_url")
    public = {"configuration": "configured" if url else "absent", "authentication_discovery": "unverified",
              "transport": "unverified", "discovery": "unverified"}
    if url and initialize:
        public.update(asyncio.run(initialize_server({"url": url})))
        import httpx
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        try:
            response = httpx.get(parsed.scheme + "://" + parsed.netloc + "/.well-known/oauth-protected-resource", timeout=5, trust_env=False)
            public["authentication_discovery"] = authentication_discovery(response)
        except httpx.HTTPError:
            public["authentication_discovery"] = "unavailable"
    result["servers"]["research"] = public
    path = Path(root) / ".codex/config.toml"
    if path.exists():
        installed = tomllib.loads(path.read_text()).get("mcp_servers", {})
        desired = harness_servers(binding, installation_path or os.environ.get("NIMA_CONFIG", "installation.json"))
        result["harness_binding"] = {name: "matches" if all(installed.get(name, {}).get(k) == v
            for k, v in spec.items() if k in {"command", "args", "url"}) else "mismatch" for name, spec in desired.items()}
    result["ready"] = all(entry.get("transport") == "verified" and entry.get("binding", "verified") == "verified"
        for entry in result["servers"].values() if entry["configuration"] != "absent")
    return result
