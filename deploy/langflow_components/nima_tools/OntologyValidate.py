"""Inspectable deterministic preview, deliberately not an authorization token."""
from lfx.io import Output
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.ontology_tools import SaveOntologyRequest, validate_profile
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value


class OntologyValidate(BaseComponent):
    name = "OntologyValidate"
    display_name = "Validate Ontology"
    description = "Check vocabulary, required types, relation endpoints, and reserved predicates. Preview is not permission; save revalidates."
    nima_manifest = manifest_for_component("OntologyValidate")
    outputs = [Output(name="result", display_name="Request and validation", method="result_data", group_outputs=True),
        Output(name="preview", display_name="Validation preview", method="preview_message", group_outputs=True)]

    async def run(self):
        request = SaveOntologyRequest.model_validate(_value(self.payload))
        return {"request": request.model_dump(mode="json"),
            "validation": validate_profile(request).model_dump(mode="json")}
