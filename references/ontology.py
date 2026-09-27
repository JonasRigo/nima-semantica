"""Ontology definitions and the Langflow ontology selector component."""

from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MultilineInput, Output
from lfx.schema.data import Data


def mathematical_argument_graph() -> type[BaseModel]:
    class ClaimComponent(BaseModel):
        component_id: str = Field(pattern=r"^c[0-9]+$")
        label: str
        statement: str
        role: Literal["headline", "necessary_component", "local_result", "imported_result"]
        derivation_level: Literal["none", "asserted", "informal", "detailed", "conditional", "cited"]
        source_chunk_ids: list[str] = Field(min_length=1)
        equation_tags: list[str] = Field(default_factory=list)

    class ProofObligation(BaseModel):
        obligation_id: str = Field(pattern=r"^o[0-9]+$")
        label: str
        description: str
        required_by_component_ids: list[str] = Field(min_length=1)
        status: Literal["discharged", "partially_discharged", "open", "contradicted"]
        supporting_component_ids: list[str] = Field(default_factory=list)
        source_chunk_ids: list[str] = Field(default_factory=list)
        explanation: str

    class ArgumentRelation(BaseModel):
        subject_id: str
        predicate: Literal["depends_on", "supports", "contradicts", "imports", "specializes", "equivalent_to"]
        object_id: str

    class ImportedDocument(BaseModel):
        document_id: str = Field(pattern=r"^d[0-9]+$")
        citation: str
        source_chunk_ids: list[str] = Field(min_length=1)

    return create_model(
        "MathematicalArgumentGraph",
        components=(list[ClaimComponent], Field(min_length=1)),
        obligations=(list[ProofObligation], Field(default_factory=list)),
        relations=(list[ArgumentRelation], Field(default_factory=list)),
        imported_documents=(list[ImportedDocument], Field(default_factory=list)),
        scope_complete=(bool, ...),
        scope_note=(str, ...),
    )


def regional_argument_model() -> type[BaseModel]:
    class RegionalClaim(BaseModel):
        id: str = ""
        label: str
        role: str
        status: str
        explanation: str = ""
        source_chunks: list[str] = Field(default_factory=list)
        equation_tags: list[str] = Field(default_factory=list)

    class RegionalObligation(BaseModel):
        id: str = ""
        label: str = ""
        description: str
        required_by: list[str] = Field(default_factory=list)
        status: str
        source_chunks: list[str] = Field(default_factory=list)
        explanation: str = ""

    class RegionalRelation(BaseModel):
        subject: str
        predicate: str
        object: str

    class RegionalDocument(BaseModel):
        id: str = ""
        label: str = ""
        citation: str = ""
        source_chunks: list[str] = Field(default_factory=list)

    return create_model(
        "RegionalArgument",
        claims=(list[RegionalClaim], Field(default_factory=list, max_length=8)),
        obligations=(list[RegionalObligation], Field(default_factory=list, max_length=10)),
        relations=(list[RegionalRelation], Field(default_factory=list, max_length=12)),
        imported_documents=(list[RegionalDocument], Field(default_factory=list, max_length=8)),
    )


def generic_graph_model(entity_types: list[str], relation_types: list[str]) -> type[BaseModel]:
    """Create a generic graph model for a user-defined ontology."""
    entity_type = Literal[tuple(entity_types)] if entity_types else str
    relation_type = Literal[tuple(relation_types)] if relation_types else str

    Entity = create_model(
        "OntologyEntity",
        id=(str, ...), type=(entity_type, ...), label=(str, ...), text=(str, ""),
        properties=(dict[str, Any], Field(default_factory=dict)),
        source_chunks=(list[str], Field(default_factory=list)),
    )
    Relation = create_model(
        "OntologyRelation",
        subject=(str, ...), predicate=(relation_type, ...), object=(str, ...),
        properties=(dict[str, Any], Field(default_factory=dict)),
        source_chunks=(list[str], Field(default_factory=list)),
    )
    return create_model(
        "GenericOntologyGraph",
        entities=(list[Entity], Field(default_factory=list)),
        relations=(list[Relation], Field(default_factory=list)),
    )


BUILTIN_ONTOLOGIES = {
    "regional_argument": regional_argument_model,
    "mathematical_argument_graph": mathematical_argument_graph,
}


class OntologyManager(Component):
    """Select a built-in Pydantic ontology or define a generic one."""

    display_name = "Ontology Manager"
    description = "Select a built-in Pydantic ontology or define entity and relation types."
    icon = "DataframeIcon"
    name = "ontology_manager"

    inputs = [
        DropdownInput(
            name="ontology_selection", display_name="Select Ontology",
            options=[*BUILTIN_ONTOLOGIES, "custom"], value="regional_argument",
            real_time_refresh=True,
        ),
        MultilineInput(
            name="entity_types", display_name="ENTITY_TYPES",
            info="Comma- or newline-separated entity labels for a custom ontology.",
            value="DOCUMENT\nCLAIM\nFORMULA", advanced=True,
        ),
        MultilineInput(
            name="relation_types", display_name="RELATION_TYPES",
            info="Comma- or newline-separated relation labels for a custom ontology.",
            value="supports\ndepends_on\ndefines", advanced=True,
        ),
    ]
    outputs = [Output(name="ontology", display_name="Ontology Schema", method="build_ont")]

    @staticmethod
    def _parse_types(value: str | list[str] | None) -> list[str]:
        values = value if isinstance(value, list) else (value or "").replace(",", "\n").splitlines()
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    def build_ont(self) -> Data:
        selected = self.ontology_selection or "regional_argument"
        entity_types = self._parse_types(self.entity_types)
        relation_types = self._parse_types(self.relation_types)
        if selected == "custom":
            model = generic_graph_model(entity_types, relation_types)
        else:
            factory = BUILTIN_ONTOLOGIES.get(selected)
            if factory is None:
                raise ValueError(f"Unknown ontology: {selected}")
            model = factory()

        descriptor = {
            "name": model.__name__, "ontology": selected,
            "entity_types": entity_types, "relation_types": relation_types,
            "json_schema": model.model_json_schema(), "model": model,
        }
        self.status = f"Built ontology: {model.__name__}"
        return Data(data=descriptor)
