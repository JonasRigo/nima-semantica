"""Fixed project-write and attempt-history policy; no approval is generated here."""
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.project_update import POLICY_DIGEST,PROGRESS_PROFILE,PROGRESS_KINDS


class ProjectUpdatePolicy(BaseComponent):
    name="ProjectUpdatePolicy"
    display_name="Exact Approval and Progress Policy"
    description="Project-only, revision/hash/actor-bound approval. Private state is never admitted. Native attempts become observed history, not verified science. Progress ontology registration shares the approved atomic commit."
    nima_manifest=manifest_for_component("ProjectUpdatePolicy")
    inputs=[]

    async def run(self):
        return {"policy_digest":POLICY_DIGEST,"progress_ontology":PROGRESS_PROFILE.model_dump(mode="json"),
            "progress_kinds":PROGRESS_KINDS,"approval_granted":False,"automatic_scientific_promotion":False}
