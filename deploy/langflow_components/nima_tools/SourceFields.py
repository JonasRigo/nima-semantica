"""Visible source bytes, mode, and stable operation identity; no path fetching."""
from lfx.io import DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import strict_json_object
from nima_semantica.source_tools import PrepareSourcesRequest
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class SourceFields(BaseComponent):
    name = "SourceFields"
    display_name = "Source Preparation Inputs"
    description = "Supply text or base64 bytes, never server paths or URLs. Preview is read-only; preparation requires an operation ID."
    nima_manifest = manifest_for_component("SourceFields")
    inputs = [
        DropdownInput(name="mode", display_name="Mode", options=["preview", "prepare_index"], value="preview"),
        DropdownInput(name="index_mode", display_name="Index mode", options=["lexical", "vector"], value="lexical"),
        MultilineInput(name="sources_json", display_name="Sources JSON", value='{"sources":[{"name":"inspection.md","data_base64":"IyBJbnNwZWN0aW9uIGZpeHR1cmUKCkZvciB4ID0gMiwgJHheMiA9IDQkLgo="}]}',
            info="Use base64 for exact multiline/LaTeX bytes: Langflow string fields unescape backslash-n before JSON parsing. Native Data/JSON handles can carry text directly."),
        MessageTextInput(name="operation_id", display_name="Write operation ID", value=""),
        MessageTextInput(name="run_id", display_name="Optional Research Run ID", value=""),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False),
    ]
    outputs = [Output(name="request", display_name="Validated request", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 8_100_000:
                raise ValueError("source request exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("expected a JSON object")
        mode = override.get("mode", self.mode)
        payload = {"mode": mode, "index_mode": self.index_mode, "run_id": self.run_id or None}
        if "sources" not in override:
            if len(self.sources_json) > 8_100_000:
                raise ValueError("sources exceed input limit")
            payload.update(strict_json_object(self.sources_json))
        if mode == "prepare_index":
            payload["operation_id"] = self.operation_id or None
        payload.update(override)
        return PrepareSourcesRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
