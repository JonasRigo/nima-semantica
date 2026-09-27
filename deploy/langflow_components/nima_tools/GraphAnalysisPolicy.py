"""Visible fixed inference rules, rather than model-selected rule admission."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.graph_analysis import RULES,POLICY_DIGEST,TRUSTED_STATUSES


class GraphAnalysisPolicy(BaseComponent):
    name="GraphAnalysisPolicy"
    display_name="Analysis Translation and Inference Rules"
    description="Typed metadata only. Dependency direction comes from the saved ontology's necessary_dependency flag. Rules detect reachability, cycles and unresolved/problem dependencies, never infer truth from supporting prose."
    nima_manifest=manifest_for_component("GraphAnalysisPolicy")
    inputs=[]

    async def run(self):
        return {"policy_digest":POLICY_DIGEST,"rules":[r.model_dump(mode="json") for r in RULES],
            "strict_relation_statuses":sorted(TRUSTED_STATUSES),"publishable":False,
            "limits":"Stored statuses are not independent verification. Missing attached evidence is not evidence of absence. Properties and prose are never executed as logic."}
