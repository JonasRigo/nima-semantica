"""Read-only rendering of comparison state and pending project recording."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class ComparisonStateView(BaseComponent):
    name = "ComparisonStateView"
    display_name = "Private Comparison State, Coverage and Progress"
    description = "Inspect private attempt revisions, assessment history, issues, receipts and the pending project progress proposal."
    nima_manifest = manifest_for_component("ComparisonStateView")

    async def run(self):
        result = _value(self.payload)
        data = result.get("data",{})
        return {"status":result["status"],"private_state":data.get("reasoning_state"),
            "assessment_history":data.get("assessment_history",[]),"context":data.get("context_packets",[]),
            "result":data.get("result"),"project_progress":data.get("project_progress"),
            "receipt_ids":result.get("receipt_ids",[]),"publishable":False}
