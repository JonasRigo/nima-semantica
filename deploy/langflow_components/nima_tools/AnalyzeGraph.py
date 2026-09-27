"""Inspectable deterministic graph read, translation and Semantica boundary."""
import json
from lfx.io import StrInput, BoolInput, IntInput
from lfx.schema import Message, DataFrame
from nima_semantica.graph_analysis import AnalyzeGraphRequest,AnalyzeGraphContext,analyze_graph,POLICY_DIGEST
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent,handle
from nima_semantica.orchestration.langflow.values import _value


class AnalyzeGraph(BaseComponent):
    name="AnalyzeGraph"
    display_name="Analyze Graph · Deterministic Semantica"
    description="Validate scoped OKF evidence/ontology, translate typed metadata and run bounded dependency/obligation inference. Retain exact support identities, conditional hypotheses, diagnostics and pending project progress. No LLM, scientific admission or graph writes."
    nima_manifest=manifest_for_component("AnalyzeGraph")
    inputs=[handle("payload","Pinned analysis request"),handle("policy","Inspectable reasoning policy",required=False),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project; blank for corpus (operator)",value="research"),
        BoolInput(name="allow_audit_writes",display_name="Allow receipts and outcome artifacts (operator)",value=False),
        IntInput(name="max_nodes",display_name="Maximum nodes (operator)",value=128),
        IntInput(name="max_edges",display_name="Maximum edges (operator)",value=256),
        IntInput(name="max_facts",display_name="Maximum closure facts (operator)",value=2000),
        IntInput(name="max_matches",display_name="Maximum inference supports (operator)",value=10000),
        IntInput(name="max_rounds",display_name="Maximum inference rounds (operator)",value=64),
        IntInput(name="timeout_seconds",display_name="Inference timeout (operator)",value=30)]

    async def run(self):
        if getattr(self,"policy",None) and _value(self.policy).get("policy_digest")!=POLICY_DIGEST:
            raise ValueError("canvas policy differs from native policy")
        request=AnalyzeGraphRequest.model_validate(_value(self.payload))
        context=AnalyzeGraphContext(corpus_id=self.corpus_id,project_id=self.project_id.strip() or None,
            allow_audit_writes=self.allow_audit_writes,max_nodes=self.max_nodes,max_edges=self.max_edges,
            max_facts=self.max_facts,max_matches=self.max_matches,max_rounds=self.max_rounds,timeout_seconds=self.timeout_seconds)
        with configured_store(required=False) as store:
            return analyze_graph(store,request,context).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("data",{}).get("conclusions",[]))
