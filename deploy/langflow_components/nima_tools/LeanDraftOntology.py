"""Controller-owned proof state ontology, distinct from the output ontology."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.lean_draft_state import PROFILE, POLICY_DIGEST
from nima_semantica.math_reasoning import RULES


class LeanDraftOntology(BaseComponent):
    name = "LeanDraftOntology"
    display_name = "Private Formalization Ontology and Rules"
    description = "Controller-owned formal target, environment, exact source revisions, declaration resolutions, verifier checks and correspondence obligations. Semantica propagates represented dependencies; private state is never the output research graph."
    nima_manifest = manifest_for_component("LeanDraftOntology")
    inputs = []

    async def run(self):
        return {"policy_digest":POLICY_DIGEST,"ontology":PROFILE.model_dump(mode="json"),
            "rules":[r.model_dump(mode="json") for r in RULES],"publishable":False,
            "proof_semantics":"Controller-bound exact-evidence handles and ontology validity do not certify semantic fidelity."}
