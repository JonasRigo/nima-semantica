"""Harness-selected targets; operator permissions stay outside public JSON."""
import json
from lfx.io import StrInput, DropdownInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.substantiation_contracts import SubstantiationRequest
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class SubstantiationFields(BaseComponent):
    name = "SubstantiationFields"
    display_name = "Substantiate Graph Snapshot Inputs"
    description = "Pin a graph revision and optional proposed extraction artifact; select node/edge identities and the findings to revisit. Additional prepared region IDs are optional; exact corpus/project scope and permissions are operator-owned."
    nima_manifest = manifest_for_component("SubstantiationFields")
    inputs = [DropdownInput(name="mode",display_name="Mode",options=["preview","substantiate"],value="preview"),
        StrInput(name="graph_revision_json",display_name="Exact graph revision (JSON)",value="null"),
        StrInput(name="targets_json",display_name="Targets: kind, full ref, finding (JSON list)",value="[]"),
        StrInput(name="artifact_id",display_name="Proposed extraction artifact ID, optional",value=""),
        StrInput(name="source_region_ids_json",display_name="Additional prepared region IDs (JSON list)",value="[]"),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs = [Output(name="request",display_name="Validated substantiation request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>100000:raise ValueError("request exceeds input limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected request object")
        payload=dict(mode=self.mode,graph_revision=json.loads(self.graph_revision_json),targets=json.loads(self.targets_json),
            source_region_ids=json.loads(self.source_region_ids_json),artifact_id=self.artifact_id or None,
            operation_id=self.operation_id or None,run_id=self.run_id or None)
        return SubstantiationRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
