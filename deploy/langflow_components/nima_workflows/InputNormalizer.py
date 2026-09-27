from uuid import uuid4

from lfx.io import HandleInput, StrInput, TableInput, Output
from lfx.schema import Data, DataFrame, Message

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.flow_adapters import input_context
from nima_semantica.normalization_contracts import FieldMapping
from nima_semantica.orchestration.langflow.adapter_support import native, readable, configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent


class InputNormalizer(BaseComponent):
    name = "InputNormalizer"
    display_name = "NIMA Input Normalizer"
    description = "Adapt existing input and carry NIMA scope separately without rewriting the flow."
    nima_manifest = manifest_for_component("InputNormalizer")
    inputs = [
        HandleInput(name="input_value", display_name="Existing input", input_types=["Message", "Data", "JSON", "DataFrame", "Table"], required=True),
        StrInput(name="corpus_id", display_name="Corpus", required=True),
        StrInput(name="project_id", display_name="Project"),
        StrInput(name="ontology_profile", display_name="Ontology profile", advanced=True),
        StrInput(name="adapter_id", display_name="Adapter identity", value="user-flow", advanced=True),
        StrInput(name="request_id", display_name="Request identity (blank = new)", advanced=True),
        HandleInput(name="source_bindings", display_name="Source bindings", input_types=["Data", "JSON"], advanced=True),
        TableInput(name="bindings", display_name="Input field bindings", value=[], table_schema=[
            {"name": "source_path", "display_name": "From", "type": "str"},
            {"name": "target_path", "display_name": "To", "type": "str"},
            {"name": "required", "display_name": "Required", "type": "bool"}]),
    ]
    outputs = [
        Output(name="data", display_name="Flow JSON", method="flow_data", group_outputs=True),
        Output(name="text", display_name="Flow text", method="flow_text", group_outputs=True),
        Output(name="rows", display_name="Flow table", method="flow_rows", group_outputs=True),
        Output(name="context", display_name="NIMA context", method="context_data", group_outputs=True),
    ]

    async def run(self):
        source = native(self.source_bindings) or {}
        with configured_store(required=False) as store:
            revision = store.graph_revision(self.corpus_id, self.project_id or None) if store else None
        payload, context = input_context(native(self.input_value), corpus_id=self.corpus_id,
            project_id=self.project_id or None, request_id=self.request_id or uuid4().hex,
            adapter_id=self.adapter_id, ontology_profile=self.ontology_profile or None,
            graph_revision=revision, source_region_ids=source.get("source_region_ids", []),
            chunk_bindings=source.get("chunk_bindings", {}), registry_revision=source.get("registry_revision"),
            relation_bindings=source.get("relation_bindings", {}),
            bindings=[FieldMapping.model_validate(item) for item in self.bindings])
        return {"payload": payload, "context": context.model_dump(mode="json")}

    async def flow_data(self) -> Data:
        return Data(data=(await self.result_data()).data["payload"])

    async def flow_text(self) -> Message:
        return Message(text=readable((await self.flow_data()).data))

    async def flow_rows(self) -> DataFrame:
        payload = (await self.flow_data()).data
        return DataFrame(payload["rows"] if "rows" in payload else [payload])

    async def context_data(self) -> Data:
        return Data(data=(await self.result_data()).data["context"])
