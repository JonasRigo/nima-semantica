"""Inspectable operator scope and append-only Research Run service boundary."""
from lfx.io import BoolInput, StrInput, Output
from lfx.schema import DataFrame

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.research_run_tool import ResearchRunContext, ResearchRunRequest, research_run
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class ResearchRunTool(BaseComponent):
    name = "ResearchRunTool"
    display_name = "Research Run"
    description = "Record harness decisions and inspect attempts. No scheduling, model calls, graph admission, or scientific certification."
    nima_manifest = manifest_for_component("ResearchRunTool")
    inputs = [
        handle("payload", "Validated request"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research"),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        BoolInput(name="allow_writes", display_name="Allow audit writes (operator)", value=False,
            info="Authorize run/transition and receipt writes only. This does not approve graph changes or scientific claims."),
    ]
    outputs = [
        Output(name="result", display_name="Typed run result", method="result_data", group_outputs=True),
        Output(name="preview", display_name="Run and history preview", method="preview_message", group_outputs=True),
        Output(name="table", display_name="Attempt history", method="table_data", group_outputs=True),
    ]

    async def run(self):
        request = ResearchRunRequest.model_validate(_value(self.payload))
        context = ResearchRunContext(corpus_id=self.corpus_id, project_id=self.project_id,
            actor=self.actor, allow_writes=self.allow_writes)
        # The path comes exclusively from deployment configuration, not requests.
        with configured_store(required=False) as store:
            if store is None:
                return ToolResult(operation="Research Run", status="unavailable",
                    diagnostics=({"code":"research_run.store_not_configured", "message":"Set operator-owned NIMA_STORE_ROOT to an existing v2 store."},),
                    note="No run or receipt was written.").model_dump(mode="json")
            return research_run(store, request, context).model_dump(mode="json")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data["data"].get("attempts", []))
