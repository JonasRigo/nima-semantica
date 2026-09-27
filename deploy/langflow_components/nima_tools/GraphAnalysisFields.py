"""Harness-selected revision and analysis mode; no public authorization knobs."""
from lfx.io import StrInput, DropdownInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.graph_analysis import AnalyzeGraphRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class GraphAnalysisFields(BaseComponent):
    name="GraphAnalysisFields"
    display_name="Analyze Graph · Pinned Inputs"
    description="Pin the authorized graph revision, or select a registered self-contained Deep Extraction candidate at that revision. Strict excludes proposed dependencies; hypothesizing keeps explicit taint. JSON also accepts target/parent progress links."
    nima_manifest=manifest_for_component("GraphAnalysisFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","strict","hypothesizing"],value="preview"),
        StrInput(name="graph_revision_json",display_name="Exact graph revision (JSON)",value="{}"),
        StrInput(name="artifact_id",display_name="Proposed graph artifact ID, optional",value=""),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated analysis request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>60000:raise ValueError("request exceeds input limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected JSON object")
        payload={"mode":self.mode,"operation_id":self.operation_id or None,"run_id":self.run_id or None,
            "artifact_id":self.artifact_id or None,"graph_revision":strict_json_object(self.graph_revision_json) or None}
        return AnalyzeGraphRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
