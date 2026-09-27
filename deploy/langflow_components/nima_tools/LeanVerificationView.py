"""Inert inspection of exact types, diagnostics, attempts and pending progress."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.component_contracts import manifest_for_component


class LeanVerificationView(BaseComponent):
    name="LeanVerificationView"
    display_name="Lean Evidence, Diagnostics and Progress"
    description="Exact formal result, environment, source hashes, diagnostic logs and receipt-bound progress proposal. Formal success does not certify source correspondence or complete a parent proof."
    nima_manifest=manifest_for_component("LeanVerificationView")

    async def run(self):
        result=_value(self.payload)
        return {"status":result["status"],"evidence":result.get("data",{}),
            "artifacts":result.get("artifacts",{}),"receipt_ids":result.get("receipt_ids",[]),"scientific_admission":False}
