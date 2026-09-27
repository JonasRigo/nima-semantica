"""Visible deterministic Lean service boundary; no model or retrieval component."""
import json
from lfx.io import StrInput, BoolInput, HandleInput
from lfx.schema import Message, DataFrame
from nima_semantica.verify_lean_tool import VerifyLeanRequest, VerifyLeanContext, verify_lean
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class VerifyLean(BaseComponent):
    name="VerifyLean"
    display_name="Verify Lean · Pinned Kernel Check"
    description="Execute exact submitted modules in the operator-pinned isolated verifier; independently inspect theorem dependencies, allowed axioms and kernel replay. Persist exact types, source hashes, diagnostics, attempts and pending project progress. No source edits, dependency installation, retrieval or LLM."
    nima_manifest=manifest_for_component("VerifyLean")
    inputs=[handle("payload","Exact Lean submission"),
        StrInput(name="corpus_id",display_name="Authorized corpus (operator)",value="papers"),
        StrInput(name="project_id",display_name="Authorized project (operator)",value="research"),
        BoolInput(name="allow_execution",display_name="Allow isolated Lean execution (operator)",value=False),
        BoolInput(name="allow_audit_writes",display_name="Allow proof attempts and artifacts (operator)",value=False),
        HandleInput(name="verifier",display_name="Pinned Lean verifier (optional operator injection)",input_types=["LeanVerifier"],required=False)]

    async def run(self):
        request=VerifyLeanRequest.model_validate(_value(self.payload))
        context=VerifyLeanContext(corpus_id=self.corpus_id,project_id=self.project_id,
            allow_execution=self.allow_execution,allow_audit_writes=self.allow_audit_writes)
        with configured_store(required=False) as store:
            return verify_lean(store,request,context,verifier=getattr(self,"verifier",None)).model_dump(mode="json")

    async def preview_message(self) -> Message:
        text=json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):text=text.replace(char,"\\u"+format(ord(char),"04x"))
        return Message(text="```json\n"+text+"\n```")

    async def table_data(self) -> DataFrame:
        data=(await self.result_data()).data.get("data",{})
        checked=data.get("verification",{}).get("project_result",{})
        return DataFrame([{"target":name,"exact_type":kind,"axioms":checked.get("axioms",{}).get(name,[]),
            "formal_acceptance":data.get("formal_verification_accepted",False),"correspondence_verified":False}
            for name,kind in checked.get("declaration_types",{}).items()])
