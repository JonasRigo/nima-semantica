"""Framework-independent contracts for adapting user-owned flows.

Normalization translates external flow I/O into NIMA-shaped proposals. It is
deliberately non-mutating: a normalized output may carry an ``OKFDelta``, but
only a separate approval and graph-commit operation can apply that delta.
"""

from __future__ import annotations

from .okf_contracts import GraphRevision

from enum import StrEnum
from typing import Any, Annotated, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .okf_contracts import Digest, GraphIdentifier, OKFDelta


AdapterPath = Annotated[str, Field(min_length=1, max_length=512)]


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NormalizationDiagnostic(StrictModel):
    """A visible issue or fact produced while adapting flow input/output."""

    diagnostic_id: GraphIdentifier
    code: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")]
    severity: DiagnosticSeverity
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    path: AdapterPath | None = None
    source: Annotated[str, Field(min_length=1, max_length=256)] = "normalizer"
    artifact_id: GraphIdentifier | None = None


class NormalizationStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class FieldMapping(StrictModel):
    """A declared source-to-target conversion performed by an adapter."""

    source_path: AdapterPath
    target_path: AdapterPath
    required: bool = False
    lossy: bool = False
    transform: str | None = Field(default=None, min_length=1, max_length=256)


class FlowAdapterManifest(StrictModel):
    """Capabilities and mappings for one imported user flow."""

    schema_version: Literal[2] = 2
    adapter_id: GraphIdentifier
    name: Annotated[str, Field(min_length=1, max_length=256)]
    version: Annotated[str, Field(min_length=1, max_length=128)]
    input_mappings: tuple[FieldMapping, ...] = Field(default=(), max_length=256)
    output_mappings: tuple[FieldMapping, ...] = Field(default=(), max_length=256)
    input_media_types: tuple[str, ...] = Field(default=(), max_length=64)
    output_media_types: tuple[str, ...] = Field(default=(), max_length=64)
    authority: Literal["proposal_only"] = "proposal_only"

    @model_validator(mode="after")
    def unique_targets(self) -> "FlowAdapterManifest":
        for mappings, label in (
            (self.input_mappings, "input"),
            (self.output_mappings, "output"),
        ):
            targets = [mapping.target_path for mapping in mappings]
            if len(targets) != len(set(targets)):
                raise ValueError(f"duplicate {label} mapping target paths")
        return self


class NormalizationArtifact(StrictModel):
    """A raw or generated artifact referenced by normalization, not embedded."""

    artifact_id: GraphIdentifier
    media_type: Annotated[str, Field(min_length=1, max_length=256)]
    content_hash: Digest
    locator: Annotated[str, Field(min_length=1, max_length=2_048)]
    role: Annotated[str, Field(min_length=1, max_length=128)]
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)


class NormalizedProposal(StrictModel):
    """A typed, reviewable proposal produced by a normalizer."""

    proposal_id: GraphIdentifier
    kind: Literal[
        "record",
        "node",
        "edge",
        "hypothesis",
        "finding",
        "obligation",
        "artifact",
    ]
    payload: dict[str, Any] = Field(default_factory=dict, max_length=128)
    evidence: tuple[NormalizationArtifact, ...] = Field(default=(), max_length=256)


class NormalizedRequest(StrictModel):
    """The scoped, provenance-bearing input envelope sent to an adapter."""

    schema_version: Literal[2] = 2
    request_id: GraphIdentifier
    adapter_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    objective: str | None = Field(default=None, max_length=4_000)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=256)
    raw_inputs: tuple[NormalizationArtifact, ...] = Field(default=(), max_length=256)
    ontology_profile: GraphIdentifier | None = None

    @model_validator(mode="after")
    def unique_artifacts(self) -> "NormalizedRequest":
        ids = [artifact.artifact_id for artifact in self.raw_inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("normalized request contains duplicate raw artifact IDs")
        return self


class NormalizedOutput(StrictModel):
    """An adapter result; it cannot represent approval or a graph commit."""

    schema_version: Literal[2] = 2
    request_id: GraphIdentifier
    adapter_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    status: NormalizationStatus
    proposals: tuple[NormalizedProposal, ...] = Field(default=(), max_length=100_000)
    graph_delta: OKFDelta | None = None
    artifacts: tuple[NormalizationArtifact, ...] = Field(default=(), max_length=256)
    diagnostics: tuple[NormalizationDiagnostic, ...] = Field(default=(), max_length=1_024)
    authority: Literal["proposal_only"] = "proposal_only"
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def validate_scope_and_references(self) -> "NormalizedOutput":
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("normalized output contains duplicate artifact IDs")
        diagnostic_ids = [diagnostic.diagnostic_id for diagnostic in self.diagnostics]
        if len(diagnostic_ids) != len(set(diagnostic_ids)):
            raise ValueError("normalized output contains duplicate diagnostic IDs")
        if self.graph_delta is not None:
            if self.graph_delta.corpus_id != self.corpus_id:
                raise ValueError("normalized graph delta corpus scope differs from output")
            if self.graph_delta.project_id not in (None, self.project_id):
                raise ValueError("normalized graph delta project scope differs from output")
            if self.graph_revision is not None and self.graph_delta.base_revision != self.graph_revision:
                raise ValueError("normalized graph delta revision differs from output")
        return self


__all__ = [
    "AdapterPath",
    "DiagnosticSeverity",
    "FieldMapping",
    "FlowAdapterManifest",
    "NormalizationArtifact",
    "NormalizationDiagnostic",
    "NormalizationStatus",
    "NormalizedOutput",
    "NormalizedProposal",
    "NormalizedRequest",
]
