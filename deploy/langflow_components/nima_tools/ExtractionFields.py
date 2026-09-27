"""Public extraction scope; operator permissions are deliberately absent."""
import json
from lfx.io import StrInput, DropdownInput, HandleInput, Output
from lfx.schema import Data
from nima_semantica.extraction_contracts import DeepExtractionRequest
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import strict_json_object
from nima_semantica.orchestration.langflow.adapter_support import native
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class ExtractionFields(BaseComponent):
    name = "ExtractionFields"
    display_name = "Deep Extraction Inputs"
    description = "Select prepared regions or a prepared document, extraction question and saved ontology. JSON also accepts pinned graph revision and target/parent progress references. No automatic ingestion or graph admission."
    nima_manifest = manifest_for_component("ExtractionFields")
    inputs = [DropdownInput(name="mode",display_name="Mode",options=["preview","regional","document","document_to_proposal"],value="preview"),
        StrInput(name="question",display_name="Extraction question",value="Extract source-attributed claims, definitions and their explicit relations; retain uncertainty."),
        StrInput(name="source_region_ids_json",display_name="Prepared region IDs (JSON list)",value="[]"),
        StrInput(name="source_id",display_name="Prepared document source ID",value=""),
        StrInput(name="ontology_profile",display_name="Saved ontology name/version or digest",value="literature_evidence@1.0.0"),
        StrInput(name="operation_id",display_name="Unique attempt ID",value=""),
        StrInput(name="run_id",display_name="Harness run ID, optional",value=""),
        HandleInput(name="mcp_request",display_name="Optional request JSON",input_types=["Message","Data","JSON"],required=False)]
    outputs = [Output(name="request",display_name="Validated extraction request",method="request_data",group_outputs=True),
        Output(name="preview",display_name="Request preview",method="preview_message",group_outputs=True)]

    async def run(self):
        override=native(getattr(self,"mcp_request",None))
        if isinstance(override,str):
            if len(override)>60000:raise ValueError("request exceeds input limit")
            override=strict_json_object(override) if override.strip() else {}
        if override is None:override={}
        if not isinstance(override,dict):raise ValueError("expected request object")
        payload={k:getattr(self,k) for k in ("mode","question","ontology_profile")}
        payload.update(source_id=self.source_id or None,source_region_ids=json.loads(self.source_region_ids_json),
            operation_id=self.operation_id or None,run_id=self.run_id or None)
        return DeepExtractionRequest.model_validate(payload|override).model_dump(mode="json")

    async def request_data(self) -> Data:
        return await self.result_data()
