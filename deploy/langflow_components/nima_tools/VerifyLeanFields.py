"""Exact submitted Lean modules and theorem names; never generated or repaired."""
import json
from lfx.io import StrInput, MultilineInput, DropdownInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.verify_lean_tool import VerifyLeanRequest
from nima_semantica.providers import strict_json_object
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import RequestFields as BaseComponent


class VerifyLeanFields(BaseComponent):
    name="VerifyLeanFields"
    display_name="Verify Lean · Exact Submission"
    description="Exact source modules, compilation order and target theorem names. JSON additionally accepts expected alpha-normalized type fingerprints, run/proof references, source regions and a graph revision. No source transformation."
    nima_manifest=manifest_for_component("VerifyLeanFields")
    inputs=[DropdownInput(name="mode",display_name="Mode",options=["preview","verify"],value="preview"),
        MultilineInput(name="sources_json",display_name="Module sources (JSON)",value=json.dumps({"Submission":"theorem target : True := True.intro"})),
        StrInput(name="targets_json",display_name="Theorem names (JSON)",value='["target"]'),
        StrInput(name="imports_json",display_name="Submitted modules to inspect (JSON)",value='["Submission"]'),
        StrInput(name="module_order_json",display_name="Compilation order (JSON, optional)",value="[]"),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs=[Output(name="request",display_name="Validated exact submission",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>16*1024*1024:raise ValueError("request size limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected JSON object")
        payload={"mode":self.mode,"operation_id":self.operation_id or None,"run_id":self.run_id or None,
            "sources":strict_json_object(self.sources_json),"targets":json.loads(self.targets_json),
            "imports":json.loads(self.imports_json),"module_order":json.loads(self.module_order_json)}
        return VerifyLeanRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
