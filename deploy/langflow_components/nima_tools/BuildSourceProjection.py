"""Inspectable final exact-evidence check and revision-bound projection rebuild."""
from lfx.io import BoolInput, StrInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.source_tools import SourceToolContext, project_sources
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class BuildSourceProjection(BaseComponent):
    name = "BuildSourceProjection"
    display_name = "Validate and Publish Source Index"
    description = "Revalidate visible source evidence, rebuild lexical/vector/structural projections, and verify requested region coverage. Only this stage reports index readiness."
    nima_manifest = manifest_for_component("BuildSourceProjection")
    inputs = [handle("payload", "Receipted source/embedding result"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized index project (operator)", value="research"),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        BoolInput(name="allow_corpus_writes", display_name="Allow projection writes (operator)", value=False),
    ]

    def run_sync(self):
        incoming = ToolResult.model_validate(_value(self.payload))
        context = SourceToolContext(corpus_id=self.corpus_id, project_id=self.project_id or None,
            actor=self.actor, allow_corpus_writes=self.allow_corpus_writes)
        if incoming.data.get("mode") == "preview" or incoming.status != "complete":
            return incoming.model_dump(mode="json")
        with configured_store(required=False) as store:
            return project_sources(store, incoming, context).model_dump(mode="json")
