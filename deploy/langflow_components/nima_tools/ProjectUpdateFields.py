"""Public project update data; authorization is never accepted from tool JSON."""
import json
from lfx.io import StrInput,DropdownInput,HandleInput,Output
from lfx.schema import Data
from nima_semantica.project_update import UpdateProjectRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class ProjectUpdateFields(BaseComponent):
    name="ProjectUpdateFields"
    display_name="Update Project Graph · Request"
    description="Preview, prepare an exact delta for approval, commit or repair a projection. Select an explicit delta, registered extraction artifact or progress proposal batch. Public JSON cannot grant approval or write permission."
    nima_manifest=manifest_for_component("ProjectUpdateFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","prepare","commit","rebuild_projection"],value="preview"),
        StrInput(name="graph_revision_json",display_name="Expected graph revision (JSON)",value="null"),
        StrInput(name="delta_json",display_name="Exact OKF delta (JSON)",value="null"),
        StrInput(name="artifact_id",display_name="Registered graph candidate artifact ID",value=""),
        StrInput(name="progress_proposal_ids_json",display_name="Progress proposal IDs (JSON list)",value="[]"),
        StrInput(name="operation_id",display_name="Unique operation ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated update request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>4000000:raise ValueError("request exceeds bound")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected JSON object")
        payload=dict(mode=self.mode,graph_revision=json.loads(self.graph_revision_json),delta=json.loads(self.delta_json),
            artifact_id=self.artifact_id or None,progress_proposal_ids=json.loads(self.progress_proposal_ids_json),
            operation_id=self.operation_id or None,run_id=self.run_id or None)
        return UpdateProjectRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
