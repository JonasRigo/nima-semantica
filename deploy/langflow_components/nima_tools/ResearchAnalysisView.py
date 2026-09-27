"""Inert view of saved report artifacts, receipts and pending project recording."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component

class ResearchAnalysisView(BaseComponent):
    name="ResearchAnalysisView"
    display_name="Analysis · Artifacts and Publication Receipts"
    description="Inspect immutable JSON/Markdown IDs, validation checks and progress. Saving does not approve scientific conclusions."
    nima_manifest=manifest_for_component("ResearchAnalysisView")
    async def run(self):
        result=_value(self.payload)
        return {"status":result["status"],"analysis":result.get("data",{}),
            "artifacts":result.get("artifacts",{}),"receipt_ids":result.get("receipt_ids",[]),"scientific_admission":False}
