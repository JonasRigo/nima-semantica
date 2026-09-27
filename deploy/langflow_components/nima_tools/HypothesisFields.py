"""Harness-selected question, priors and immutable hypothesis constraints."""
import json
from lfx.io import StrInput,DropdownInput,HandleInput,Output
from lfx.schema import Data
from nima_semantica.hypothesis_contracts import GenerateHypothesesRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class HypothesisFields(BaseComponent):
    name="HypothesisFields"
    display_name="Hypothesis Generation · Scope and Constraints"
    description="Generate or restate hypotheses at an exact graph revision. Select prior hypothesis IDs, prepared regions and graph targets; JSON also accepts immutable required assumptions, named constraints and target/parent progress links. No operator permissions in public JSON."
    nima_manifest=manifest_for_component("HypothesisFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","generate","restate"],value="preview"),
        StrInput(name="objective",display_name="Question or research objective",value="Propose grounded, testable alternatives and retain uncertainty."),
        StrInput(name="graph_revision_json",display_name="Exact graph revision (JSON)",value="null"),
        StrInput(name="source_region_ids_json",display_name="Prepared region IDs (JSON list)",value="[]"),
        StrInput(name="prior_hypothesis_ids_json",display_name="Prior hypothesis IDs for restatement (JSON list)",value="[]"),
        StrInput(name="graph_targets_json",display_name="Selected graph objects: kind and full ref (JSON list)",value="[]"),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated hypothesis request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>150000:raise ValueError("request exceeds input limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected request object")
        payload=dict(mode=self.mode,objective=self.objective,graph_revision=json.loads(self.graph_revision_json),
            source_region_ids=json.loads(self.source_region_ids_json),prior_hypothesis_ids=json.loads(self.prior_hypothesis_ids_json),
            graph_targets=json.loads(self.graph_targets_json),operation_id=self.operation_id or None,run_id=self.run_id or None)
        return GenerateHypothesesRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
