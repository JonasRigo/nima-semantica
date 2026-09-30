"""Visible editable profile and strict public save/validation request."""
from lfx.io import DropdownInput, HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.ontology_tools import SaveOntologyRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import RequestFields as BaseComponent


class SaveOntologyFields(BaseComponent):
    name = "SaveOntologyFields"
    display_name = "Save Ontology Inputs"
    description = "Edit a new profile. Validate is the default; saving requires a distinct operation ID and operator permission."
    nima_manifest = manifest_for_component("SaveOntologyFields")
    inputs = [
        DropdownInput(name="mode", display_name="Mode", options=["validate", "save"], value="validate"),
        MultilineInput(name="profile_json", display_name="Editable profile JSON", value='{"name":"inspection_ontology","version":"1.0.0","node_types":[{"name":"claim","description":"A statement requiring evidence"}],"relation_types":[]}'),
        MessageTextInput(name="operation_id", display_name="Save operation ID", value=""),
        MessageTextInput(name="run_id", display_name="Optional research run ID", value=""),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False),
    ]
    outputs = [Output(name="request", display_name="Normalized request", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 210_000:
                raise ValueError("ontology request exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("expected a JSON object")
        mode = override.get("mode", self.mode)
        payload = {"mode": mode, "run_id": self.run_id or None}
        if "profile" not in override:
            if len(self.profile_json) > 200_000:
                raise ValueError("ontology profile exceeds input limit")
            payload["profile"] = strict_json_object(self.profile_json)
        if mode == "save":
            payload["operation_id"] = self.operation_id or None
        payload.update(override)
        return SaveOntologyRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
