"""Read-only inspection of paths, exact edge evidence, limits and progress."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class DependencyTraceView(BaseComponent):
    name="DependencyTraceView"
    display_name="Dependency Paths, Cycles, Leaves and Progress"
    description="Inspect exact node/edge identities and evidence, distinct routes, cycle witnesses, outstanding recorded obligations and traversal frontiers. Pending progress is not a scientific graph commit."
    nima_manifest=manifest_for_component("DependencyTraceView")

    async def run(self):
        result=_value(self.payload)
        return {"status":result["status"],"data":result.get("data",{}),"artifacts":result.get("artifacts",{}),
            "receipt_ids":result.get("receipt_ids",[]),"scientific_admission":False}
