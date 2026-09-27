"""Framework-independent Lean project verification service."""

from __future__ import annotations

from asyncio import CancelledError
from dataclasses import asdict
from typing import Any, Literal

from pydantic import Field, model_validator

from .execution_receipts import ExecutionReceiptService
from .evidence_contracts import require_source_region
from .lean_project import LeanProjectRequest, LeanProjectResult
from .okf_contracts import GraphRevision
from .models import ConflictError, Record, StrictModel, canonical, identity
from .receipts import ExecutionReceipt


class LeanVerificationRequest(StrictModel):
    sources: dict[str, str] = Field(min_length=1, max_length=32)
    targets: tuple[str, ...] = Field(min_length=1, max_length=64)
    imports: tuple[str, ...] = Field(min_length=1, max_length=32)
    corpus_id: str = Field(min_length=1, pattern=r"^\S+$")
    project_id: str | None = Field(default=None, pattern=r"^\S+$")
    graph_revision: GraphRevision | None = None
    source_revision: str | None = Field(default=None, max_length=256)
    proof_target_id: str | None = Field(default=None, pattern=r"^\S+$")
    parent_node_ids: tuple[str, ...] = Field(default=(), max_length=256)
    definition_node_ids: tuple[str, ...] = Field(default=(), max_length=256)
    source_region_ids: tuple[str, ...] = Field(default=(), max_length=256)
    run_id: str | None = Field(default=None, pattern=r"^\S+$")
    operation_id: str = Field(default="", pattern=r"^\S{0,256}$")
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")

    @model_validator(mode="after")
    def validate_lean_request(self) -> "LeanVerificationRequest":
        LeanProjectRequest(dict(self.sources), tuple(self.targets), tuple(self.imports)).validate()
        return self


class LeanVerificationResult(StrictModel):
    operation_id: str
    corpus_id: str
    project_id: str | None = None
    status: Literal["verified", "inconclusive", "failed"]
    outcome: Literal["verified", "inconclusive", "failed"]
    evidence_artifact: str | None = None
    environment_manifest: dict[str, Any] = Field(default_factory=dict)
    project_result: dict[str, Any] = Field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    receipt_id: str
    correspondence_verified: Literal[False] = False
    authority: Literal["verification_result"] = "verification_result"


class LeanVerificationService:
    """Run one bounded Lean attempt against an administrator-pinned verifier."""

    stage = "lean_verification"

    def __init__(self, store, verifier=None, *, receipts: ExecutionReceiptService | None = None):
        self.store = store
        self.verifier = verifier
        self.receipts = receipts or ExecutionReceiptService(store)

    def environment_manifest(self) -> dict[str, Any]:
        if self.verifier is None:
            return {"configured": False}
        toolchain = getattr(self.verifier, "toolchain", None)
        libraries = getattr(self.verifier, "libraries", ())
        return {
            "configured": True,
            "protocol": "lean-project-kernel-replay-v3",
            "toolchain_sha256": getattr(toolchain, "sha256", None),
            "library_sha256": [getattr(item, "sha256", None) for item in libraries],
            "trusted_imports": list(getattr(self.verifier, "trusted_imports", ())),
            "timeout_seconds": getattr(self.verifier, "timeout", None),
            "memory_mb": getattr(self.verifier, "memory_mb", None),
            "output_bytes": getattr(self.verifier, "output_bytes", None),
            "artifact_bytes": getattr(self.verifier, "artifact_bytes", None),
            "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
        }

    def request_hash(self, request: LeanVerificationRequest) -> str:
        """Exact binding for native reasoning checks, including compilation order."""
        return identity({"request":request,"source_order":list(request.sources),"environment":self.environment_manifest()})

    def execute(self, request: LeanVerificationRequest) -> LeanVerificationResult:
        operation_id = request.operation_id or identity({
            "stage": self.stage,
            "request": request.model_dump(mode="json", exclude={"operation_id", "idempotency_key"}),
            "source_order": list(request.sources),
            "environment": self.environment_manifest(),
        })
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        # Canonical JSON sorts mappings; compilation order must remain part of identity.
        request_hash = self.request_hash(request)
        previous = self.receipts.replay(
            receipt_id, corpus_id=request.corpus_id, project_id=request.project_id, request_hash=request_hash
        )
        if previous is not None:
            return LeanVerificationResult.model_validate(previous.metadata["result"])

        environment = self.environment_manifest()
        status: Literal["verified", "inconclusive", "failed"] = "failed"
        project_result: dict[str, Any] = {}
        evidence_artifact = None
        diagnostics: tuple[str, ...] = ()
        error = None
        interruption = None
        try:
            if request.graph_revision is not None and self.store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
                raise ConflictError("Lean verification request targets a stale graph revision")
            if self.verifier is None:
                raise RuntimeError("no administrator-configured Lean verifier")
            for region_id in request.source_region_ids:
                require_source_region(self.store, region_id, corpus_id=request.corpus_id, project_id=request.project_id)
            pinned_environment = self.environment_manifest()
            native = LeanProjectRequest(dict(request.sources), tuple(request.targets), tuple(request.imports))
            result = self.verifier.verify(self.store, native)
            if self.environment_manifest() != pinned_environment:
                raise ConflictError("Lean verifier environment changed during verification")
            project_result = asdict(result)
            evidence_artifact = result.evidence_artifact
            diagnostics = tuple(result.diagnostics)
            status = "verified" if result.compilation_succeeded and result.inspection_succeeded and result.certification_verified and result.axioms_accepted and result.kernel_replay_succeeded else (
                "inconclusive" if result.compilation_succeeded else "failed"
            )
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            diagnostics = (type(exc).__name__,)
            error = "Lean verification failed"

        result = LeanVerificationResult(
            operation_id=operation_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            status=status,
            outcome=status,
            evidence_artifact=evidence_artifact,
            environment_manifest=environment,
            project_result=project_result,
            diagnostics=diagnostics,
            receipt_id=receipt_id,
        )
        output_ids = tuple(item for item in (evidence_artifact,) if item)
        execution_receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=operation_id,
            stage=self.stage,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            run_id=request.run_id,
            graph_revision=request.graph_revision,
            source_revision=request.source_revision,
            input_ids=(*request.source_region_ids, *request.parent_node_ids, *request.definition_node_ids),
            output_ids=output_ids,
            status="interrupted" if interruption is not None else "completed" if status == "verified" else "partial" if status == "inconclusive" else "failed",
            error=error,
            diagnostics=tuple({"message": item} for item in diagnostics),
            provider="lean",
            tool_version="lean-project-kernel-replay-v3",
            idempotency_key=request.idempotency_key,
            metadata={"environment_manifest": environment, "request_hash": request_hash, "result": result.model_dump(mode="json")},
        )
        with self.store.joined_transaction():
            self._persist_attempt(request, result)
            self.receipts.record(execution_receipt)
        if interruption is not None:
            raise interruption
        return result

    def _persist_attempt(self, request: LeanVerificationRequest, result: LeanVerificationResult) -> str:
        payload = {
            "operation_id": result.operation_id,
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "status": result.status,
            "authority": "verification_result",
        }
        with self.store.joined_transaction():
            for record_id, existing in self.store.records("LeanProofAttempt", corpus_id=request.corpus_id):
                if existing.project_id != request.project_id or existing.content.get("operation_id") != result.operation_id:
                    continue
                if existing.content != payload:
                    raise ConflictError("Lean proof attempt ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind="LeanProofAttempt",
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                parents=request.source_region_ids,
                content=payload,
            ))


__all__ = ["LeanVerificationRequest", "LeanVerificationResult", "LeanVerificationService"]
