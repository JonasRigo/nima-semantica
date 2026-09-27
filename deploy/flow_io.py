"""Shared publication I/O and installed-port validation; never generates flows."""

from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def write_json(path, value):
    """Atomically write owner-only JSON, including when replacing an old file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def credential_reference(name, field):
    """A native variable name is exportable metadata, never a resolved secret."""
    return (name in {"api_key", "openai_api_key"}
            and field.get("load_from_db") is True
            and isinstance(field.get("value"), str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", field["value"]) is not None)


def is_nima_component_group(group):
    """Recognize legacy sidebar labels and current Langflow extension IDs."""
    return group.startswith("NIMA ") or group.startswith("nima_")


def redacted(flow):
    result = copy.deepcopy(flow)
    for node in result["data"]["nodes"]:
        for name, field in node["data"]["node"]["template"].items():
            if isinstance(field, dict) and (
                field.get("password")
                or any(word in name.lower() for word in ("token", "secret", "api_key"))
            ):
                # max_output_tokens is a numeric option, not a credential.
                if name in ("max_tokens", "max_output_tokens", "num_predict"):
                    continue
                if credential_reference(name, field):
                    continue
                field["value"] = ""
                field["load_from_db"] = False
    return result


def validate_edges(flow):
    nodes = {n["id"]: n for n in flow["data"]["nodes"]}
    if len(nodes) != len(flow["data"]["nodes"]):
        raise ValueError("Duplicate node IDs")
    seen = set()
    aliases = {"JSON": "Data", "Table": "DataFrame"}
    for edge in flow["data"]["edges"]:
        if edge["id"] in seen:
            raise ValueError("Duplicate edge IDs")
        seen.add(edge["id"])
        if edge["source"] not in nodes or edge["target"] not in nodes:
            raise ValueError("Dangling edge")
        for kind, endpoint in (("sourceHandle", "source"), ("targetHandle", "target")):
            handle = edge["data"][kind]
            if json.loads(edge[kind].replace("œ", '"')) != handle or handle["id"] != edge[endpoint]:
                raise ValueError("Stale editor handle")
        source = nodes[edge["source"]]["data"]
        target = nodes[edge["target"]]["data"]["node"]
        sh, th = edge["data"]["sourceHandle"], edge["data"]["targetHandle"]
        outputs = {o["name"]: o for o in source["node"]["outputs"]}
        if sh["name"] not in outputs or th["fieldName"] not in target["template"]:
            raise ValueError("Missing edge port")
        output = outputs[sh["name"]]
        field = target["template"][th["fieldName"]]
        offered = output.get("types", [])
        accepted = field.get("input_types") or [field.get("type")]
        if set(sh.get("output_types", [])) != set(offered):
            raise ValueError("Stale output types")
        if set(th.get("inputTypes", [])) != set(field.get("input_types") or []):
            raise ValueError("Stale input types")
        if not ({aliases.get(t, t) for t in offered} & {aliases.get(t, t) for t in accepted}):
            raise ValueError("Incompatible edge types")
        if not any(o.get("group_outputs") or o.get("allows_loop") for o in outputs.values()):
            if source.get("selected_output") != sh["name"]:
                raise ValueError("Connected output is hidden in editor")


def bind_palette(flow, palette):
    bindings = []
    for node in flow["data"]["nodes"]:
        name, generated = node["data"]["type"], node["data"]["node"]
        custom = "nima_semantica." in generated["template"]["code"]["value"]
        matches = [
            (group, key, item)
            for group, entries in palette.items()
            for key, item in entries.items()
            if item.get("name", key) == name and is_nima_component_group(group) == custom
        ]
        if len(matches) != 1:
            raise ValueError(f"{name}: expected one installed palette class, found {len(matches)}")
        group, key, installed = matches[0]
        for field, spec in generated["template"].items():
            if field.startswith("_"):
                continue
            actual = installed["template"].get(field)
            if actual is None:
                raise ValueError(f"{name}.{field}: missing installed input")
            for attr in ("type", "input_types", "list"):
                if spec.get(attr) != actual.get(attr):
                    raise ValueError(f"{name}.{field}: installed input {attr} differs")
        outputs = {o["name"]: o for o in installed.get("outputs", [])}
        for output in generated.get("outputs", []):
            actual = outputs.get(output["name"])
            if actual is None or any(output.get(k) != actual.get(k) for k in ("types", "method")):
                raise ValueError(f"{name}.{output['name']}: installed output differs")
        generated["template"]["code"] = copy.deepcopy(installed["template"]["code"])
        for attr in (
            "name",
            "namespaced_id",
            "extension",
            "extension_version",
            "bundle",
            "metadata",
        ):
            if attr in installed:
                generated[attr] = copy.deepcopy(installed[attr])
        bindings.append(
            {
                "node": node["id"],
                "class": name,
                "category": group,
                "palette_id": key,
                "code_sha256": hashlib.sha256(
                    generated["template"]["code"]["value"].encode()
                ).hexdigest(),
            }
        )
    validate_edges(flow)
    return bindings


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def rows(value, key):
    return value if isinstance(value, list) else value.get(key, value.get("items", []))


def request(client, method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None
