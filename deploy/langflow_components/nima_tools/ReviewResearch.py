"""Inspectable review agent with exact grounding and proposal-only publication."""
import json
from lfx.io import StrInput, BoolInput, IntInput, HandleInput
from lfx.schema import Message, DataFrame
from nima_semantica.review_contracts import ReviewRequest, ReviewContext
from nima_semantica.review_tool import review_research
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.review_state import POLICY_DIGEST
from nima_semantica.math_retrieval import MathRetrievalPolicy
from nima_semantica.providers import ModelManifest, completion_envelope
from nima_semantica.symbolic_transport import configured_symbolic_worker
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class ReviewResearch(BaseComponent):
    name = "ReviewResearch"
    display_name = "Review Research · Ontology-State Agent"
    description = "Single ontology-state reviewer: exact proof/claim scope, evidence retrieval, represented dependency checks, authorized counterexample searches, matching substantiation and advisory assessments. The harness retains acceptance and scheduling."
    nima_manifest = manifest_for_component("ReviewResearch")
    inputs = [
        BoolInput(name="allow_counterexamples",display_name="Allow scoped isolated counterexample searches (operator)",value=False),
        StrInput(name="counterexample_target_ids_json",display_name="Authorized search target IDs (JSON list, operator)",value="[]"),
        IntInput(name="max_counterexamples",display_name="Maximum nested searches (operator)",value=3),
        IntInput(name="max_evaluations",display_name="Maximum evaluations per search (operator)",value=1000),
        BoolInput(name="allow_substantiation",display_name="Allow nested source substantiation (operator)",value=False),
        IntInput(name="max_substantiations",display_name="Maximum nested substantiations (operator)",value=2),handle("payload","Validated review request"),handle("policy","Pinned private ontology",required=False),
        handle("retrieval_policy","Optional context retrieval policy",required=False),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project (operator)",value="research"),
        BoolInput(name="allow_audit_writes",display_name="Allow attempts and outcome artifacts (operator)",value=False),
        BoolInput(name="allow_model_calls",display_name="Allow model calls (operator)",value=False),
        IntInput(name="max_actions",display_name="Agent action limit (operator)",value=20),
        IntInput(name="max_nodes",display_name="Maximum snapshot nodes (operator)",value=128),
        IntInput(name="max_edges",display_name="Maximum snapshot edges (operator)",value=256),
        IntInput(name="max_read_regions",display_name="Maximum exact-read regions (operator)",value=32),
        StrInput(name="model_manifest_json",display_name="Model identity JSON (operator)",value="{}",advanced=True),
        HandleInput(name="model",display_name="Review language model (operator)",input_types=["LanguageModel"],required=False)]

    def run_sync(self):
        policy = getattr(self,"policy",None)
        if policy and _value(policy).get("policy_digest") != POLICY_DIGEST:
            raise ValueError("canvas policy differs from pinned controller policy")
        request = ReviewRequest.model_validate(_value(self.payload))
        manifest = ModelManifest.model_validate_json(self.model_manifest_json) if self.model_manifest_json.strip() not in ("","{}") else None
        context = ReviewContext(corpus_id=self.corpus_id,project_id=self.project_id,
            allow_audit_writes=self.allow_audit_writes,allow_model_calls=self.allow_model_calls,
            allow_counterexamples=self.allow_counterexamples,counterexample_target_ids=json.loads(self.counterexample_target_ids_json),
            max_counterexamples=self.max_counterexamples,max_evaluations=self.max_evaluations,
            allow_substantiation=self.allow_substantiation,max_substantiations=self.max_substantiations,
            max_actions=self.max_actions,max_nodes=self.max_nodes,max_edges=self.max_edges,max_read_regions=self.max_read_regions,
            model_manifest=manifest, retrieval=MathRetrievalPolicy.model_validate(_value(self.retrieval_policy))
                if getattr(self,"retrieval_policy",None) else MathRetrievalPolicy())
        language_model = getattr(self,"model",None)
        if request.mode != "preview" and context.allow_counterexamples:
            try:
                health = configured_symbolic_worker().run("print(1)", 5)
                if health.get("outcome") != "executed" or health.get("stdout", "").strip() != "1":
                    raise RuntimeError("symbolic worker probe did not execute")
            except Exception:
                return ToolResult(operation="Review Research", status="unavailable",
                    diagnostics=({"code":"symbolic_worker_unavailable", "message":"Configured isolated symbolic worker failed its execution probe."},)).model_dump(mode="json")
        def invoke(prompt):
            messages = [*prompt["messages"],{"role":"user","content":"Remaining actions: "+str(prompt["remaining_actions"])}]
            response = language_model.bind_tools(prompt["tools"],tool_choice="required",parallel_tool_calls=False).invoke(messages)
            if not response.usage_metadata:raise ValueError("model omitted usage metadata")
            calls = response.tool_calls
            valid = len(calls)==1 and calls[0]["name"] in {t["function"]["name"] for t in prompt["tools"]} and not getattr(response,"invalid_tool_calls",[])
            content = json.dumps({"name":calls[0]["name"],"arguments":calls[0]["args"]}) if valid else "invalid tool envelope"
            return completion_envelope(content,manifest,response.usage_metadata,response.response_metadata)
        with configured_store(required=False) as store:
            return review_research(store,request,context,model=invoke if language_model is not None else None).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("data",{}).get("attempts",[]))
