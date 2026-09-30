from lfx.io import HandleInput, DropdownInput, TableInput, Output
from lfx.schema import Data

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.flow_adapters import AdapterContext, normalize_output
from nima_semantica.normalization_contracts import FieldMapping
from nima_semantica.orchestration.langflow.adapter_support import native, configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent


class OutputNormalizer(BaseComponent):
    name = "OutputNormalizer"
    display_name = "NIMA Output Normalizer"
    description = "Retain original output and normalize proposals, never approve or commit them."
    nima_manifest = manifest_for_component("OutputNormalizer")
    inputs = [
        HandleInput(name="input_value", display_name="Existing flow output", input_types=["Message", "Data", "JSON", "DataFrame", "Table"], required=True),
        HandleInput(name="context", display_name="NIMA context", input_types=["Data", "JSON"], required=True),
        DropdownInput(name="preset", display_name="Output preset", options=["text", "record", "graph_extraction"], value="record"),
        TableInput(name="bindings", display_name="Output field bindings", value=[], table_schema=[
            {"name": "source_path", "display_name": "From", "type": "str"},
            {"name": "target_path", "display_name": "To", "type": "str"},
            {"name": "required", "display_name": "Required", "type": "bool"}]),
    ]
    outputs = [
        Output(name="normalized", display_name="NIMA proposal", method="normalized_data", group_outputs=True),
        Output(name="raw", display_name="Raw output and diagnostics", method="result_data", group_outputs=True),
        Output(name="preview", display_name="Preview", method="preview_message", group_outputs=True),
    ]

    def run_sync(self):
        context = AdapterContext.model_validate(native(self.context))
        with configured_store(required=False) as store:
            output = normalize_output(native(self.input_value), context, preset=self.preset, store=store,
                bindings=[FieldMapping.model_validate(item) for item in self.bindings])
        return output.model_dump(mode="json")

    async def normalized_data(self) -> Data:
        return Data(data=(await self.result_data()).data["normalized"])
