"""Corpus-scoped source registration and source-region persistence."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import Field

from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .models import ConflictError, NimaError, Record, StrictModel
from .okf_contracts import Digest, GraphIdentifier
from .storage import GraphStore


class SourceDescriptor(StrictModel):
    schema_version: Literal[1] = 1
    source_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    artifact_id: Digest
    source_revision: GraphIdentifier
    name: str = Field(min_length=1, max_length=2_000)
    media_type: str = Field(min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)


class SourceRegion(StrictModel):
    schema_version: Literal[1] = 1
    source_id: GraphIdentifier
    corpus_id: GraphIdentifier
    artifact_id: Digest
    source_artifact_id: Digest | None = None
    source_revision: GraphIdentifier
    project_id: GraphIdentifier | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)


class SourceCorpusService:
    """Own corpus source identity and exact source-region references."""

    SOURCE_KIND = "SourceDescriptor"
    REGION_KIND = "SourceRegion"

    def __init__(self, store: GraphStore, artifacts: ArtifactService | None = None):
        self.store = store
        self.artifacts = artifacts or ArtifactService(store)

    def register_source(
        self,
        data: bytes,
        descriptor: SourceDescriptor,
        envelope: ArtifactEnvelope,
        *,
        registry_revision: str,
        expected_store_revision: str | None = None,
    ) -> str:
        if expected_store_revision is not None and self.store.revision != expected_store_revision:
            raise ConflictError("stale graph revision")
        if descriptor.corpus_id != envelope.corpus_id or descriptor.artifact_id != envelope.artifact_id:
            raise ConflictError("source descriptor and artifact envelope scope or identity differs")
        if envelope.project_id != descriptor.project_id:
            raise ConflictError("source descriptor and artifact project differ")
        digest = hashlib.sha256(data).hexdigest()
        if digest != descriptor.artifact_id or digest != envelope.content_hash:
            raise ConflictError("source descriptor and artifact envelope hash does not match bytes")
        payload = descriptor.model_dump(mode="json")
        existing_artifact = self.artifacts.registry.resolve(envelope.artifact_id,
            corpus_id=descriptor.corpus_id, project_id=descriptor.project_id, exact_scope=True)
        if existing_artifact is not None:
            registry_revision = existing_artifact.registry_revision
        with self.store.joined_transaction(expected_store_revision):
            for record_id, record in self.store.records(self.SOURCE_KIND, corpus_id=descriptor.corpus_id):
                if record.content.get("source_id") != descriptor.source_id:
                    continue
                if SourceDescriptor.model_validate(record.content).model_dump(mode="json") != payload:
                    raise ConflictError("source ID is already bound to different metadata")
                self.artifacts.publish(data, envelope, registry_revision=registry_revision)
                return record_id
            self.artifacts.publish(data, envelope, registry_revision=registry_revision)
            return self.store.put(Record(
                kind=self.SOURCE_KIND, corpus_id=descriptor.corpus_id, project_id=descriptor.project_id, content=payload,
            ))

    def get_source(self, source_id: str, *, corpus_id: str, project_id: str | None = None) -> SourceDescriptor | None:
        for _, record in self.store.records(self.SOURCE_KIND, corpus_id=corpus_id):
            if record.content.get("source_id") == source_id and record.project_id in (None, project_id):
                return SourceDescriptor.model_validate(record.content)
        return None

    def register_region(
        self,
        region: SourceRegion,
        *,
        parent_ids: tuple[GraphIdentifier, ...] = (),
        expected_store_revision: str | None = None,
    ) -> str:
        source = self.get_source(region.source_id, corpus_id=region.corpus_id, project_id=region.project_id)
        if source is None:
            raise NimaError("source region references an unknown source")
        if region.metadata.get("preparation_quality", "full") != source.metadata.get("preparation_quality", "full"):
            raise ConflictError("source region must preserve preparation quality")
        if source.project_id is not None and region.project_id != source.project_id:
            raise ConflictError("source region must preserve source visibility")
        source_artifact_id = region.source_artifact_id or region.artifact_id
        if source_artifact_id != source.artifact_id or region.source_revision != source.source_revision:
            raise ConflictError("source region revision or artifact differs from source descriptor")
        data = self.store.read_artifact(region.artifact_id)
        if hashlib.sha256(data).hexdigest() != region.artifact_id:
            raise NimaError("source artifact hash validation failed")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NimaError("source regions require UTF-8 source artifacts") from exc
        if region.end < region.start or region.end > len(text) or text[region.start:region.end] != region.text:
            raise ConflictError("source region offsets or text do not match the immutable source")
        record = Record(
            kind=self.REGION_KIND, corpus_id=region.corpus_id,
            project_id=region.project_id, parents=(source.source_id, *parent_ids),
            content=region.model_dump(mode="json"),
        )
        with self.store.joined_transaction(expected_store_revision):
            existing = self.store.get(record.id, corpus_id=region.corpus_id, project_id=region.project_id)
            if existing is not None:
                if existing != record:
                    raise ConflictError("source region identity is already bound to different content")
                return record.id
            return self.store.put(record)

    def get_region(
        self,
        region_id: str,
        *,
        corpus_id: str,
        project_id: str | None = None,
    ) -> SourceRegion | None:
        record = self.store.get(region_id, corpus_id=corpus_id, project_id=project_id)
        if record is None or record.kind != self.REGION_KIND or record.project_id not in (None, project_id):
            return None
        return SourceRegion.model_validate(record.content)


__all__ = ["SourceCorpusService", "SourceDescriptor", "SourceRegion"]
