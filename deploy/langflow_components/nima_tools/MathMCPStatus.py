"""Inspectable status boundary for the private calculation graph."""
import json
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, MessageTextInput, Output
from lfx.schema import Message
from nima_semantica.math_mcp_contracts import TOOLS, invoke, operation_catalogue
from nima_semantica.math_mcp import canvas_service
from nima_semantica.component_contracts import manifest_for_component


class MathMCPStatus(Component):
    nima_manifest = manifest_for_component('MathMCPStatus')
    display_name = 'Math · Status'
    description = 'Inspect lifecycle, task binding and current revision. Read-only; may run concurrently.'
    name = 'MathMCPStatus'
    icon = "calculator"
    inputs = [
        MessageTextInput(name="request_json", display_name="Typed request JSON", value="{}", tool_mode=True),
        BoolInput(name="execute", display_name="Execute authorized request (operator)", value=False),
    ]
    outputs = [Output(name="response", display_name="Result and receipt", method="run_request")]

    def run_request(self) -> Message:
        operation = 'status'
        if operation == "operations":
            result = operation_catalogue()
        elif not self.execute:
            result = {"executed": False, "operation": operation,
                "request_schema": TOOLS[operation][0].model_json_schema(),
                "authority": "Private graph only; no mathematical truth certificate."}
        else:
            request = json.loads(self.request_json or "{}")
            # Same typed arguments, service and admission rules as stdio MCP.
            # NIMA_MATH_CONFIG binds scope and capabilities outside model inputs.
            result = invoke(canvas_service(), operation, request)
        return Message(text=json.dumps(result, ensure_ascii=False))
