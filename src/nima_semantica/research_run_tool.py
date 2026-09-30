"""Public Research Run boundary; records decisions without making them."""
from __future__ import annotations

from asyncio import CancelledError
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .corpus_registry import CorpusRegistry
from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, StrictModel, identity
from .okf_contracts import GraphIdentifier, GraphRevision
from .receipts import ExecutionReceipt
from .research_contracts import ResearchRun, ResearchRunStatus, TaskTransition
from .research_run_service import ResearchRunService
from .tool_contracts import ToolResult


class RunCreation(StrictModel):
    objective: str = Field(min_length=1, max_length=20_000)
    skill_id: GraphIdentifier
    skill_revision: GraphIdentifier
    registry_revision: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None


class RunTransition(StrictModel):
    transition_id: GraphIdentifier
    from_state: ResearchRunStatus
    to_state: ResearchRunStatus
    from_run_revision: StrictInt = Field(ge=0)
    reason: str = Field(min_length=1, max_length=4_000)
    doubt_or_review: str | None = Field(default=None, max_length=4_000)
    receipt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=100)


class ResearchRunRequest(StrictModel):
    mode: Literal["inspect", "create", "transition"] = "inspect"
    run_id: GraphIdentifier
    operation_id: GraphIdentifier | None = None
    creation: RunCreation | None = None
    transition: RunTransition | None = None
    expected_store_revision: GraphIdentifier | None = None
    limit: StrictInt = Field(default=100, ge=1, le=200)
    after_run_revision: StrictInt = Field(default=0, ge=0)
    after_receipt_id: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def mode_payload(self):
        if (self.creation is not None) != (self.mode == "create"):
            raise ValueError("creation is required only for create mode")
        if (self.transition is not None) != (self.mode == "transition"):
            raise ValueError("transition is required only for transition mode")
        if self.mode != "inspect" and self.operation_id is None:
            raise ValueError("writes require an explicit operation_id")
        if self.mode == "inspect" and (self.operation_id is not None or self.expected_store_revision is not None):
            raise ValueError("inspect does not accept write controls")
        return self


class ResearchRunContext(StrictModel):
    """Operator-owned settings, never merged from the public request."""
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    actor: GraphIdentifier
    allow_writes: StrictBool = False


def _failure(code, message, *, status="failed"):
    return ToolResult(operation="Research Run", status=status,
        diagnostics=({"code": code, "message": message},),
        note="Audit recording only; no research action was scheduled or scientifically approved.")


def inspect_run(store, request, context):
    service = ResearchRunService(store)
    run = service.get_run(request.run_id, corpus_id=context.corpus_id, project_id=context.project_id)
    if run is None:
        return _failure("research_run.not_found", "No run exists in the authorized scope.", status="unavailable")
    transitions = [item for item in service.transitions(request.run_id, corpus_id=context.corpus_id,
        project_id=context.project_id) if item.to_run_revision > request.after_run_revision]
    receipts = [ExecutionReceipt.model_validate(record.content)
        for _, record in store.records("ExecutionReceipt", corpus_id=context.corpus_id, project_id=context.project_id)
        if record.project_id == context.project_id and record.content.get("run_id") == request.run_id
        and record.content.get("receipt_id", "") > request.after_receipt_id]
    receipts.sort(key=lambda item: item.receipt_id)
    transition_page, receipt_page = transitions[:request.limit], receipts[:request.limit]
    return ToolResult(operation="Research Run", status="complete", data={
        "run": run.model_dump(mode="json"), "store_revision": store.revision,
        "transitions": [item.model_dump(mode="json") for item in transition_page],
        "attempts": [item.model_dump(mode="json", exclude={"metadata"}) for item in receipt_page],
        "more_transitions": len(transitions) > request.limit, "more_attempts": len(receipts) > request.limit,
        "next_run_revision": transition_page[-1].to_run_revision if transition_page else request.after_run_revision,
        "next_receipt_id": receipt_page[-1].receipt_id if receipt_page else request.after_receipt_id,
        "next_request": {"mode": "inspect", "run_id": request.run_id, "limit": request.limit,
            "after_run_revision": transition_page[-1].to_run_revision if transition_page else request.after_run_revision,
            "after_receipt_id": receipt_page[-1].receipt_id if receipt_page else request.after_receipt_id
        } if len(transitions) > request.limit or len(receipts) > request.limit else None,
    }, note="Read-only audit history, not evidence that a claim is true. Receipts retain their original outcomes; metadata payloads are omitted.")


