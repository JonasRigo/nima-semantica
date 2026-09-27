"""Visible public retrieval request; authority and providers are operator-owned."""
from lfx.io import DropdownInput, IntInput, MessageTextInput, StrInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.research_retrieval import ResearchRetrievalRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class ResearchRetrievalFields(BaseComponent):
    name = "ResearchRetrievalFields"
    display_name = "Research Context Inputs"
    description = "Query, explicit lexical/vector/hybrid mode, current projection selection and context limits. Model-backed modes require an attempt ID."
    nima_manifest = manifest_for_component("ResearchRetrievalFields")
    inputs = [MessageTextInput(name="query", display_name="Research query", value="x"),
        DropdownInput(name="mode", display_name="Retrieval mode", options=["lexical", "vector", "hybrid"], value="lexical"),
        StrInput(name="projection_id", display_name="Exact projection ID (optional)", value=""),
        StrInput(name="expected_store_revision", display_name="Expected store revision (optional)", value=""),
        StrInput(name="operation_id", display_name="Model attempt ID", value=""),
        StrInput(name="run_id", display_name="Research run ID (optional)", value=""),
        IntInput(name="limit", display_name="Seed limit", value=8),
        IntInput(name="max_hops", display_name="Graph hops", value=2),
        IntInput(name="max_nodes", display_name="Visited node limit", value=64, advanced=True),
        IntInput(name="max_edges", display_name="Examined edge limit", value=128, advanced=True),
        IntInput(name="max_neighbors", display_name="Neighbors per node", value=32, advanced=True),
        IntInput(name="max_results", display_name="Passage limit", value=16),
        IntInput(name="max_chars", display_name="Exact passage character limit", value=30000),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message", "Data", "JSON"], required=False)]
    outputs = [Output(name="request", display_name="Validated request", method="request_data", group_outputs=True),
        Output(name="preview", display_name="Request preview", method="preview_message", group_outputs=True)]

    async def run(self):
        override = native(getattr(self, "mcp_request", None))
        if override is None:
            override = {}
        if isinstance(override, str):
            if len(override) > 30000:
                raise ValueError("request exceeds transport limit")
            override = strict_json_object(override) if override.strip() else {}
        if not isinstance(override, dict):
            raise ValueError("expected a JSON object")
        payload = {name: getattr(self, name) for name in ResearchRetrievalRequest.model_fields}
        for name in ("projection_id", "expected_store_revision", "operation_id", "run_id"):
            payload[name] = payload[name] or None
        payload.update(override)
        return ResearchRetrievalRequest.model_validate(payload).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
