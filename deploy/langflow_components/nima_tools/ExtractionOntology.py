"""Controller-owned extraction state ontology, distinct from the output ontology."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.extraction_state import PROFILE, RULES, POLICY_DIGEST


class ExtractionOntology(BaseComponent):
    name = "ExtractionOntology"
    display_name = "Private Extraction Ontology and Rules"
    description = "Controller-owned coverage, candidates, source reads and unresolved issues. Semantica propagates represented dependencies; private state is never the output research graph."
    nima_manifest = manifest_for_component("ExtractionOntology")
    inputs = []

    async def run(self):
        return {"policy_digest":POLICY_DIGEST,"ontology":PROFILE.model_dump(mode="json"),
            "rules":[r.model_dump(mode="json") for r in RULES],"publishable":False,
            "extraction_semantics":"Exact quotations and ontology validity do not certify semantic fidelity."}
