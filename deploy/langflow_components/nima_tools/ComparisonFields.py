"""Harness-selected objects, exact comparison target and immutable criteria."""
import json
from lfx.io import StrInput,DropdownInput,HandleInput,Output
from lfx.schema import Data
from nima_semantica.comparison_contracts import CompareObjectsRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import RequestFields as BaseComponent


class ComparisonFields(BaseComponent):
    name="ComparisonFields"
    display_name="Compare Research Objects · Scope and Criteria"
    description="Compare two to six pinned graph objects, hypothesis proposals or typed mathematical/proof objects under explicit criteria. Proof modes require an exact common graph target. Public JSON cannot change operator permissions."
    nima_manifest=manifest_for_component("ComparisonFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","mathematical_objects","hypotheses","proof_strategies","proof_attempts"],value="preview"),
        StrInput(name="objective",display_name="Comparison objective",value="Compare the selected objects without choosing a winner or certifying equivalence."),
        StrInput(name="graph_revision_json",display_name="Exact graph revision (JSON)",value="null"),
        StrInput(name="target_json",display_name="Exact common proof target (JSON)",value="null"),
        StrInput(name="objects_json",display_name="Objects: IDs and typed bindings (JSON list)",value="[]"),
        StrInput(name="criteria_json",display_name="Criteria: criterion_id and description (JSON list)",value="[]"),
        StrInput(name="source_region_ids_json",display_name="Prepared region IDs (JSON list)",value="[]"),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated comparison request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>150000:raise ValueError("request exceeds input limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected request object")
        payload=dict(mode=self.mode,objective=self.objective,graph_revision=json.loads(self.graph_revision_json),
            target=json.loads(self.target_json),objects=json.loads(self.objects_json),criteria=json.loads(self.criteria_json),
            source_region_ids=json.loads(self.source_region_ids_json),operation_id=self.operation_id or None,run_id=self.run_id or None)
        return CompareObjectsRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
