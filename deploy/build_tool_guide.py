"""Refresh the maintained Tool Guide canvas in memory; emit JSON on stdout."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CANVAS = ROOT / "examples/langflow_replacement/tool_guide.json"
COMPONENTS = ROOT / "deploy/langflow_components/nima_tools"


def load_component(name):
    spec = importlib.util.spec_from_file_location("nima_tool_guide_" + name, COMPONENTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, name)


def build():
    flow = json.loads(CANVAS.read_text())
    flow.pop("id", None)
    flow["name"] = "Tool Guide"
    flow["description"] = "Deterministic NIMA catalog lookup. Empty tool lists all 24 maintained capabilities; exact name or ID selects one. No model, dispatch, storage, or automatic tool selection. Optional MCP JSON overrides only tool. Canvas visually approved."
    flow["endpoint_name"] = "nima-tool-guide"
    snapshots = {}
    for index, saved in enumerate(flow["data"]["nodes"]):
        name = saved["data"]["type"]
        if name in ("GuideFields", "ToolGuide"):
            component = load_component(name)()
            node = component.to_frontend_node()
            node.update(id=saved["id"], type="genericNode", position=saved["position"])
            node["data"].update(id=saved["id"], type=name)
            source = (COMPONENTS / (name + ".py")).read_text()
            node["data"]["node"]["template"]["code"]["value"] = source
            metadata = {"nima_component_id": component.nima_manifest.component_id,
                "contract_version": component.nima_manifest.version,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
            node["data"]["node"]["metadata"] = metadata
            snapshots[name] = metadata
            flow["data"]["nodes"][index] = node
        else:
            template = saved["data"]["node"]["template"]
            if name in ("ChatInput", "ChatOutput"):
                template["should_store_message"]["value"] = False
                template["session_id"]["value"] = ""
            if name == "ChatInput":
                template["input_value"]["value"] = "{}"
                template["files"]["value"] = []
            if name == "TextInput":
                template["input_value"]["value"] = ""
    nodes = {n["id"]: n for n in flow["data"]["nodes"]}
    # Retain topology and locations, refresh handle types against current ports.
    for edge in flow["data"]["edges"]:
        source, target = nodes[edge["source"]], nodes[edge["target"]]
        sh, th = edge["data"]["sourceHandle"], edge["data"]["targetHandle"]
        port = next(p for p in source["data"]["node"]["outputs"] if p["name"] == sh["name"])
        field = target["data"]["node"]["template"][th["fieldName"]]
        sh["output_types"] = port["types"]
        th.update(inputTypes=field.get("input_types", []), type=field["type"])
        edge["sourceHandle"] = json.dumps(sh).replace('"', "œ")
        edge["targetHandle"] = json.dumps(th).replace('"', "œ")
        edge["id"] = f"nima-{edge['source']}-{sh['name']}-{edge['target']}-{th['fieldName']}"
    flow["nima_tool_manifest"] = {"tool_id": "tool_guide", "contract_version": "1",
        "visual_approval": "approved", "publication": "not_published_by_builder",
        "side_effect_class": "pure_transform", "receipt_mode": "none", "components": snapshots}
    return flow


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
