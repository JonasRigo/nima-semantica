"""Generate one inspectable Langflow canvas per typed calculation MCP operation.

Run inside the installed Langflow environment with --write. Generated component
source is embedded in each canvas; no agent or provider component is present.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from nima_semantica.math_mcp_contracts import TOOLS, operation_catalogue

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "deploy/langflow_components/nima_tools"
OUTPUT = ROOT / "examples/langflow_replacement/math_mcp"


def component_source(operation, class_name, description):
    return f'''"""Inspectable {operation} boundary for the private calculation graph."""
import json
from lfx.custom.custom_component.component import Component
from pydantic import ValidationError
from lfx.io import BoolInput, MessageTextInput, Output
from lfx.schema import Message
from nima_semantica.math_mcp_contracts import TOOLS, invoke, operation_catalogue
from nima_semantica.math_mcp import canvas_service
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.request_diagnostics import invalid_request
from nima_semantica.providers import strict_json_object


class {class_name}(Component):
    nima_manifest = manifest_for_component({class_name!r})
    display_name = {("Math · " + operation.replace("_", " ").title())!r}
    description = {description!r}
    name = {class_name!r}
    icon = "calculator"
    inputs = [
        MessageTextInput(name="request_json", display_name="Typed request JSON", value="{{}}", tool_mode=True),
        BoolInput(name="execute", display_name="Execute authorized request (operator)", value=False),
    ]
    outputs = [Output(name="response", display_name="Result and receipt", method="run_request")]

    def run_request(self) -> Message:
        operation = {operation!r}
        if operation == "operations":
            result = operation_catalogue()
        elif not self.execute:
            result = {{"executed": False, "operation": operation,
                "request_schema": TOOLS[operation][0].model_json_schema(),
                "authority": "Private graph only; no mathematical truth certificate."}}
        else:
            try:
                request = strict_json_object(self.request_json or "{{}}")
                TOOLS[operation][0].model_validate(request)
            except (ValidationError, ValueError) as exc:
                return Message(text=json.dumps(invalid_request(operation, exc)))
            # Same typed arguments, service and admission rules as stdio MCP.
            # NIMA_MATH_CONFIG binds scope and capabilities outside model inputs.
            result = invoke(canvas_service(), operation, request)
        return Message(text=json.dumps(result, ensure_ascii=False))
'''


def edge(source, target, output, field):
    port = next(p for p in source["data"]["node"]["outputs"] if p["name"] == output)
    spec = target["data"]["node"]["template"][field]
    source["data"]["selected_output"] = output
    sh = {"dataType": source["data"]["type"], "id": source["id"], "name": output, "output_types": port["types"]}
    th = {"fieldName": field, "id": target["id"], "inputTypes": spec.get("input_types", []), "type": spec["type"]}
    return {"id": source["id"] + "-" + target["id"], "source": source["id"], "target": target["id"],
        "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"),
        "data": {"sourceHandle": sh, "targetHandle": th}}


def build(operation, description):
    name = "MathMCP" + "".join(x.title() for x in operation.split("_"))
    source = component_source(operation, name, description)
    with tempfile.TemporaryDirectory(prefix="nima-canvas-build-") as directory:
        path = Path(directory) / (name + ".py")
        path.write_text(source)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        component = getattr(module, name)()
        node = component.to_frontend_node()
    node.update(id=name + "-nima", type="genericNode", position={"x": 450, "y": 0})
    node["data"].update(id=node["id"], type=name)
    node["data"]["node"]["template"]["code"]["value"] = source
    base = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    chat_in = copy.deepcopy(next(n for n in base["data"]["nodes"] if n["data"]["type"] == "ChatInput"))
    chat_out = copy.deepcopy(next(n for n in base["data"]["nodes"] if n["data"]["type"] == "ChatOutput"))
    for chat, x in ((chat_in, 0), (chat_out, 900)):
        chat["position"] = {"x": x, "y": 0}
        chat["data"]["node"]["template"]["should_store_message"]["value"] = False
        chat["data"]["node"]["template"]["session_id"]["value"] = ""
    chat_in["data"]["node"]["template"]["input_value"]["value"] = "{}"
    chat_in["data"]["node"]["template"]["files"]["value"] = []
    base.pop("id", None)
    base.update(name="Calculate Mathematics · " + operation.replace("_", " ").title(),
        description=description, endpoint_name="nima-math-" + operation.replace("_", "-"))
    base["data"]["nodes"] = [chat_in, node, chat_out]
    base["data"]["edges"] = [edge(chat_in, node, "message", "request_json"), edge(node, chat_out, "response", "input_value")]
    base["nima_tool_manifest"] = {"tool_id": "nima_math_" + operation, "contract_version": "math-session-v1",
        "visual_approval": "pending", "publication": "candidate", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "native_sources_sha256": {file: hashlib.sha256((ROOT / "src/nima_semantica" / file).read_bytes()).hexdigest()
            for file in ("calculation_values.py", "math_mcp_contracts.py", "math_mcp.py", "math_session_service.py", "math_interface_helpers.py", "math_repair_feedback.py", "math_evidence_handoff.py", "math_calculation_assistance.py", "math_response_views.py", "math_graph_program.py", "math_single_graph_state.py")}}
    return name, source, base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    entries = {op: description for op, (_, description) in TOOLS.items()}
    entries["operations"] = "Inspect the exact atomic operation catalogue and examples."
    if args.write:
        output = args.output_root / "examples/langflow_replacement/math_mcp"
        components = args.output_root / "deploy/langflow_components/nima_tools"
        output.mkdir(parents=True, exist_ok=True)
        components.mkdir(parents=True, exist_ok=True)
    for operation, description in entries.items():
        name, source, flow = build(operation, description)
        if args.write:
            (components / (name + ".py")).write_text(source)
            (output / (operation + ".json")).write_text(json.dumps(flow, indent=2, ensure_ascii=False) + "\n")
        else:
            print(flow["name"])


if __name__ == "__main__":
    main()
