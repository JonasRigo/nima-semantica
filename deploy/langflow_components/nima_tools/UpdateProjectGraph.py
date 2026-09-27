"""Inspectable native commit transport; no model or automatic approval."""
import json
from lfx.io import StrInput,BoolInput,IntInput
from lfx.schema import Message,DataFrame
from nima_semantica.project_update import UpdateProjectRequest,UpdateProjectContext,update_project_graph,POLICY_DIGEST
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent,handle
from nima_semantica.orchestration.langflow.values import _value


class UpdateProjectGraph(BaseComponent):
    name="UpdateProjectGraph"
    display_name="Update Project Graph · Approved Commit"
    description="Read-only preparation, then exact operator-approved project commit. Preserve progress/outcome links atomically; rebuild projections only when authorized. Failed projection does not undo a committed graph. No model, automatic approval or corpus promotion."
    nima_manifest=manifest_for_component("UpdateProjectGraph")
    inputs=[handle("payload","Exact project update request"),handle("policy","Inspectable commit policy",required=False),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project (operator)",value="research"),
        StrInput(name="actor",display_name="Approving actor (operator)",value="harness"),
        StrInput(name="approval_json",display_name="Exact OKFCommitApproval JSON (operator)",value="null"),
        BoolInput(name="allow_graph_writes",display_name="Allow approved graph writes (operator)",value=False),
        BoolInput(name="allow_audit_writes",display_name="Allow attempt records (operator)",value=False),
        BoolInput(name="allow_projection_writes",display_name="Allow projection rebuilds (operator)",value=False),
        IntInput(name="max_changes",display_name="Maximum delta changes (operator)",value=512),
        IntInput(name="max_regions",display_name="Maximum indexed regions (operator)",value=100000),
        IntInput(name="max_tokens",display_name="Maximum lexical tokens (operator)",value=1000000)]

    async def run(self):
        if getattr(self,"policy",None) and _value(self.policy).get("policy_digest")!=POLICY_DIGEST:raise ValueError("canvas policy differs from native policy")
        request=UpdateProjectRequest.model_validate(_value(self.payload))
        raw=json.loads(self.approval_json)
        context=UpdateProjectContext(corpus_id=self.corpus_id,project_id=self.project_id,actor=self.actor,
            approval=OKFCommitApproval.model_validate(raw) if raw else None,**{k:getattr(self,k) for k in
                ("allow_graph_writes","allow_audit_writes","allow_projection_writes","max_changes","max_regions","max_tokens")})
        with configured_store(required=False) as store:return update_project_graph(store,request,context).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        data=(await self.result_data()).data.get("data",{})
        return DataFrame([data["project_recording"]] if "project_recording" in data else [])
