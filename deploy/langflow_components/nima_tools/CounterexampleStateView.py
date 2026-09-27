"""Read-only rendering of search state and pending project recording."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class CounterexampleStateView(BaseComponent):
    name = "CounterexampleStateView"
    display_name = "Private Search State, Coverage and Progress"
    description = "Inspect encodings, attempts, witness checks, source context and the pending progress proposal. No project graph changes."
    nima_manifest = manifest_for_component("CounterexampleStateView")

    async def run(self):
        result = _value(self.payload)
        data = result.get("data",{})
        return {"status":result["status"],"private_state":data.get("reasoning_state"),
            "searches":data.get("searches",[]),"context":data.get("context_packets",[]),
            "result":data.get("result"),"project_progress":data.get("project_progress"),
            "receipt_ids":result.get("receipt_ids",[]),"publishable":False}
