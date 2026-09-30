"""Inspectable run_calculation_graph boundary for the private calculation graph."""
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


class MathMCPRunCalculationGraph(Component):
    nima_manifest = manifest_for_component('MathMCPRunCalculationGraph')
    display_name = 'Math · Run Calculation Graph'
    description = 'Compile and execute atomic symbolic operations with graph-linked provenance. Session mutation: run sequentially, await its returned revision before the next mutation (including retrieval).'
    name = 'MathMCPRunCalculationGraph'
    icon = "calculator"
    inputs = [
        MessageTextInput(name="request_json", display_name="Typed request JSON", value="{}", tool_mode=True),
        BoolInput(name="execute", display_name="Execute authorized request (operator)", value=False),
    ]
    outputs = [Output(name="response", display_name="Result and receipt", method="run_request")]

    def run_request(self) -> Message:
        operation = 'run_calculation_graph'
        if operation == "operations":
            result = operation_catalogue()
        elif not self.execute:
            result = {"executed": False, "operation": operation,
                "request_schema": TOOLS[operation][0].model_json_schema(),
                "authority": "Private graph only; no mathematical truth certificate."}
        else:
            try:
                request = strict_json_object(self.request_json or "{}")
                TOOLS[operation][0].model_validate(request)
            except (ValidationError, ValueError) as exc:
                return Message(text=json.dumps(invalid_request(operation, exc)))
            # Same typed arguments, service and admission rules as stdio MCP.
            # NIMA_MATH_CONFIG binds scope and capabilities outside model inputs.
            result = invoke(canvas_service(), operation, request)
        return Message(text=json.dumps(result, ensure_ascii=False))
