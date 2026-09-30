"""Deterministic scope validation, rendering and immutable publication."""
import json
from lfx.io import StrInput, BoolInput
from lfx.schema import Message, DataFrame
from nima_semantica.research_analysis import SaveAnalysisRequest, SaveAnalysisContext, save_research_analysis
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value

class SaveResearchAnalysis(BaseComponent):
    name="SaveResearchAnalysis"
    display_name="Save Analysis · Validate, Render and Publish"
    description="Validate scope, exact provenance, receipt references and structured review substantiation; render deterministic JSON/Markdown; publish immutable artifacts and pending project progress. No LLM or scientific graph admission."
    nima_manifest=manifest_for_component("SaveResearchAnalysis")
    inputs=[handle("payload","Analysis request"),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project (operator)",value="research"),
        BoolInput(name="allow_artifact_writes",display_name="Allow report and audit publication (operator)",value=False)]
    def run_sync(self):
        request=SaveAnalysisRequest.model_validate(_value(self.payload))
        context=SaveAnalysisContext(corpus_id=self.corpus_id,project_id=self.project_id,
            allow_artifact_writes=self.allow_artifact_writes)
        with configured_store(required=False) as store:
            return save_research_analysis(store,request,context).model_dump(mode="json")
    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")
    async def table_data(self) -> DataFrame:
        return DataFrame([{"kind":k,"artifact_id":v} for k,v in (await self.result_data()).data.get("artifacts",{}).items()])
