"""Operator-authorized method-context retrieval inside search planning."""
from lfx.io import BoolInput, StrInput, DropdownInput
from nima_semantica.installation import EmbeddingProfile
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.math_retrieval import MathRetrievalPolicy


class LeanDraftRetrieval(BaseComponent):
    name = "LeanDraftRetrieval"
    display_name = "Lean Context Retrieval · Operator Policy"
    description = "Optional native corpus retrieval and exact reads for support and counterevidence. May add prepared evidence regions, never enlarge the selected graph targets."
    nima_manifest = manifest_for_component("LeanDraftRetrieval")
    inputs = [BoolInput(name="enabled",display_name="Allow context retrieval (operator)",value=False),
        StrInput(name="projection_id",display_name="Prepared projection ID (operator)",value=""),
        DropdownInput(name="mode",display_name="Retrieval mode (operator)",options=["hybrid","vector","lexical"],value="hybrid"),
        BoolInput(name="allow_embeddings",display_name="Allow query embeddings (operator)",value=False),
        BoolInput(name="allow_attempt_writes",display_name="Allow retrieval receipts (operator)",value=False),
        StrInput(name="embedding_profile_json",display_name="Pinned embedding profile (operator)",value="null")]

    async def run(self):
        return MathRetrievalPolicy(enabled=self.enabled,projection_id=self.projection_id.strip() or None,
            mode=self.mode,allow_embeddings=self.allow_embeddings,allow_attempt_writes=self.allow_attempt_writes,
            embedding_profile=EmbeddingProfile.model_validate_json(self.embedding_profile_json) if self.embedding_profile_json.strip() != "null" else None).model_dump(mode="json")
