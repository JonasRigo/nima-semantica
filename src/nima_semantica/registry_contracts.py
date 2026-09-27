"""Persistent corpus-wide system registry contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .okf_contracts import Digest, GraphIdentifier


class RegistryResourceKind(StrEnum):
    CORPUS = "corpus"
    ARTIFACT = "artifact"
    SOURCE_REGION = "source_region"
    ONTOLOGY_PROFILE = "ontology_profile"
    GRAPH_SNAPSHOT = "graph_snapshot"
    GRAPH_DELTA = "graph_delta"
    PROJECTION = "projection"
    RESEARCH_RUN = "research_run"
    REPORT = "report"
    EXPERIMENT = "experiment"
    VERIFICATION = "verification"


class RegistryStatus(StrEnum):
    PROPOSED = "proposed"
    AVAILABLE = "available"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    REVOKED = "revoked"


class CorpusDescriptor(StrictModel):
    schema_version: Literal[1] = 1
    corpus_id: GraphIdentifier
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4_000)
    corpus_revision: GraphIdentifier
    owner: GraphIdentifier | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)


class RegistryRevision(StrictModel):
    schema_version: Literal[1] = 1
    revision_id: GraphIdentifier
    corpus_id: GraphIdentifier
    sequence: int = Field(ge=0)
    parent_revision: GraphIdentifier | None = None
    changed_resource_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=100_000)

    @model_validator(mode="after")
    def unique_resources(self) -> "RegistryRevision":
        if len(self.changed_resource_ids) != len(set(self.changed_resource_ids)):
            raise ValueError("registry revision contains duplicate resource IDs")
        return self


class RegistryEntry(StrictModel):
    schema_version: Literal[1] = 1
    resource_id: GraphIdentifier
    resource_kind: RegistryResourceKind
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    content_hash: Digest
    source_revision: GraphIdentifier | None = None
    registry_revision: GraphIdentifier
    artifact_id: Digest | None = None
    status: RegistryStatus = RegistryStatus.AVAILABLE
    parents: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)

    @model_validator(mode="after")
    def unique_links(self) -> "RegistryEntry":
        if len(self.parents) != len(set(self.parents)):
            raise ValueError("registry entry contains duplicate parents")
        if len(self.provenance) != len(set(self.provenance)):
            raise ValueError("registry entry contains duplicate provenance IDs")
        return self


__all__ = ["CorpusDescriptor", "RegistryEntry", "RegistryResourceKind", "RegistryRevision", "RegistryStatus"]
