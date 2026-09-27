"""Operator-authorized method-context retrieval inside search planning."""
from lfx.io import BoolInput, StrInput
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.math_retrieval import MathRetrievalPolicy


class ComparisonRetrieval(BaseComponent):
    name = "ComparisonRetrieval"
    display_name = "Comparison Context Retrieval · Operator Policy"
    description = "Optional native corpus retrieval and exact reads for support and counterevidence. May add prepared evidence regions, never enlarge the selected graph targets."
    nima_manifest = manifest_for_component("ComparisonRetrieval")
    inputs = [BoolInput(name="enabled",display_name="Allow context retrieval (operator)",value=False),
        StrInput(name="projection_id",display_name="Prepared projection ID (operator)",value="")]

    async def run(self):
        return MathRetrievalPolicy(enabled=self.enabled,projection_id=self.projection_id.strip() or None).model_dump(mode="json")