def research_run(store, request: ResearchRunRequest, context: ResearchRunContext) -> ToolResult:
    request = ResearchRunRequest.model_validate(request.model_dump(mode="json"))
    context = ResearchRunContext.model_validate(context.model_dump(mode="json"))
    if request.mode == "inspect":
        with store.joined_transaction():
            return inspect_run(store, request, context)
    if not context.allow_writes:
        return _failure("research_run.write_not_authorized", "Enable audit writes in the operator-owned canvas configuration.")
    receipts = ExecutionReceiptService(store)
    service = ResearchRunService(store, CorpusRegistry(store))
    request_hash = identity({"request": request, "context": context})
    receipt_id = identity({"stage": "research_run", "operation_id": request.operation_id,
        "corpus_id": context.corpus_id, "project_id": context.project_id})
    # Keep replay, transition, state update, and successful receipt atomic.
    with store.joined_transaction():
        previous = receipts.replay(receipt_id, corpus_id=context.corpus_id,
            project_id=context.project_id, request_hash=request_hash)
        if previous is not None:
            return ToolResult.model_validate(previous.metadata["result"])
    interrupted = None
    try:
        with store.joined_transaction(request.expected_store_revision):
            # Recheck under the write lock before side effects.
            previous = receipts.replay(receipt_id, corpus_id=context.corpus_id,
                project_id=context.project_id, request_hash=request_hash)
            if previous is not None:
                return ToolResult.model_validate(previous.metadata["result"])
            if request.mode == "create":
                revision = request.creation.graph_revision
                if revision is not None and revision != store.graph_revision(context.corpus_id, context.project_id):
                    raise ConflictError("creation graph revision is stale or outside scope")
                run = ResearchRun(run_id=request.run_id, corpus_id=context.corpus_id, project_id=context.project_id,
                    **request.creation.model_dump())
                record_id = service.create_run(run)
            else:
                change = request.transition
                for identifier in change.receipt_ids:
                    attached = receipts.get(identifier, corpus_id=context.corpus_id, project_id=context.project_id)
                    if attached is None or attached.run_id != request.run_id:
                        raise ConflictError("attached receipt is not part of this scoped run")
                record_id = service.append_transition(TaskTransition(
                    transition_id=change.transition_id, run_id=request.run_id,
                    corpus_id=context.corpus_id, project_id=context.project_id, actor=context.actor,
                    from_state=change.from_state, to_state=change.to_state,
                    from_run_revision=change.from_run_revision, to_run_revision=change.from_run_revision + 1,
                    reason=change.reason, doubt_or_review=change.doubt_or_review,
                    input_ids=change.receipt_ids, idempotency_key=request.operation_id,
                    authorization_scope=(context.corpus_id, context.project_id), approved=False))
            current = service.get_run(request.run_id, corpus_id=context.corpus_id, project_id=context.project_id)
            result = ToolResult(operation="Research Run", status="complete", receipt_ids=(receipt_id,),
                data={"mode": request.mode, "record_id": record_id, "run": current.model_dump(mode="json")},
                note="Run state records a harness decision; it does not schedule actions or certify scientific conclusions. Replays return the original result; inspect for current state.")
            receipts.record(ExecutionReceipt(receipt_id=receipt_id, operation_id=request.operation_id,
                stage="research_run", corpus_id=context.corpus_id, project_id=context.project_id, run_id=request.run_id,
                output_ids=(record_id,), status="completed", tool_version="research-run-v1",
                metadata={"request_hash": request_hash, "result": result.model_dump(mode="json")}))
            return result
    except (Exception, CancelledError, KeyboardInterrupt) as exc:
        interrupted = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
        result = _failure("research_run." + type(exc).__name__, "Run recording failed; inspect the current revision and request fields.")
        result = result.model_copy(update={"receipt_ids": (receipt_id,)})
        receipts.record(ExecutionReceipt(receipt_id=receipt_id, operation_id=request.operation_id,
            stage="research_run", corpus_id=context.corpus_id, project_id=context.project_id, run_id=request.run_id,
            status="interrupted" if interrupted is not None else "failed", error="run recording failed",
            diagnostics=result.diagnostics, tool_version="research-run-v1",
            metadata={"request_hash": request_hash, "result": result.model_dump(mode="json")}))
        if interrupted is not None:
            raise interrupted
        return result
