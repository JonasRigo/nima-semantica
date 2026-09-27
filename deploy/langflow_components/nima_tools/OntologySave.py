"""Inspectable explicit immutable-write boundary; preview cannot authorize a save."""
from lfx.io import BoolInput, StrInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.ontology_tools import OntologyContext, SaveOntologyRequest, save_ontology
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class OntologySave(BaseComponent):
    name = "OntologySave"
    display_name = "Save Immutable Ontology"
    description = "Revalidate, check operator permission, then atomically publish artifact, registry binding, and attempt receipt. No graph mutation."
    nima_manifest = manifest_for_component("OntologySave")
    inputs = [handle("payload", "Request and validation"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research",
            info="Empty authorizes corpus scope only; never set from agent JSON."),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        BoolInput(name="allow_writes", display_name="Allow ontology writes (operator)", value=False),
    ]

    async def run(self):
        payload = _value(self.payload)
        request = SaveOntologyRequest.model_validate(payload["request"])
        context = OntologyContext(corpus_id=self.corpus_id, project_id=self.project_id or None,
            actor=self.actor, allow_writes=self.allow_writes)
        # Never trust payload["validation"]. It is informational, not a capability.
        if request.mode == "validate":
            return save_ontology(None, request, context).model_dump(mode="json")
        with configured_store(required=False) as store:
            return save_ontology(store, request, context).model_dump(mode="json")
