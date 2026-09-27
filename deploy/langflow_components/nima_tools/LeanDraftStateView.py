"""Read-only rendering of proof state and pending project recording."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class LeanDraftStateView(BaseComponent):
    name = "LeanDraftStateView"
    display_name = "Lean Revisions, Diagnostics and Progress"
    description = "Inspect private attempt revisions, argument revision history, issues, receipts and the pending project progress proposal."
    nima_manifest = manifest_for_component("LeanDraftStateView")

    async def run(self):
        result = _value(self.payload)
        data = result.get("data",{})
        return {"status":result["status"],"private_state":data.get("reasoning_state"),
            "source_history":data.get("source_history",[]),"context":data.get("context_packets",[]),
            "verifications":data.get("verifications",[]),"resolutions":data.get("resolutions",{}),"discoveries":data.get("discoveries",[]),
            "result":data.get("result"),"project_progress":data.get("project_progress"),
            "receipt_ids":result.get("receipt_ids",[]),"publishable":False}
