"""Universal artifact envelopes and graph-artifact bindings.

Artifacts are control-plane records.  They carry typed content and provenance;
they do not replace the project OKF graph or the immutable byte store.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .okf_contracts import (
    Digest,
    GraphIdentifier,
    OKFDelta,
    OKFSnapshot,
)


class ArtifactEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    artifact_id: Digest
    artifact_kind: GraphIdentifier
    media_type: str = Field(min_length=1, max_length=256)
    content_hash: Digest
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    source_revision: GraphIdentifier | None = None
    content: dict[str, Any] = Field(default_factory=dict, max_length=256)
    source_artifact_ids: tuple[Digest, ...] = Field(default=(), max_length=10_000)
    provenance: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    status: Literal["proposed", "available", "superseded", "failed"] = "available"

    @model_validator(mode="after")
    def unique_links(self) -> "ArtifactEnvelope":
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise ValueError("artifact envelope contains duplicate source artifacts")
        if len(self.provenance) != len(set(self.provenance)):
            raise ValueError("artifact envelope contains duplicate provenance IDs")
        return self


class GraphArtifact(StrictModel):
    """An artifact envelope containing exactly one OKF graph representation."""

    schema_version: Literal[1] = 1
    envelope: ArtifactEnvelope
    snapshot: OKFSnapshot | None = None
    delta: OKFDelta | None = None

    @model_validator(mode="after")
    def exactly_one_graph_payload(self) -> "GraphArtifact":
        if (self.snapshot is None) == (self.delta is None):
            raise ValueError("graph artifact must contain exactly one snapshot or delta")
        graph = self.snapshot or self.delta
        if graph.corpus_id != self.envelope.corpus_id:
            raise ValueError("graph artifact corpus scope differs from envelope")
        graph_project = graph.project_id
        if graph_project not in (None, self.envelope.project_id):
            raise ValueError("graph artifact project scope differs from envelope")
        return self


__all__ = ["ArtifactEnvelope", "GraphArtifact"]
