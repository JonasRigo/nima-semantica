"""Inspectable authenticated read with exact-source checks and inert preview."""
import json
from lfx.io import StrInput
from lfx.schema import DataFrame, Message
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.evidence_reader import ReadEvidenceRequest, ReadEvidenceContext, read_evidence
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class ReadEvidence(BaseComponent):
    name = "ReadEvidence"
    display_name = "Read and Validate Evidence"
    description = "Resolve published artifacts in operator scope, verify registry/hash/source bindings, return complete content and bounded direct provenance. No writes, model calls, or active rendering."
    nima_manifest = manifest_for_component("ReadEvidence")
    inputs = [handle("payload","Validated evidence selection"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research",
            info="Empty means corpus-only; private artifacts from other projects are never read.")]

    def run_sync(self):
        request = ReadEvidenceRequest.model_validate(_value(self.payload))
        context = ReadEvidenceContext(corpus_id=self.corpus_id, project_id=self.project_id or None)
        with configured_store(required=False) as store:
            return read_evidence(store,request,context).model_dump(mode="json")

    async def preview_message(self) -> Message:
        # Valid JSON escaping preserves exact content after decoding and prevents
        # source backticks/HTML from escaping the inert preview code fence.
        text = json.dumps((await self.result_data()).data,ensure_ascii=False,indent=2)
        for char in ("`","<",">","&"):
            text = text.replace(char,"\\u" + format(ord(char),"04x"))
        return Message(text="```json\n" + text + "\n```")

    async def table_data(self) -> DataFrame:
        data = (await self.result_data()).data.get("data",{})
        return DataFrame([{key:data[key] for key in ("kind","artifact_id","bytes","content_complete","exact_source_checked")}] if "kind" in data else [])
