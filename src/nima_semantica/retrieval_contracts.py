"""Revision- and scope-bound contracts for derived GraphRAG projections."""

from __future__ import annotations

from .okf_contracts import GraphIdentity, GraphRevision

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .okf_contracts import GraphIdentifier


class EmbeddingProjectionManifest(StrictModel):
    """Identity of a rebuildable vector projection, never authoritative graph data."""

    schema_version: Literal[2] = 2
    projection_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    source_revision: GraphIdentifier
    provider: GraphIdentifier
    model: GraphIdentifier
    model_revision: GraphIdentifier
    dimension: int = Field(gt=0, le=100_000)
    metric: Literal["cosine", "dot", "l2"] = "cosine"
    indexed_artifact_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=1_000_000)

    @model_validator(mode="after")
    def unique_artifacts(self) -> "EmbeddingProjectionManifest":
        if len(self.indexed_artifact_ids) != len(set(self.indexed_artifact_ids)):
            raise ValueError("embedding projection contains duplicate artifact IDs")
        return self


class RetrievalSeed(StrictModel):
    record_id: GraphIdentifier
    score: float
    source: Literal["vector", "lexical", "graph"]


class RetrievalContextPacket(StrictModel):
    """Bounded context handed to a workflow, with reproducible retrieval identity."""

    schema_version: Literal[2] = 2
    query: str = Field(min_length=1, max_length=20_000)
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision
    projection: EmbeddingProjectionManifest
    seeds: tuple[RetrievalSeed, ...] = Field(default=(), max_length=100_000)
    selected_graph_refs: tuple[GraphIdentity, ...] = Field(default=(), max_length=100_000)
    selected_record_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=100_000)
    traversal: dict[str, Any] = Field(default_factory=dict, max_length=64)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=256)
    truncated: bool = False

    @model_validator(mode="after")
    def scope_matches_projection(self) -> "RetrievalContextPacket":
        if (self.graph_revision.corpus_id, self.graph_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("retrieval graph revision scope differs")
        if any(ref.corpus_id != self.corpus_id or ref.project_id not in (None, self.project_id) for ref in self.selected_graph_refs):
            raise ValueError("retrieval graph identity scope differs")
        if self.projection.corpus_id != self.corpus_id:
            raise ValueError("retrieval projection corpus scope differs from context packet")
        if self.projection.project_id not in (None, self.project_id):
            raise ValueError("retrieval projection project scope differs from context packet")
        return self


def validate_projection_revision(
    projection: EmbeddingProjectionManifest,
    *,
    corpus_id: str,
    project_id: str | None,
    source_revision: str,
) -> None:
    """Reject a vector projection that cannot answer the requested revision/scope."""
    if projection.corpus_id != corpus_id or projection.project_id not in (None, project_id):
        raise ValueError("embedding projection scope does not match retrieval scope")
    if projection.source_revision != source_revision:
        raise ValueError("embedding projection is stale for the requested source revision")


__all__ = [
    "EmbeddingProjectionManifest",
    "RetrievalContextPacket",
    "RetrievalSeed",
    "validate_projection_revision",
]
