"""Inspectable named Research Run inputs; public JSON cannot set authority."""
from lfx.io import DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema import Data

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import strict_json_object
from nima_semantica.research_run_tool import ResearchRunRequest
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import RequestFields as BaseComponent


class ResearchRunFields(BaseComponent):
    name = "ResearchRunFields"
    display_name = "Research Run Inputs"
    description = "Choose inspect, create, or transition. JSON overrides request fields, never operator scope or permissions."
    nima_manifest = manifest_for_component("ResearchRunFields")
    inputs = [
        DropdownInput(name="mode", display_name="Mode", options=["inspect", "create", "transition"], value="inspect"),
        MessageTextInput(name="run_id", display_name="Run ID", value="inspection-run"),
        MessageTextInput(name="operation_id", display_name="Write operation ID", value="", info="Required for writes. Reuse only for an identical retry."),
        MultilineInput(name="creation_json", display_name="Creation fields (JSON)", value='{"objective":"Inspect the research workflow","skill_id":"manual-review","skill_revision":"1"}'),
        MultilineInput(name="transition_json", display_name="Transition fields (JSON)", value='{"transition_id":"pause-1","from_state":"running","to_state":"paused","from_run_revision":0,"reason":"Await human review"}'),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False),
    ]
    outputs = [
        Output(name="request", display_name="Validated request", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True),
    ]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 50_000:
                raise ValueError("Research Run request exceeds the input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("Research Run expects a JSON object")
        mode = override.get("mode", self.mode)
        payload = {"mode": mode, "run_id": self.run_id}
        if mode != "inspect":
            payload["operation_id"] = self.operation_id or None
        if mode == "create" and "creation" not in override:
            payload["creation"] = strict_json_object(self.creation_json)
        if mode == "transition" and "transition" not in override:
            payload["transition"] = strict_json_object(self.transition_json)
        payload.update(override)
        return ResearchRunRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
