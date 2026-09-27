"""Framework-independent embedding index publication service."""

from __future__ import annotations

from asyncio import CancelledError
from typing import Any

from pydantic import Field, FiniteFloat

from .execution_receipts import ExecutionReceiptService
from .evidence_contracts import require_source_region
from .models import ConfigurationError, NimaError, StrictModel, identity
from .embedding_publication import SourceIndex
from .providers import ModelManifest
from .receipts import ExecutionReceipt


class EmbeddingIndexRequest(StrictModel):
    corpus_id: str = Field(default="default", pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    region_ids: tuple[str, ...] = Field(min_length=1, max_length=256)
    manifest: ModelManifest
    expected_revision: str | None = Field(default=None, max_length=256)
    profile: str = Field(default="", max_length=256)
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")


class EmbeddingIndexResult(StrictModel):
    operation_id: str
    corpus_id: str
    project_id: str | None = None
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_id: str


class EmbeddingIndexService:
    """Validate and publish immutable region embeddings with model identity."""

    stage = "embedding_index"

    def __init__(self, store, *, receipts: ExecutionReceiptService | None = None):
        self.store = store
        self.receipts = receipts or ExecutionReceiptService(store)

    def index(
        self, request: EmbeddingIndexRequest, vectors: tuple[tuple[FiniteFloat, ...], ...] | list[list[float]]
    ) -> EmbeddingIndexResult:
        return self._execute(request, vectors=vectors)

    def _execute(self, request, *, vectors=None, provider=None):
        request_hash = identity({"request": request, "vectors": vectors, "embed": provider is not None})
        operation_id = request.idempotency_key or identity({
            "stage": self.stage, "request": request.model_dump(mode="json", exclude={"expected_revision", "idempotency_key"}),
            "vectors": vectors,
        })
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        previous = self.receipts.replay(
            receipt_id, corpus_id=request.corpus_id, project_id=request.project_id, request_hash=request_hash
        )
        if previous is not None:
            return EmbeddingIndexResult(
                operation_id=operation_id, corpus_id=request.corpus_id,
                project_id=request.project_id, status=previous.status,
                result=previous.metadata.get("result", {}),
                diagnostics=previous.diagnostics, receipt_id=receipt_id,
            )

        interruption = None
        try:
            records = [require_source_region(self.store, region_id, corpus_id=request.corpus_id,
                project_id=request.project_id) for region_id in request.region_ids]
            if provider is not None:
                if request.expected_revision is not None and self.store.revision != request.expected_revision:
                    raise NimaError("embedding request targets a stale store revision")
                vectors, manifest = provider.embed(request.profile, [record.content["text"] for record in records])
                if manifest != request.manifest:
                    raise ConfigurationError("embedding provider manifest differs from request")
            payload = {
                "corpus_id": request.corpus_id, "project_id": request.project_id,
                "region_ids": list(request.region_ids), "vectors": vectors,
                "manifest": request.manifest.model_dump(mode="json"),
                "expected_revision": request.expected_revision,
            }
            result = SourceIndex(payload, self.store)
            status, diagnostics, error = "completed", (), None
            output_ids = tuple(
                value for value in (
                    result.get("embedding_batch_id"), result.get("matrix_artifact_id"),
                    *result.get("region_ids", ()),
                ) if value
            )
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            result = {}
            output_ids = ()
            status = "failed"
            diagnostics = ({"code": type(exc).__name__},)
            error = "embedding index publication failed"

        execution_receipt = ExecutionReceipt(
            receipt_id=receipt_id, operation_id=operation_id, stage=self.stage,
            corpus_id=request.corpus_id, project_id=request.project_id,
            input_ids=request.region_ids, output_ids=output_ids, status="interrupted" if interruption is not None else status,
            error=error, diagnostics=diagnostics,
            provider=request.manifest.provider, model=request.manifest.model,
            tool_version="embedding-index-v1", idempotency_key=request.idempotency_key,
            metadata={"request_hash": request_hash, "manifest": request.manifest.model_dump(mode="json"), "result": result},
        )
        self.receipts.record(execution_receipt)
        if interruption is not None:
            raise interruption
        return EmbeddingIndexResult(
            operation_id=operation_id, corpus_id=request.corpus_id,
            project_id=request.project_id, status=status, result=result,
            diagnostics=diagnostics, receipt_id=receipt_id,
        )

    def embed_and_index(self, request: EmbeddingIndexRequest, provider) -> EmbeddingIndexResult:
        """Include provider failures and interruption in the index attempt receipt."""
        return self._execute(request, provider=provider)


__all__ = ["EmbeddingIndexRequest", "EmbeddingIndexResult", "EmbeddingIndexService"]
