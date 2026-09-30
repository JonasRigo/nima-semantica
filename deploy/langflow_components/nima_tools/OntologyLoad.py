"""Inspectable authorized listing and exact immutable profile lookup."""
from lfx.io import StrInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.ontology_tools import LoadOntologyRequest, OntologyContext, load_ontology
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class OntologyLoad(BaseComponent):
    name = "OntologyLoad"
    display_name = "List and Load Ontology"
    description = "List packaged and authorized saved profiles, then resolve the exact immutable identity. Read-only; never loads executable code."
    nima_manifest = manifest_for_component("OntologyLoad")
    inputs = [handle("payload", "Validated selection"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research",
            info="Empty means corpus-only; never lists other projects."),
    ]

    async def run(self):
        payload = _value(self.payload)
        if payload.get("request_error"):
            from nima_semantica.tool_contracts import ToolResult
            return ToolResult(operation="Load Ontology", status="failed", diagnostics=({
                "code": "request.invalid_json", "message": payload["request_error"]},)).model_dump(mode="json")
        request = LoadOntologyRequest.model_validate(payload)
        context = OntologyContext(corpus_id=self.corpus_id, project_id=self.project_id or None)
        with configured_store(required=False) as store:
            return load_ontology(store, request, context).model_dump(mode="json")
