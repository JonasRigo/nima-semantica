"""Inspectable native embedding connection and exact-region indexing boundary."""
from lfx.io import BoolInput, HandleInput, StrInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.providers import ModelManifest, strict_json_object
from nima_semantica.source_tools import SourceToolContext, embed_sources
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.orchestration.langflow.adapter_support import configured_store, EmbeddingProvider
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class EmbedSourceRegions(BaseComponent):
    name = "EmbedSourceRegions"
    display_name = "Embed Exact Source Regions"
    description = "Lexical mode bypasses embedding. Vector mode requires an operator-connected Embeddings model and pinned manifest; never substitutes fake vectors."
    nima_manifest = manifest_for_component("EmbedSourceRegions")
    inputs = [handle("payload", "Receipted prepared sources"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized index project (operator)", value="research"),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        BoolInput(name="allow_corpus_writes", display_name="Allow source/index workflow writes (operator)", value=False),
        BoolInput(name="allow_embeddings", display_name="Allow connected embedding calls (operator)", value=False),
        HandleInput(name="embeddings", display_name="Embedding model (operator connection)", input_types=["Embeddings"], required=False),
        StrInput(name="manifest_json", display_name="Pinned embedding manifest (operator)", value="{}",
            info="Set provider, model, revision, parameters, dimension, and normalization to match the connected model; never include credentials."),
    ]

    async def run(self):
        incoming = ToolResult.model_validate(_value(self.payload))
        context = SourceToolContext(corpus_id=self.corpus_id, project_id=self.project_id or None,
            actor=self.actor, allow_corpus_writes=self.allow_corpus_writes, allow_embeddings=self.allow_embeddings)
        # Parsing model settings must also happen in the receipted stage, so a
        # malformed operator manifest cannot leave an unrecorded attempt.
        manifest = None
        provider = None
        if incoming.data.get("index_mode") == "vector" and self.allow_embeddings and self.allow_corpus_writes:
            try:
                manifest = ModelManifest.model_validate(strict_json_object(self.manifest_json))
                if getattr(self, "embeddings", None) is not None:
                    provider = EmbeddingProvider(self.embeddings, manifest.model_dump(mode="json"))
            except (ValueError, TypeError):
                manifest = None
        if incoming.data.get("mode") == "preview" or incoming.status != "complete":
            return incoming.model_dump(mode="json")
        with configured_store(required=False) as store:
            return embed_sources(store, incoming, context, provider=provider, manifest=manifest).model_dump(mode="json")
