"""Preview, guard and recover an entire operator configuration transition."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

from .models import canonical, identity
from .storage import atomic_bytes


def file_identity(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("configuration destination must not be a symlink")
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, (str, int, float)):
        return json.dumps(value)
    raise ValueError("unsupported harness TOML value")


def harness_servers(binding, config_path):
    common = {"NIMA_CONFIG": str(Path(config_path).resolve()), "NIMA_STORE_ROOT": binding["store_root"]}
    if binding.get("mcp_url"):
        common["NIMA_RESEARCH_MCP_URL"] = binding["mcp_url"]
    servers = {}
    for kind in ("proof", "math"):
        service = binding.get("private_services", {}).get(kind)
        args = (["-m", "nima_semantica.private_proxy", "--url", service["url"], "--token-file", service["token_file"]]
                if service else ["-m", "nima_semantica." + kind + "_mcp", "--config", binding[kind + "_config"]])
        servers["nima-" + kind] = {"command": sys.executable, "args": args, "env": common,
            "startup_timeout_sec": 30, "tool_timeout_sec": 3600}
    if binding.get("mcp_url"):
        servers["nima"] = {"url": binding["mcp_url"], "startup_timeout_sec": 30, "tool_timeout_sec": 3600}
    return servers


def reconciliation_preview(root, config_path, binding, harnesses, *, project_manifest=None, owner_uid=None, owner_gid=None):
    root = Path(root).resolve()
    if set(harnesses) - {"codex", "claude", "opencode"}:
        raise ValueError("unsupported harness")
    scope = (binding["corpus_id"], binding["project_id"])
    planned = {root / ".nima/project.json": canonical(binding) + b"\n"}
    if project_manifest is not None:
        planned[Path(project_manifest).resolve()] = canonical(binding) + b"\n"
    # Check scope/projection before planning any path change. Preserve legacy files.
    for kind, pin in (("proof", "projection_id"), ("math", "retrieval_projection_id")):
        source = Path(binding[kind + "_config"])
        data = json.loads(source.read_text())
        if (data.get("corpus_id", "papers"), data["project_id"]) != scope:
            raise ValueError(kind + " configuration scope differs from target binding")
        if binding.get("projection_id") and data.get(pin) != binding["projection_id"]:
            raise ValueError(kind + " projection differs; operator must select an explicit coherent rebind")
        if source.stat().st_mode & 0o077:
            raise ValueError(kind + " configuration must remain private")
    servers = harness_servers(binding, config_path)
    if "codex" in harnesses:
        path = root / ".codex/config.toml"
        text = path.read_text() if path.exists() else ""
        old = tomllib.loads(text).get("mcp_servers", {})
        # Remove only NIMA tables; retain every other table byte-for-byte.
        blocks = re.split(r"(?m)(?=^\s*\[[^\n]+\]\s*$)", text)
        kept = []
        for block in blocks:
            header = re.match(r"\s*\[([^\n]+)\]", block)
            if header:
                parsed = tomllib.loads("[" + header.group(1) + "]\n")
                if set(parsed.get("mcp_servers", {})) & set(servers):
                    continue
            kept.append(block)
        text = "".join(kept)
        for name, server in servers.items():
            server = dict(server)
            policies = dict(old.get(name, {}))
            for budget in ("startup_timeout_sec", "tool_timeout_sec"):
                if budget in policies and budget in server:
                    server[budget] = max(policies[budget], server[budget])
            for key in ("command", "args", "url", "bearer_token_env_var", "http_headers", "env_http_headers"):
                policies.pop(key, None)
            environment = {**policies.get("env", {}), **server.get("env", {})}
            policies.update(server)
            if environment:
                policies["env"] = environment
            text += "\n[mcp_servers." + json.dumps(name) + "]\n"
            text += "".join(json.dumps(k) + " = " + _toml_value(v) + "\n" for k, v in policies.items())
        tomllib.loads(text)
        planned[path] = text.encode()
    for harness, filename, table in (("claude", ".mcp.json", "mcpServers"), ("opencode", "opencode.json", "mcp")):
        if harness not in harnesses:
            continue
        path = root / filename
        data = json.loads(path.read_text()) if path.exists() else {}
        settings = data.setdefault(table, {})
        for name, server in servers.items():
            value = {k: v for k, v in server.items() if k not in {"startup_timeout_sec", "tool_timeout_sec"}}
            if harness == "claude" and "url" in value:
                value["type"] = "http"
            if harness == "opencode":
                value = ({"type": "remote", "url": value["url"], "enabled": True} if "url" in value else
                    {"type": "local", "command": [value["command"], *value["args"]], "environment": value["env"], "enabled": True})
            settings[name] = {**{k: v for k, v in settings.get(name, {}).items()
                if k not in {"type", "command", "args", "env", "environment", "url"}}, **value}
        planned[path] = (json.dumps(data, indent=2) + "\n").encode()
    changes = [{"path": str(path), "expected_sha256": file_identity(path),
                "payload_base64": base64.b64encode(payload).decode(),
                "after_sha256": hashlib.sha256(payload).hexdigest(), "mode": 0o600,
                "owner_uid": owner_uid if owner_uid is not None else (path.stat().st_uid if path.exists() else os.getuid()),
                "owner_gid": owner_gid if owner_gid is not None else (path.stat().st_gid if path.exists() else os.getgid())}
               for path, payload in planned.items()]
    inputs = [{"path": str(Path(config_path).resolve()), "expected_sha256": file_identity(config_path)}]
    inputs.extend({"path": str(Path(binding[k + "_config"]).resolve()),
                   "expected_sha256": file_identity(binding[k + "_config"])} for k in ("math", "proof"))
    result = {"format_version": 1, "scope": {"corpus_id": scope[0], "project_id": scope[1]},
              "changes": changes, "inputs": inputs}
    result["preview_hash"] = identity(result)
    return result


def apply_reconciliation(preview, directory, *, checkpoint=None):
    preview = dict(preview)
    expected = preview.pop("preview_hash")
    if identity(preview) != expected:
        raise ValueError("reconciliation preview hash mismatch")
    for entry in [*preview["changes"], *preview.get("inputs", [])]:
        if file_identity(entry["path"]) != entry["expected_sha256"]:
            raise ValueError("configuration changed since preview: " + entry["path"])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    journal = {**preview, "preview_hash": expected, "status": "prepared"}
    for n, change in enumerate(journal["changes"]):
        path = Path(change["path"])
        change["before_mode"] = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        change["before_uid"] = path.stat().st_uid if path.exists() else None
        change["before_gid"] = path.stat().st_gid if path.exists() else None
        change["backup"] = str((directory / f"{n}.before").resolve()) if path.exists() else None
        if path.exists():
            atomic_bytes(Path(change["backup"]), path.read_bytes())
            if file_identity(change["backup"]) != change["expected_sha256"]:
                raise ValueError("configuration backup verification failed")
    manifest = directory / "recovery.json"
    atomic_bytes(manifest, canonical(journal))
    return recover_reconciliation(manifest, "complete", checkpoint=checkpoint)


def recover_reconciliation(manifest, mode, *, checkpoint=None):
    manifest = Path(manifest)
    journal = json.loads(manifest.read_text())
    original = {key: journal[key] for key in ("format_version", "scope", "inputs")}
    original["changes"] = [{key: item[key] for key in ("path", "expected_sha256", "payload_base64", "after_sha256", "mode", "owner_uid", "owner_gid") if key in item}
                           for item in journal["changes"]]
    if identity(original) != journal["preview_hash"]:
        raise ValueError("recovery manifest preview identity mismatch")
    if mode not in {"restore", "complete"}:
        raise ValueError("recovery mode must be restore or complete")
    if mode == "complete":
        for entry in journal.get("inputs", []):
            if file_identity(entry["path"]) != entry["expected_sha256"]:
                raise ValueError("recovery input changed since preview: " + entry["path"])
    # Validate every destination and backup before changing any file.
    for change in journal["changes"]:
        if change["backup"] and Path(change["backup"]).parent.resolve() != manifest.parent.resolve():
            raise ValueError("recovery backup must belong to this recovery directory")
        if file_identity(change["path"]) not in {change["expected_sha256"], change["after_sha256"]}:
            raise ValueError("recovery refuses unrelated configuration drift")
        if change["backup"] and file_identity(change["backup"]) != change["expected_sha256"]:
            raise ValueError("recovery backup digest mismatch")
        if hashlib.sha256(base64.b64decode(change["payload_base64"], validate=True)).hexdigest() != change["after_sha256"]:
            raise ValueError("recovery payload digest mismatch")
    for change in journal["changes"]:
        path = Path(change["path"])
        if file_identity(path) not in {change["expected_sha256"], change["after_sha256"]}:
            raise ValueError("configuration changed during recovery: " + str(path))
        prior_owner = (path.stat().st_uid, path.stat().st_gid) if path.exists() else (os.getuid(), os.getgid())
        if mode == "complete":
            atomic_bytes(path, base64.b64decode(change["payload_base64"], validate=True))
            path.chmod(change["mode"])
            uid, gid = change.get("owner_uid", prior_owner[0]), change.get("owner_gid", prior_owner[1])
        elif change["backup"]:
            atomic_bytes(path, Path(change["backup"]).read_bytes())
            path.chmod(change["before_mode"])
            uid, gid = change.get("before_uid", prior_owner[0]), change.get("before_gid", prior_owner[1])
        else:
            path.unlink(missing_ok=True)
            uid = gid = None
        if uid is not None and gid is not None and (path.stat().st_uid, path.stat().st_gid) != (uid, gid):
            os.chown(path, uid, gid)
        if checkpoint:
            checkpoint(str(path))
    journal["status"] = "complete" if mode == "complete" else "restored"
    atomic_bytes(manifest, canonical(journal))
    return {"status": journal["status"], "recovery_manifest": str(manifest), "scope": journal["scope"]}
