"""Pinned installation embedding provider, visible on the canvas."""
import json
from types import SimpleNamespace
from langchain_core.embeddings import Embeddings
from lfx.custom import Component
from lfx.io import Output, StrInput, SecretStrInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.installation import EmbeddingProfile
from nima_semantica.setup_services import embedding_provider


class ConfiguredEmbeddings(Component):
    nima_manifest = manifest_for_component("ConfiguredEmbeddings")
    display_name = "NIMA Configured Embeddings"
    name = "ConfiguredEmbeddings"
    description = "Exact model identity and dimensions from the installation profile. Credentials resolve from the installation's named Langflow Credential."
    inputs = [StrInput(name="profile_json", display_name="Embedding profile", value="{}"), SecretStrInput(name="api_key", display_name="Embedding API key", value="", required=False)]
    outputs = [Output(name="embeddings", display_name="Embeddings", method="build_embeddings")]

    def build_embeddings(self) -> Embeddings:
        profile = EmbeddingProfile.model_validate_json(self.profile_json)
        provider, manifest = embedding_provider(SimpleNamespace(embedding=profile), credential_value=self.api_key)
        class Adapter(Embeddings):
            def embed_documents(self, texts):
                return provider.embed("configured", texts)[0]
            def embed_query(self, text):
                return provider.embed_query("configured", text)[0][0]
        return Adapter()
