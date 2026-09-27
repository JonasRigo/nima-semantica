"""Inspectable comparison agent with exact grounding and proposal-only publication."""
import json
from lfx.io import StrInput, BoolInput, IntInput, HandleInput
from lfx.schema import Message, DataFrame
from nima_semantica.comparison_contracts import CompareObjectsRequest, ComparisonContext
from nima_semantica.comparison_tool import compare_research_objects
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.comparison_state import POLICY_DIGEST
from nima_semantica.math_retrieval import MathRetrievalPolicy
from nima_semantica.providers import ModelManifest, completion_envelope
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class CompareResearchObjects(BaseComponent):
    name = "CompareResearchObjects"
    display_name = "Compare Research Objects · Ontology-State Agent"
    description = "Single ontology-state agent: compare all selected pairs under immutable criteria, seek exact evidence, revise findings and inspect represented dependencies. Preserves assumptions, losses and obligations; no winner selection, mathematical certification or graph admission."
    nima_manifest = manifest_for_component("CompareResearchObjects")
    inputs = [handle("payload","Validated comparison request"),handle("policy","Pinned private ontology",required=False),
        handle("retrieval_policy","Optional context retrieval policy",required=False),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project (operator)",value="research"),
        BoolInput(name="allow_audit_writes",display_name="Allow attempts and outcome artifacts (operator)",value=False),
        BoolInput(name="allow_model_calls",display_name="Allow model calls (operator)",value=False),
        IntInput(name="max_actions",display_name="Agent action limit (operator)",value=24),
        IntInput(name="max_nodes",display_name="Maximum snapshot nodes (operator)",value=256),
        IntInput(name="max_edges",display_name="Maximum snapshot edges (operator)",value=512),
        IntInput(name="max_read_regions",display_name="Maximum exact-read regions (operator)",value=32),
        StrInput(name="model_manifest_json",display_name="Model identity JSON (operator)",value="{}",advanced=True),
        HandleInput(name="model",display_name="Comparison language model (operator)",input_types=["LanguageModel"],required=False)]

    async def run(self):
        policy = getattr(self,"policy",None)
        if policy and _value(policy).get("policy_digest") != POLICY_DIGEST:
            raise ValueError("canvas policy differs from pinned controller policy")
        request = CompareObjectsRequest.model_validate(_value(self.payload))
        manifest = ModelManifest.model_validate_json(self.model_manifest_json) if self.model_manifest_json.strip() not in ("","{}") else None
        context = ComparisonContext(corpus_id=self.corpus_id,project_id=self.project_id,
            allow_audit_writes=self.allow_audit_writes,allow_model_calls=self.allow_model_calls,
            max_actions=self.max_actions,max_nodes=self.max_nodes,max_edges=self.max_edges,max_read_regions=self.max_read_regions,
            model_manifest=manifest, retrieval=MathRetrievalPolicy.model_validate(_value(self.retrieval_policy))
                if getattr(self,"retrieval_policy",None) else MathRetrievalPolicy())
        language_model = getattr(self,"model",None)
        def invoke(prompt):
            messages = [*prompt["messages"],{"role":"user","content":"Remaining actions: "+str(prompt["remaining_actions"])}]
            response = language_model.bind_tools(prompt["tools"],tool_choice="required",parallel_tool_calls=False).invoke(messages)
            if not response.usage_metadata:raise ValueError("model omitted usage metadata")
            calls = response.tool_calls
            valid = len(calls)==1 and calls[0]["name"] in {t["function"]["name"] for t in prompt["tools"]} and not getattr(response,"invalid_tool_calls",[])
            content = json.dumps({"name":calls[0]["name"],"arguments":calls[0]["args"]}) if valid else "invalid tool envelope"
            return completion_envelope(content,manifest,response.usage_metadata,response.response_metadata)
        with configured_store(required=False) as store:
            return compare_research_objects(store,request,context,model=invoke if language_model is not None else None).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("data",{}).get("attempts",[]))
