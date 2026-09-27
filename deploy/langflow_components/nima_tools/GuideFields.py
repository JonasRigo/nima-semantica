"""Visible named-field and optional JSON transport for Tool Guide."""
import json

from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema import Data, Message

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.tool_guide import ToolGuideRequest
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class GuideFields(BaseComponent):
    name = "GuideFields"
    display_name = "Tool Guide Inputs"
    description = "Empty tool lists the catalog. Optional JSON overrides only the named tool field."
    nima_manifest = manifest_for_component("GuideFields")
    inputs = [
        MessageTextInput(name="tool", display_name="Tool name or ID", value="", info="Empty lists all tools; otherwise use an exact name or ID."),
        HandleInput(name="mcp_request", display_name="Optional MCP JSON", input_types=["Message", "Data", "JSON"], required=False),
    ]
    outputs = [
        Output(name="request", display_name="Validated request", method="request_data", group_outputs=True),
        Output(name="message", display_name="Request preview", method="preview_message", group_outputs=True),
    ]

    async def run(self):
        supplied = getattr(self, "mcp_request", None)
        supplied = supplied.text if isinstance(supplied, Message) else supplied.data if isinstance(supplied, Data) else supplied
        if supplied is None:
            supplied = {}
        if isinstance(supplied, str):
            if len(supplied) > 4096:
                raise ValueError("Tool Guide JSON exceeds the input limit")
            def unique_fields(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("Duplicate Tool Guide field")
                    value[key] = item
                return value
            try:
                supplied = json.loads(supplied, object_pairs_hook=unique_fields) if supplied.strip() else {}
            except (ValueError, TypeError):
                raise ValueError("Tool Guide expects a JSON object containing only the optional tool field") from None
        if not isinstance(supplied, dict):
            raise ValueError("Tool Guide expects a JSON object")
        # Validate overrides before merging so extra fields cannot be discarded.
        override = ToolGuideRequest.model_validate(supplied)
        value = override.tool if "tool" in supplied else self.tool
        return ToolGuideRequest(tool=value).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
