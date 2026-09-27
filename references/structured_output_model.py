import json
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from trustcall import create_extractor

from lfx.base.agents.token_callback import TokenUsageCallbackHandler
from lfx.base.models.chat_result import get_chat_result
from lfx.base.models.unified_models import (
    get_llm,
    handle_model_input_update,
)
from lfx.custom.custom_component.component import Component
from lfx.helpers.base_model import build_model_from_schema
from lfx.io import (
    DataInput,
    MessageTextInput,
    ModelInput,
    MultilineInput,
    Output,
    SecretStrInput,
)
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class StructuredOutputComponent(Component):
    display_name = "Structured Output"
    description = "Uses an LLM to generate structured data. Ideal for extraction and consistency."
    documentation: str = "https://docs.langflow.org/structured-output"
    name = "StructuredOutput"
    icon = "braces"

    inputs = [
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Overrides global provider settings. Leave blank to use your pre-configured API Key.",
            real_time_refresh=True,
            advanced=True,
        ),
        MultilineInput(
            name="input_value",
            display_name="Input Message",
            info="The input message to the language model.",
            tool_mode=True,
            required=True,
        ),
        MultilineInput(
            name="system_prompt",
            display_name="Format Instructions",
            info="The instructions to the language model for formatting the output.",
            value=(
                "You are an AI that extracts structured JSON objects from unstructured text. "
                "Use a predefined schema with expected types (str, int, float, bool, dict). "
                "Extract ALL relevant instances that match the schema - if multiple patterns exist, capture them all. "
                "Fill missing or ambiguous values with defaults: null for missing values. "
                "Remove exact duplicates but keep variations that have different field values. "
                "Always return valid JSON in the expected format, never throw errors. "
                "If multiple objects can be extracted, return them all in the structured format."
            ),
            required=True,
            advanced=True,
        ),
        DataInput(
            name="ontology",
            display_name="Ontology Schema",
            info="Connect Ontology Manager output, or provide its JSON descriptor.",
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="schema_name",
            display_name="Schema Name",
            info="Provide a name for the output data schema.",
            advanced=True,
        ),
        DataInput(
            name="output_schema",
            display_name="Output Schema",
            info="Connect an Ontology Manager Data/JSON schema descriptor.",
            required=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="structured_output",
            display_name="Structured Output",
            method="build_structured_output",
        ),
        Output(
            name="dataframe_output",
            display_name="Structured Output",
            method="build_structured_dataframe",
        ),
    ]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """Dynamically update build config with user-filtered model options."""
        return handle_model_input_update(self, build_config, field_value, field_name)

    def build_structured_output_base(self):
        schema_name = self.schema_name or "OutputModel"

        llm = get_llm(model=self.model, user_id=self.user_id, api_key=self.api_key)

        if not hasattr(llm, "with_structured_output"):
            msg = "Language model does not support structured output."
            raise TypeError(msg)
        output_model_ = self._resolve_output_model()
        output_model = create_model(
            schema_name,
            __doc__=f"A list of {schema_name}.",
            objects=(
                list[output_model_],
                Field(
                    description=f"A list of {schema_name}.",  # type: ignore[valid-type]
                    min_length=1,  # help ensure non-empty output
                ),
            ),
        )
        # Tracing config with token usage handler injected into the callbacks chain.
        # get_chat_result() reads "get_langchain_callbacks" as a callable, so we wrap
        # the list in a lambda to match its expected interface.
        token_handler = TokenUsageCallbackHandler()
        base_callbacks = self.get_langchain_callbacks()
        config_dict = {
            "display_name": self.display_name,
            "get_project_name": self.get_project_name,
            "get_langchain_callbacks": lambda: [*base_callbacks, token_handler],
        }
        # Generate structured output using Trustcall first, then fallback to Langchain if it fails
        result = self._extract_output_with_trustcall(llm, output_model, config_dict)
        if result is None:
            result = self._extract_output_with_langchain(llm, output_model, config_dict)
        self._token_usage = token_handler.get_usage()

        # OPTIMIZATION NOTE: Simplified processing based on trustcall response structure
        # Handle non-dict responses (shouldn't happen with trustcall, but defensive)
        if not isinstance(result, dict):
            return result

        # Extract first response and convert BaseModel to dict
        responses = result.get("responses", [])
        if not responses:
            return result

        # Convert BaseModel to dict (creates the "objects" key)
        first_response = responses[0]
        structured_data = first_response
        if isinstance(first_response, BaseModel):
            structured_data = first_response.model_dump()
        # Extract the objects array (guaranteed to exist due to our Pydantic model structure)
        return structured_data.get("objects", structured_data)

    def _resolve_output_model(self) -> type[BaseModel]:
        """Resolve a connected ontology model, with table schema as fallback."""
        # `output_schema` is the public schema port. `ontology` remains as a
        # backwards-compatible alias for flows created with the earlier port.
        ontology = self.output_schema or self.ontology
        if isinstance(ontology, type) and issubclass(ontology, BaseModel):
            return ontology
        if ontology is not None:
            if isinstance(ontology, Data):
                ontology = ontology.data
            elif hasattr(ontology, "data") and isinstance(ontology.data, dict):
                ontology = ontology.data
            if isinstance(ontology, str):
                try:
                    ontology = json.loads(ontology)
                except json.JSONDecodeError:
                    ontology = None
            if isinstance(ontology, dict):
                model = ontology.get("model")
                if isinstance(model, type) and issubclass(model, BaseModel):
                    return model
                entity_types = ontology.get("entity_types", [])
                relation_types = ontology.get("relation_types", [])
                if entity_types or relation_types:
                    return self._build_generic_graph_model(entity_types, relation_types)

        if not self.output_schema:
            raise ValueError("Connect an ontology or provide an output schema")
        return build_model_from_schema(self.output_schema)

    @staticmethod
    def _build_generic_graph_model(entity_types: list[str], relation_types: list[str]) -> type[BaseModel]:
        entity_type = Literal[tuple(entity_types)] if entity_types else str
        relation_type = Literal[tuple(relation_types)] if relation_types else str
        entity = create_model(
            "OntologyEntity", id=(str, ...), type=(entity_type, ...), label=(str, ...),
            text=(str, ""), properties=(dict[str, Any], Field(default_factory=dict)),
            source_chunks=(list[str], Field(default_factory=list)),
        )
        relation = create_model(
            "OntologyRelation", subject=(str, ...), predicate=(relation_type, ...), object=(str, ...),
            properties=(dict[str, Any], Field(default_factory=dict)),
            source_chunks=(list[str], Field(default_factory=list)),
        )
        return create_model(
            "GenericOntologyGraph",
            entities=(list[entity], Field(default_factory=list)),
            relations=(list[relation], Field(default_factory=list)),
        )

    def build_structured_output(self) -> Data:
        output = self.build_structured_output_base()
        if not isinstance(output, list) or not output:
            # handle empty or unexpected type case
            msg = "No structured output returned"
            raise ValueError(msg)
        if len(output) == 1:
            return Data(data=output[0])
        if len(output) > 1:
            # Multiple outputs - wrap them in a results container
            return Data(data={"results": output})
        return Data()

    def build_structured_dataframe(self) -> DataFrame:
        output = self.build_structured_output_base()
        if not isinstance(output, list) or not output:
            # handle empty or unexpected type case
            msg = "No structured output returned"
            raise ValueError(msg)
        if len(output) == 1:
            # For single dictionary, wrap in a list to create DataFrame with one row
            return DataFrame([output[0]])
        if len(output) > 1:
            # Multiple outputs - convert to DataFrame directly
            return DataFrame(output)
        return DataFrame()

    def _extract_output_with_trustcall(self, llm, schema: BaseModel, config_dict: dict) -> list[BaseModel] | None:
        try:
            llm_with_structured_output = create_extractor(llm, tools=[schema], tool_choice=schema.__name__)
            result = get_chat_result(
                runnable=llm_with_structured_output,
                remediation_target=llm,
                system_message=self.system_prompt,
                input_value=self.input_value,
                config=config_dict,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Trustcall extraction failed, falling back to Langchain: {e} "
                "(Note: This may not be an error—some models or configurations do not support tool calling. "
                "Falling back is normal in such cases.)"
            )
            return None
        return result or None  # langchain fallback is used if error occurs or the result is empty

    def _extract_output_with_langchain(self, llm, schema: BaseModel, config_dict: dict) -> list[BaseModel] | None:
        try:
            llm_with_structured_output = llm.with_structured_output(schema)
            result = get_chat_result(
                runnable=llm_with_structured_output,
                remediation_target=llm,
                system_message=self.system_prompt,
                input_value=self.input_value,
                config=config_dict,
            )
            if isinstance(result, BaseModel):
                result = result.model_dump()
                result = result.get("objects", result)
        except Exception as fallback_error:
            msg = (
                f"Model does not support tool calling (trustcall failed) "
                f"and fallback with_structured_output also failed: {fallback_error}"
            )
            raise ValueError(msg) from fallback_error

        return result or None
