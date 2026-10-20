"""Operator-selected native model protocol, independent of embeddings."""
from lfx.custom import Component
from lfx.io import Output, StrInput, SecretStrInput
from lfx.field_typing import LanguageModel
from nima_semantica.installation import ModelProfile
from nima_semantica.model_setup import native_chat_model
from nima_semantica.component_contracts import manifest_for_component


class ConfiguredModel(Component):
    name = "ConfiguredModel"
    display_name = "NIMA Configured Model"
    description = "Provider-aware OpenAI, OpenRouter, compatible, Anthropic, Gemini or Ollama model; embeddings are configured separately."
    nima_manifest = manifest_for_component("ConfiguredModel")
    inputs = [StrInput(name="profile_json", display_name="Model profile", value="{}"),
              SecretStrInput(name="api_key", display_name="Model credential", value="", required=False)]
    outputs = [Output(name="model_output", display_name="Language Model", method="build_model")]

    def build_model(self) -> LanguageModel:
        profile = ModelProfile.model_validate_json(self.profile_json)
        value = self.api_key
        if profile.provider != "ollama" and profile.credential:
            from nima_semantica.credential_setup import secret_value
            value = secret_value(value, profile.credential)
        return native_chat_model(profile, value)
