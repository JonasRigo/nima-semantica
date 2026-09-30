"""Visible public fields for one harness-selected claim or proof step."""
from lfx.io import StrInput, DropdownInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.counterexample_contracts import CounterexampleRequest
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import RequestFields as BaseComponent


class CounterexampleFields(BaseComponent):
    name = "CounterexampleFields"
    display_name = "Counterexample Search Inputs"
    description = "Select a claim, assumptions, domain and quantifier. JSON also accepts an exact encoding, target/parent references, graph revision and source regions."
    nima_manifest = manifest_for_component("CounterexampleFields")
    inputs = [DropdownInput(name="mode", display_name="Mode", options=["preview","integer","agent"], value="preview"),
        StrInput(name="statement", display_name="Exact claim or proof step", value="For every integer x, x squared equals x."),
        StrInput(name="domain", display_name="Domain", value="integers"),
        DropdownInput(name="quantifier", display_name="Quantifier", options=["forall","exists","mixed"], value="forall"),
        StrInput(name="assumptions_json", display_name="Assumptions as JSON list", value="[]"),
        StrInput(name="encoding_json", display_name="Integer encoding JSON, optional", value="{}", advanced=True),
        StrInput(name="operation_id", display_name="Unique attempt ID", value=""),
        StrInput(name="run_id", display_name="Harness run ID, optional", value=""),
        HandleInput(name="mcp_request", display_name="Optional request JSON", input_types=["Message","Data","JSON"], required=False)]
    outputs = [Output(name="request",display_name="Validated search request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        import json
        override = native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>60000:raise ValueError("request exceeds input limit")
            override = strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected request object")
        payload = {k:getattr(self,k) for k in ("mode","statement","domain","quantifier")}
        payload.update(operation_id=self.operation_id or None,run_id=self.run_id or None,
            assumptions=json.loads(self.assumptions_json),
            encoding=strict_json_object(self.encoding_json) if self.encoding_json.strip() not in ("","{}") else None)
        return CounterexampleRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
