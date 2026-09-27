"""Framework-independent document normalization and region ingestion."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import Field

from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .corpus import normalize, regions
from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, NimaError, Record, StrictModel, identity
from .receipts import ExecutionReceipt
from .source_corpus import SourceCorpusService, SourceDescriptor, SourceRegion
from .storage import GraphStore


class DocumentIngestionResult(StrictModel):
    schema_version: int = 1
    source_id: str
    corpus_id: str
    document_record_id: str
    normalized_artifact_id: str
    region_ids: tuple[str, ...] = Field(default=(), max_length=100_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    receipt_id: str


class DocumentIngestionService:
    """Normalize a source and persist provenance-bearing document regions."""

    def __init__(
        self,
        store: GraphStore,
        sources: SourceCorpusService | None = None,
        artifacts: ArtifactService | None = None,
        receipts: ExecutionReceiptService | None = None,
    ):
        self.store = store
        self.artifacts = artifacts or ArtifactService(store)
        self.sources = sources or SourceCorpusService(store, self.artifacts)
        self.receipts = receipts or ExecutionReceiptService(store)

    def ingest(
        self,
        data: bytes,
        descriptor: SourceDescriptor,
        envelope: ArtifactEnvelope,
        *,
        registry_revision: str,
        pdf_normalizer=None,
        expected_store_revision: str | None = None,
    ) -> DocumentIngestionResult:
        self.sources.register_source(
            data, descriptor, envelope,
            registry_revision=registry_revision,
            expected_store_revision=expected_store_revision,
        )
        text, diagnostics = normalize(data, descriptor.name, pdf_normalizer)
        normalized_bytes = text.encode("utf-8")
        normalized_id = hashlib.sha256(normalized_bytes).hexdigest()
        normalized_envelope = ArtifactEnvelope(
            artifact_id=normalized_id,
            artifact_kind="normalized_source",
            media_type="text/plain",
            content_hash=normalized_id,
            corpus_id=descriptor.corpus_id,
            project_id=descriptor.project_id,
            source_revision=descriptor.source_revision,
            content={"policy": "nima-normalize-v1", "source_id": descriptor.source_id},
            source_artifact_ids=(descriptor.artifact_id,),
        )
        if normalized_id != descriptor.artifact_id:
            self.artifacts.publish(
                normalized_bytes, normalized_envelope, registry_revision=registry_revision,
            )

        document = Record(
            kind="Document", corpus_id=descriptor.corpus_id, project_id=descriptor.project_id,
            content={"source_id": descriptor.source_id, "artifact_id": descriptor.artifact_id,
                     "name": descriptor.name},
        )
        normalized = Record(
            kind="NormalizedDocument", corpus_id=descriptor.corpus_id, project_id=descriptor.project_id,
            parents=(document.id,),
            content={"artifact_id": normalized_id, "policy": "nima-normalize-v1",
                     "diagnostics": diagnostics},
        )
        selected = regions(text, normalized_id, document.id, descriptor.corpus_id, project_id=descriptor.project_id)
        if not selected and text.strip():
            raise NimaError("source contains no indexable regions")
        with self.store.joined_transaction():
            document_record_id = self._put_idempotent(document)
            self._put_idempotent(normalized)
        region_ids = []
        for item in selected:
            region = SourceRegion(
                source_id=descriptor.source_id,
                corpus_id=descriptor.corpus_id,
                project_id=descriptor.project_id,
                artifact_id=normalized_id,
                source_artifact_id=descriptor.artifact_id,
                source_revision=descriptor.source_revision,
                start=item.content["start"], end=item.content["end"],
                ordinal=item.content["ordinal"], text=item.content["text"],
                metadata={"normalization_diagnostics": diagnostics},
            )
            region_ids.append(self.sources.register_region(region, parent_ids=(document_record_id,)))

        receipt_payload = {
            "operation_id": f"document-ingestion:{descriptor.source_id}:{descriptor.source_revision}",
            "source_id": descriptor.source_id,
            "normalized_artifact_id": normalized_id,
            "region_ids": region_ids,
        }
        receipt_id = identity(receipt_payload)
        execution_receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=receipt_payload["operation_id"],
            stage="document_ingestion",
            corpus_id=descriptor.corpus_id,
            project_id=descriptor.project_id,
            input_ids=(descriptor.artifact_id,),
            output_ids=(normalized_id, *region_ids),
            source_revision=descriptor.source_revision,
            status="completed",
            diagnostics=tuple(diagnostics),
            metadata={"source_id": descriptor.source_id},
        )
        self.receipts.record(execution_receipt)
        return DocumentIngestionResult(
            source_id=descriptor.source_id,
            corpus_id=descriptor.corpus_id,
            document_record_id=document_record_id,
            normalized_artifact_id=normalized_id,
            region_ids=tuple(region_ids),
            diagnostics=tuple(diagnostics),
            receipt_id=receipt_id,
        )

    def _put_idempotent(self, record: Record) -> str:
        existing = self.store.get(record.id, corpus_id=record.corpus_id, project_id=record.project_id)
        if existing is not None and existing != record:
            raise ConflictError("document ingestion record identity collision")
        return self.store.put(record)


__all__ = ["DocumentIngestionResult", "DocumentIngestionService"]
