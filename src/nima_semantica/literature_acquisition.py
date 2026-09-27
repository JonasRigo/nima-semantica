"""Framework-independent literature acquisition service."""

from __future__ import annotations

from asyncio import CancelledError
from urllib.parse import urlsplit

from pydantic import Field, StrictBool

from .acquisition import acquire
from .execution_receipts import ExecutionReceiptService
from .models import AcquisitionPolicy, NimaError, Record, StrictModel, canonical, identity
from .receipts import ExecutionReceipt


class LiteratureAcquisitionRequest(StrictModel):
    corpus_id: str = Field(default="default", pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    url: str = Field(min_length=1, max_length=8192)
    policy: AcquisitionPolicy
    motivating_gap: str = Field(default="", max_length=20_000)
    expected_revision: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")
    staging_only: StrictBool = False


class LiteratureAcquisitionResult(StrictModel):
    operation_id: str
    corpus_id: str
    project_id: str | None = None
    status: str
    result: dict = Field(default_factory=dict)
    diagnostics: tuple[dict, ...] = Field(default=())
    receipt_id: str


class LiteratureAcquisitionService:
    """Acquire one policy-approved source and retain its provenance."""

    stage = "literature_acquisition"

    def __init__(self, store, *, receipts: ExecutionReceiptService | None = None):
        self.store = store
        self.receipts = receipts or ExecutionReceiptService(store)

    def execute(self, request: LiteratureAcquisitionRequest) -> LiteratureAcquisitionResult:
        operation_id = request.idempotency_key or identity({
            "stage": self.stage,
            "request": request.model_dump(mode="json", exclude={"expected_revision", "idempotency_key"}),
        })
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        previous = self.receipts.replay(
            receipt_id, corpus_id=request.corpus_id, project_id=request.project_id, request_hash=identity(request)
        )
        if previous is not None:
            return LiteratureAcquisitionResult(
                operation_id=operation_id, corpus_id=request.corpus_id,
                project_id=request.project_id, status=previous.status,
                result=previous.metadata.get("result", {}),
                diagnostics=previous.diagnostics, receipt_id=receipt_id,
            )

        output_ids: tuple[str, ...] = ()
        interruption = None
        try:
            if not request.policy.enabled:
                raise NimaError("literature acquisition is disabled by policy")
            parsed = urlsplit(request.url)
            if (
                parsed.scheme not in ("http", "https")
                or parsed.hostname not in request.policy.domains
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise NimaError("acquisition target is outside approved domains")
            data, name, metadata = acquire(request.url, request.policy)
            if not data or len(data) > request.policy.max_response_bytes:
                raise NimaError("acquisition returned empty or oversized source")
            artifact = self.store.artifact(data)
            document = None if request.staging_only else Record(
                kind="Document", corpus_id=request.corpus_id, project_id=request.project_id,
                content={"artifact_id": artifact},
            )
            provenance = self.store.artifact(canonical({
                "url": request.url, "name": name, "metadata": metadata,
                "policy": request.policy.model_dump(mode="json"),
                "motivating_gap": request.motivating_gap, "artifact_id": artifact,
                "staging_only": request.staging_only,
            }))
            attempt = Record(
                kind="AcquisitionAttempt", corpus_id=request.corpus_id,
                project_id=request.project_id, parents=(document.id,) if document else (),
                content={"artifact_id": artifact, "name": name,
                         "provenance_artifact_id": provenance, "status": "completed",
                         "staging_only": request.staging_only},
            )
            with self.store.transaction(request.expected_revision):
                if document:
                    self.store.put(document)
                self.store.put(attempt)
            result = {
                "artifact_id": artifact, "name": name, "document_id": document.id if document else None,
                "acquisition_id": attempt.id, "provenance_artifact_id": provenance,
                "bytes": len(data), "metadata": metadata,
            }
            output_ids = (artifact, document.id, attempt.id, provenance) if document else (artifact, attempt.id, provenance)
            status, diagnostics, error = "completed", (), None
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            result = {}
            status = "failed"
            diagnostics = ({"code": type(exc).__name__},)
            error = "literature acquisition failed"

        execution_receipt = ExecutionReceipt(
            receipt_id=receipt_id, operation_id=operation_id, stage=self.stage,
            corpus_id=request.corpus_id, project_id=request.project_id,
            input_ids=(), output_ids=output_ids, status="interrupted" if interruption is not None else status, error=error,
            diagnostics=diagnostics, tool_version="guarded-acquisition-v1",
            idempotency_key=request.idempotency_key,
            metadata={"request_hash": identity(request), "result": result},
        )
        self.receipts.record(execution_receipt)
        if interruption is not None:
            raise interruption
        return LiteratureAcquisitionResult(
            operation_id=operation_id, corpus_id=request.corpus_id,
            project_id=request.project_id, status=status, result=result,
            diagnostics=diagnostics, receipt_id=receipt_id,
        )


__all__ = [
    "LiteratureAcquisitionRequest", "LiteratureAcquisitionResult",
    "LiteratureAcquisitionService",
]
