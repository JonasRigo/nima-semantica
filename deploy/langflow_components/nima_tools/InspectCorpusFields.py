"""Visible strict pagination inputs; scope remains operator-owned."""
from lfx.io import IntInput, StrInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.corpus_inspection import InspectCorpusRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class InspectCorpusFields(BaseComponent):
    name = "InspectCorpusFields"
    display_name = "Inspect Corpus Inputs"
    description = "Bounded source page, optionally pinned to the previous store revision. Public JSON cannot set scope."
    nima_manifest = manifest_for_component("InspectCorpusFields")
    inputs = [IntInput(name="offset", display_name="Offset", value=0),
        IntInput(name="limit", display_name="Page size (1–100)", value=25),
        StrInput(name="expected_store_revision", display_name="Expected store revision", value=""),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False)]
    outputs = [Output(name="request", display_name="Validated page", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 10_000:
                raise ValueError("request exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("expected a JSON object")
        payload = dict(offset=self.offset, limit=self.limit, expected_store_revision=self.expected_store_revision or None)
        payload.update(override)
        return InspectCorpusRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
