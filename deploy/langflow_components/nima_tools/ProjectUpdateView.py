"""Inspect exact committed revision separately from projection/publication status."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class ProjectUpdateView(BaseComponent):
    name="ProjectUpdateView"
    display_name="Commit, Progress and Projection Receipts"
    description="Inspect prepared delta/hash, commit receipt and final revision, progress recording and separately recoverable projection status. A write receipt is not a scientific validity certificate."
    nima_manifest=manifest_for_component("ProjectUpdateView")

    async def run(self):
        result=_value(self.payload)
        return {"status":result["status"],"data":result.get("data",{}),"receipt_ids":result.get("receipt_ids",[])}
