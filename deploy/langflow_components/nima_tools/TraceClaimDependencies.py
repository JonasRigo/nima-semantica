"""Inspectable native traversal; no model, proof checker or scientific graph writes."""
import json
from lfx.io import StrInput,BoolInput,IntInput
from lfx.schema import Message,DataFrame
from nima_semantica.claim_dependencies import TraceDependenciesRequest,TraceDependenciesContext,trace_claim_dependencies,POLICY_DIGEST
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent,handle
from nima_semantica.orchestration.langflow.values import _value


class TraceClaimDependencies(BaseComponent):
    name="TraceClaimDependencies"
    display_name="Trace Claim Dependencies · Bounded Traversal"
    description="Validate the pinned graph/candidate, follow declared necessary dependencies, and return exact paths, conditional edges, cycle witnesses, structural leaves, obligations and explicit cutoffs. Audit persistence only; no entailment or graph admission."
    nima_manifest=manifest_for_component("TraceClaimDependencies")
    inputs=[handle("payload","Pinned claim trace request"),handle("policy","Inspectable traversal policy",required=False),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project; blank for corpus (operator)",value="research"),
        BoolInput(name="allow_audit_writes",display_name="Allow receipts and outcome artifacts (operator)",value=False),
        IntInput(name="max_nodes",display_name="Maximum input nodes (operator)",value=256),
        IntInput(name="max_edges",display_name="Maximum input edges (operator)",value=512),
        IntInput(name="max_visited_nodes",display_name="Maximum visited nodes (operator)",value=128),
        IntInput(name="max_traversed_edges",display_name="Maximum traversed edges (operator)",value=256),
        IntInput(name="max_depth",display_name="Maximum path depth (operator)",value=32),
        IntInput(name="max_paths",display_name="Maximum returned paths (operator)",value=128),
        IntInput(name="max_steps",display_name="Maximum traversal steps (operator)",value=10000),
        IntInput(name="timeout_seconds",display_name="Traversal timeout (operator)",value=30)]

    def run_sync(self):
        if getattr(self,"policy",None) and _value(self.policy).get("policy_digest")!=POLICY_DIGEST:
            raise ValueError("canvas policy differs from native policy")
        request=TraceDependenciesRequest.model_validate(_value(self.payload))
        context=TraceDependenciesContext(corpus_id=self.corpus_id,project_id=self.project_id.strip() or None,
            allow_audit_writes=self.allow_audit_writes,**{k:getattr(self,k) for k in
                ("max_nodes","max_edges","max_visited_nodes","max_traversed_edges","max_depth","max_paths","max_steps","timeout_seconds")})
        with configured_store(required=False) as store:
            return trace_claim_dependencies(store,request,context).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("data",{}).get("trace",{}).get("paths",[]))
