"""Inspectable prepared-index search, bounded expansion, and exact evidence boundary."""
from lfx.io import BoolInput, HandleInput, StrInput
from lfx.schema import DataFrame
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import ModelManifest, strict_json_object
from nima_semantica.research_retrieval import ResearchRetrievalRequest, ResearchRetrievalContext, retrieve_research_context
from nima_semantica.orchestration.langflow.adapter_support import configured_store, EmbeddingProvider
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class RetrieveResearchContext(BaseComponent):
    name = "RetrieveResearchContext"
    display_name = "Retrieve and Validate Research Context"
    description = "Resolve a current scoped projection, search lexical/vectors, expand evidence/graph links, and validate whole exact passages. Never rebuild indexes or mutate the scientific graph."
    nima_manifest = manifest_for_component("RetrieveResearchContext")
    inputs = [handle("payload", "Validated query and limits"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research",
            info="Empty means corpus-only; projections require exact scope."),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        BoolInput(name="allow_embeddings", display_name="Allow query embedding calls (operator)", value=False),
        BoolInput(name="allow_attempt_writes", display_name="Allow retrieval attempt receipts (operator)", value=False),
        HandleInput(name="embeddings", display_name="Query embedding model (operator connection)", input_types=["Embeddings"], required=False),
        StrInput(name="manifest_json", display_name="Pinned embedding manifest (operator)", value="{}",
            info="Must exactly match the indexed provider/model/revision/parameters/dimension/normalization. Never include credentials.")]

    def run_sync(self):
        request = ResearchRetrievalRequest.model_validate(_value(self.payload))
        context = ResearchRetrievalContext(corpus_id=self.corpus_id, project_id=self.project_id or None,
            actor=self.actor, allow_embeddings=self.allow_embeddings, allow_attempt_writes=self.allow_attempt_writes)
        manifest, provider = None, None
        if request.mode != "lexical" and context.allow_embeddings and context.allow_attempt_writes:
            try:
                manifest = ModelManifest.model_validate(strict_json_object(self.manifest_json))
                if getattr(self, "embeddings", None) is not None:
                    provider = EmbeddingProvider(self.embeddings, manifest.model_dump(mode="json"))
            except (ValueError, TypeError):
                manifest = None
        with configured_store(required=False) as store:
            # The service pins the generation and validates the index before any
            # model call; query identity, graph scope and exact bytes are checked.
            # Failed/cooperatively cancelled model attempts persist receipts.
            return retrieve_research_context(store, request, context, provider=provider, manifest=manifest).model_dump(mode="json")

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("data", {}).get("regions", []))
