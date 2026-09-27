"""Visible exact selection and bounds for a non-mutating evidence read."""
from lfx.io import DropdownInput, IntInput, StrInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.evidence_reader import ReadEvidenceRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class EvidenceReadFields(BaseComponent):
    name = "EvidenceReadFields"
    display_name = "Read Evidence Inputs"
    description = "Select a published artifact, exact source region, or a complete evidence reference. Public JSON never chooses authorization scope."
    nima_manifest = manifest_for_component("EvidenceReadFields")
    inputs = [DropdownInput(name="target_kind", display_name="Target type", options=["artifact", "region", "reference"], value="artifact"),
        StrInput(name="target_id", display_name="Artifact or region ID", value=""),
        StrInput(name="reference_json", display_name="Evidence reference JSON", value="{}", advanced=True),
        DropdownInput(name="encoding", display_name="Artifact encoding", options=["utf-8","base64"], value="utf-8"),
        DropdownInput(name="render", display_name="Optional safe rendering", options=["none","escaped_html"], value="none"),
        IntInput(name="max_bytes", display_name="Maximum returned bytes", value=100000),
        IntInput(name="max_links", display_name="Direct provenance link limit", value=32),
        StrInput(name="expected_store_revision", display_name="Expected store revision", value="", advanced=True),
        StrInput(name="expected_source_revision", display_name="Expected source revision", value="", advanced=True),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message","Data","JSON"], required=False)]
    outputs = [Output(name="request", display_name="Validated evidence selection", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self,"mcp_request",None))
        if override is None:
            override = {}
        if isinstance(override,str):
            if len(override) > 120000:
                raise ValueError("request exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override,dict):
            raise ValueError("expected a JSON object")
        payload = {name:getattr(self,name) for name in ("encoding","render","max_bytes","max_links")}
        payload.update(expected_store_revision=self.expected_store_revision or None,
            expected_source_revision=self.expected_source_revision or None)
        if not any(key in override for key in ("artifact_id","region_id","reference")):
            if self.target_kind == "reference":
                if len(self.reference_json) > 100000:
                    raise ValueError("reference exceeds input limit")
                payload["reference"] = strict_json_object(self.reference_json)
            else:
                payload[self.target_kind + "_id"] = self.target_id or None
        payload.update(override)
        return ReadEvidenceRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
