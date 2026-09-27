"""Framework-independent bounded verification service."""

from __future__ import annotations

from asyncio import CancelledError
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from .calculation import SymbolicCheckInput, SymbolicWorker
from .execution_receipts import ExecutionReceiptService
from .evidence_contracts import require_source_region
from .okf_contracts import GraphRevision
from .models import ConflictError, StrictModel, VerificationResult, canonical, identity
from .receipts import ExecutionReceipt
from .scalar import ScalarCheck, check_boundaries
from .verification import PolynomialClaim, exact_counterexample


class VerificationRequest(StrictModel):
    operation: Literal["exact_counterexample", "scalar_boundary", "symbolic_check"]
    corpus_id: str = Field(min_length=1, pattern=r"^\S+$")
    project_id: str | None = Field(default=None, pattern=r"^\S+$")
    graph_revision: GraphRevision | None = None
    source_revision: str | None = Field(default=None, max_length=256)
    claim: PolynomialClaim | None = None
    witness: dict[str, StrictInt] | None = None
    boundary: ScalarCheck | None = None
    symbolic: SymbolicCheckInput | None = None
    run_id: str | None = Field(default=None, pattern=r"^\S+$")
    input_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    operation_id: str = Field(default="", pattern=r"^\S{0,256}$")
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")

    @model_validator(mode="after")
    def operation_payload(self) -> "VerificationRequest":
        supplied = {
            "exact_counterexample": self.claim is not None and self.witness is not None,
            "scalar_boundary": self.boundary is not None,
            "symbolic_check": self.symbolic is not None,
        }
        if not supplied[self.operation]:
            raise ValueError(f"{self.operation} requires its typed payload")
        return self


class VerificationServiceResult(StrictModel):
    operation_id: str
    corpus_id: str
    project_id: str | None = None
    status: Literal["completed", "failed"]
    verification: VerificationResult | None = None
    raw_result: dict[str, Any] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_id: str
    authority: Literal["verification_result"] = "verification_result"


class VerificationService:
    """Run bounded checks and preserve their deliberately limited claims."""

    stage = "verification"

    def __init__(self, store, *, receipts: ExecutionReceiptService | None = None, symbolic_worker=None):
        self.store = store
        self.receipts = receipts or ExecutionReceiptService(store)
        self.symbolic_worker = symbolic_worker or SymbolicWorker()

    def execute(self, request: VerificationRequest) -> VerificationServiceResult:
        operation_id = request.operation_id or identity({
            "stage": self.stage,
            "request": request.model_dump(mode="json", exclude={"operation_id", "idempotency_key"}),
        })
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        request_hash = identity(request)
        previous = self.receipts.replay(
            receipt_id, corpus_id=request.corpus_id, project_id=request.project_id, request_hash=request_hash
        )
        if previous is not None:
            return VerificationServiceResult.model_validate(previous.metadata["result"])

        status: Literal["completed", "failed"] = "failed"
        verification = None
        raw_result: dict[str, Any] = {}
        diagnostics: tuple[dict[str, Any], ...] = ()
        error = None
        interruption = None
        try:
            if request.graph_revision is not None and self.store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
                raise ConflictError("verification request targets a stale graph revision")
            if request.operation == "exact_counterexample":
                verification = exact_counterexample(self.store, request.claim, request.witness)
                raw_result = verification.model_dump(mode="json")
            elif request.operation == "scalar_boundary":
                raw_result = check_boundaries(request.boundary)
                artifact_id = self.store.artifact(canonical(raw_result))
                outcome = "refuted" if raw_result["outcome"] == "counterexample_to_encoded_statement" else "inconclusive"
                verification = VerificationResult(
                    target_id=identity(request.boundary),
                    protocol="scalar-boundary-sampling-v1",
                    outcome=outcome,
                    scope=raw_result["scope"],
                    evidence_artifact=artifact_id,
                )
                raw_result = {**raw_result, "verification": verification.model_dump(mode="json")}
            else:
                if (request.symbolic.task.corpus_id, request.symbolic.task.project_id) != (request.corpus_id, request.project_id):
                    raise ConflictError("symbolic verification scope differs from request")
                for region_id in request.symbolic.task.context_region_ids:
                    require_source_region(self.store, region_id, corpus_id=request.corpus_id, project_id=request.project_id)
                raw_result = self.symbolic_worker.check(request.symbolic)
                artifact_id = self.store.artifact(canonical(raw_result))
                verification = VerificationResult(
                    target_id=identity(request.symbolic.task),
                    protocol="symbolic-check-v1",
                    outcome="refuted" if raw_result.get("outcome") == "check_failed" else "inconclusive",
                    scope=raw_result.get("scope", "encoded symbolic expression only"),
                    evidence_artifact=artifact_id,
                    diagnostics=("symbolic checking does not establish the source claim",),
                )
                raw_result = {**raw_result, "verification": verification.model_dump(mode="json")}
            status = "completed"
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            diagnostics = ({"code": type(exc).__name__},)
            error = "verification failed"

        output_ids = (verification.evidence_artifact,) if verification and verification.evidence_artifact else ()
        result = VerificationServiceResult(
            operation_id=operation_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            status=status,
            verification=verification,
            raw_result=raw_result,
            diagnostics=diagnostics,
            receipt_id=receipt_id,
        )
        self.receipts.record(ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=operation_id,
            stage=self.stage,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            run_id=request.run_id,
            graph_revision=request.graph_revision,
            source_revision=request.source_revision,
            input_ids=request.input_ids,
            output_ids=output_ids,
            status="interrupted" if interruption is not None else status,
            error=error,
            diagnostics=diagnostics,
            tool_version="verification-service-v1",
            idempotency_key=request.idempotency_key,
            metadata={"operation": request.operation, "request_hash": request_hash, "result": result.model_dump(mode="json")},
        ))
        if interruption is not None:
            raise interruption
        return result


__all__ = ["VerificationRequest", "VerificationService", "VerificationServiceResult"]
