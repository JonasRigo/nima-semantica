"""Immutable ontology vocabulary contracts and packaged profiles."""
from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel, NimaError, identity
from .reasoning_kernel import Predicate, Symbol

class NodeType(StrictModel):
    name: Symbol
    description: str = Field(max_length=2000)
    role: Literal["statement", "obligation", "definition", "source"] = "statement"


class RelationType(StrictModel):
    name: Symbol
    source_types: tuple[Symbol, ...] = Field(min_length=1)
    target_types: tuple[Symbol, ...] = Field(min_length=1)
    description: str = Field(max_length=2000)
    necessary_dependency: bool = False


class OntologyProfile(StrictModel):
    schema_version: Literal[1] = 1
    name: Symbol
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    node_types: tuple[NodeType, ...] = Field(min_length=1, max_length=64)
    relation_types: tuple[RelationType, ...] = Field(default=(), max_length=64)
    predicates: tuple[Predicate, ...] = Field(default=(), max_length=64)
    instructions: str = Field(default="", max_length=10000)
    required_node_types: tuple[Symbol, ...] = ()
    required_relation_types: tuple[Symbol, ...] = ()

    @model_validator(mode="after")
    def vocabulary(self):
        names = [n.name for n in self.node_types]
        relations = [r.name for r in self.relation_types]
        predicates = [p.name for p in self.predicates]
        if any(len({name.casefold() for name in group}) != len(group) for group in (names, relations, predicates)):
            raise ValueError("duplicate ontology vocabulary")
        if any(not set((*r.source_types, *r.target_types)) <= set(names) for r in self.relation_types):
            raise ValueError("relation references an undeclared node type")
        if any(p.startswith(("Node_", "Edge_")) for p in predicates):
            raise ValueError("Node_ and Edge_ are reserved translation predicates")
        if not set(self.required_node_types) <= set(names) or not set(self.required_relation_types) <= set(relations):
            raise ValueError("coverage requirements reference undeclared vocabulary")
        return self

    @property
    def digest(self):
        return identity(self)


def saved_profile(name="claim_obligation") -> OntologyProfile:
    """Immutable packaged profiles; custom profiles use the same JSON contract."""
    from importlib.resources import files
    if name not in ("claim_obligation", "theorem_dependencies", "literature_evidence", "literature_review", "beyond_iid_mathematics"):
        raise NimaError("unknown saved ontology profile")
    return OntologyProfile.model_validate_json(files("nima_semantica.profiles").joinpath(name + ".json").read_text())
