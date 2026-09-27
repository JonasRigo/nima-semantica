"""Public report request; operator authority cannot be supplied in JSON."""
from lfx.io import DropdownInput, StrInput, MultilineInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.research_analysis import SaveAnalysisRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent

class SaveAnalysisFields(BaseComponent):
    name="SaveAnalysisFields"
    display_name="Analysis · Bundle and Exact References"
    description="Caller-authored sections, exact evidence, immutable artifact and receipt references. JSON also accepts target/parent progress bindings. No scientific admission."
    nima_manifest=manifest_for_component("SaveAnalysisFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","save"],value="preview"),
        StrInput(name="operation_id",display_name="Unique save operation",value=""),
        StrInput(name="run_id",display_name="Harness run ID",value=""),
        MultilineInput(name="graph_revision_json",display_name="Pinned graph revision (JSON)",value="{}"),
        MultilineInput(name="bundle_json",display_name="Research analysis bundle (JSON)",value="{}"),
        HandleInput(name="mcp_request",display_name="Request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]
    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>2_000_000:raise ValueError("request size limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected object")
        payload={"mode":self.mode,"operation_id":self.operation_id or None,"run_id":self.run_id or None,
            "graph_revision":strict_json_object(self.graph_revision_json) or None,
            "bundle":strict_json_object(self.bundle_json) or None}
        return SaveAnalysisRequest.model_validate(payload|override).model_dump(mode="json")
    async def request_data(self) -> Data:
        return await self.result_data()
