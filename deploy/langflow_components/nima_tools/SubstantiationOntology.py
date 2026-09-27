"""Controller-owned substantiation state ontology, distinct from the output ontology."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.substantiation_state import PROFILE, RULES, POLICY_DIGEST


class SubstantiationOntology(BaseComponent):
    name = "SubstantiationOntology"
    display_name = "Private Substantiation Ontology and Rules"
    description = "Controller-owned coverage, assessments, source reads and unresolved issues. Semantica propagates represented dependencies; private state is never the output research graph."
    nima_manifest = manifest_for_component("SubstantiationOntology")
    inputs = []

    async def run(self):
        return {"policy_digest":POLICY_DIGEST,"ontology":PROFILE.model_dump(mode="json"),
            "rules":[r.model_dump(mode="json") for r in RULES],"publishable":False,
            "substantiation_semantics":"Exact quotations and ontology validity do not certify semantic fidelity."}

