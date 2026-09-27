"""Visible exact ontology selection and strict public JSON normalization."""
from lfx.io import DropdownInput, HandleInput, MessageTextInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.ontology_tools import LoadOntologyRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class LoadOntologyFields(BaseComponent):
    name = "LoadOntologyFields"
    display_name = "Load Ontology Inputs"
    description = "List profiles or select an exact name/version or digest. No latest-version fallback."
    nima_manifest = manifest_for_component("LoadOntologyFields")
    inputs = [
        DropdownInput(name="mode", display_name="Mode", options=["list", "load"], value="list"),
        MessageTextInput(name="profile_name", display_name="Profile name", value=""),
        MessageTextInput(name="version", display_name="Exact version", value=""),
        MessageTextInput(name="digest", display_name="Exact digest", value=""),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False),
    ]
    outputs = [Output(name="request", display_name="Validated selection", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Selection preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 10_000:
                raise ValueError("ontology selection exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("expected a JSON object")
        payload = {"mode": self.mode, "name": self.profile_name or None,
            "version": self.version or None, "digest": self.digest or None}
        payload.update(override)
        return LoadOntologyRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
