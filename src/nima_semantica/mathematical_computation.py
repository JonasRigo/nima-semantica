"""Framework-independent bounded mathematical computation service.

The service owns the calculation request/receipt boundary.  Generated or
caller-supplied expressions still execute only through ``SymbolicWorker``;
this module never evaluates them on the host process and never turns an
execution result into a verification claim.
"""

from __future__ import annotations

from asyncio import CancelledError
from typing import Any, Literal

from pydantic import Field

from .calculation import CalculationTask, SymbolicWorker
from .execution_receipts import ExecutionReceiptService
from .okf_contracts import GraphRevision
from .models import ConfigurationError, ConflictError, StrictModel, identity
from .evidence_contracts import require_source_region
from .receipts import ExecutionReceipt


class AlgebraSymbol(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    commutative: bool = True


class AlgebraSpec(StrictModel):
    kind: Literal["commutative", "non_commutative"] = "commutative"
    symbols: tuple[AlgebraSymbol, ...] = Field(default=(), max_length=64)
    rules: tuple[str, ...] = Field(default=(), max_length=128)

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "backend": "sympy",
            "algebra": self.kind,
            "symbols": [symbol.model_dump(mode="json") for symbol in self.symbols],
            "rules": list(self.rules),
            "rules_applied": False,
            "manifest_version": "sympy-algebra-v1",
        }


class MathematicalComputationRequest(StrictModel):
    task: CalculationTask = Field(default_factory=CalculationTask)
    algebra: AlgebraSpec = Field(default_factory=AlgebraSpec)
    operation_id: str = Field(default="", pattern=r"^\S{0,256}$")
    run_id: str | None = Field(default=None, pattern=r"^\S+$")
    graph_revision: GraphRevision | None = None
    source_revision: str | None = Field(default=None, pattern=r"^\S+$")
    input_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")


class MathematicalComputationResult(StrictModel):
    operation_id: str
    corpus_id: str
    project_id: str
    status: Literal["completed", "failed"]
    backend_manifest: dict[str, Any]
    result: dict[str, Any] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_id: str


def _worker_source(request: MathematicalComputationRequest) -> str:
    """Build a deterministic SymPy program; values are injected as literals."""
    task = request.task
    symbols = {symbol.name: symbol.commutative for symbol in request.algebra.symbols}
    symbols.setdefault(task.variable, request.algebra.kind == "commutative")
    payload = {
        "expression": task.expression,
        "variable": task.variable,
        "operation": task.operation,
        "expansion_point": task.expansion_point,
        "expansion_order": task.expansion_order,
        "symbols": symbols,
        "symbol_domain": task.symbol_domain,
    }
    return (
        "import json\nimport sympy as s\n"
        f"request = {payload!r}\n"
        "locals_ = {name: s.Symbol(name, commutative=commutative) for name, commutative in request['symbols'].items()}\n"
        "if request['symbols'][request['variable']]: locals_[request['variable']] = s.Symbol(request['variable'], **{request['symbol_domain']: True})\n"
        "locals_.update({name: getattr(s, name) for name in ('pi', 'E', 'I')})\n"
        "expression = s.sympify(request['expression'], locals=locals_)\n"
        "variable = locals_[request['variable']]\n"
        "operation = request['operation']\n"
        "if operation == 'integrate': value = s.integrate(expression, variable)\n"
        "elif operation == 'differentiate': value = s.diff(expression, variable)\n"
        "elif operation == 'series': value = s.series(expression, variable, request['expansion_point'], request['expansion_order']).removeO()\n"
        "elif operation == 'simplify': value = s.simplify(expression)\n"
        "elif operation == 'solve': value = s.solve(expression, variable)\n"
        "else: value = s.N(expression)\n"
        "print(json.dumps({'operation': operation, 'expression': str(expression), 'result': str(value), 'algebra': request['symbols']}))\n"
    )


class MathematicalComputationService:
    """Run one bounded calculation and persist exactly one immutable receipt."""

    stage = "mathematical_computation"

    def __init__(self, store, *, receipts: ExecutionReceiptService | None = None, worker=None):
        self.store = store
        self.receipts = receipts or ExecutionReceiptService(store)
        self.worker = worker or SymbolicWorker()

    def execute(self, request: MathematicalComputationRequest) -> MathematicalComputationResult:
        task = request.task
        if task.backend != "sympy":
            raise ConfigurationError("Lean computation is owned by LeanVerificationService")
        manifest = request.algebra.manifest
        operation_id = request.operation_id or identity({"request": request.model_dump(mode="json"), "stage": self.stage})
        receipt_id = identity({"operation_id": operation_id, "stage": self.stage})
        request_hash = identity(request)
        previous = self.receipts.replay(receipt_id, corpus_id=task.corpus_id,
            project_id=task.project_id, request_hash=request_hash)
        if previous is not None:
            return MathematicalComputationResult.model_validate(previous.metadata["service_result"])
        metadata = {"backend_manifest": manifest, "task": task.model_dump(mode="json")}
        interruption = None
        try:
            if request.graph_revision is not None and self.store.graph_revision(task.corpus_id, task.project_id) != request.graph_revision:
                raise ConflictError("calculation request targets a stale or foreign graph revision")
            for region_id in task.context_region_ids:
                require_source_region(self.store, region_id, corpus_id=task.corpus_id, project_id=task.project_id)
            execution = self.worker.run(_worker_source(request), task.timeout_seconds)
            metadata["execution"] = execution
            status = "completed" if execution.get("outcome") == "executed" and execution.get("exit_code", 1) == 0 else "failed"
            diagnostics = () if status == "completed" else ({"code": execution.get("outcome", "failed")},)
            result = execution if status == "failed" else _decode_output(execution.get("stdout", ""))
            if status == "completed":
                result = {**result, "execution": execution, "mathematically_verified": False}
            error = None if status == "completed" else execution.get("outcome", "calculation failed")
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            status, diagnostics, result, error = "failed", ({"code": type(exc).__name__},), {}, "calculation execution failed"
        service_result = MathematicalComputationResult(
            operation_id=operation_id, corpus_id=task.corpus_id, project_id=task.project_id,
            status=status, backend_manifest=manifest, result=result,
            diagnostics=diagnostics, receipt_id=receipt_id,
        )
        receipt = ExecutionReceipt(
            receipt_id=receipt_id, operation_id=operation_id, stage=self.stage,
            corpus_id=task.corpus_id, project_id=task.project_id, run_id=request.run_id,
            graph_revision=request.graph_revision, source_revision=request.source_revision,
            input_ids=tuple(dict.fromkeys((*request.input_ids, *task.context_region_ids))), output_ids=(),
            status="interrupted" if interruption is not None else status, error=error,
            diagnostics=diagnostics, tool_version="sympy-algebra-v1",
            idempotency_key=request.idempotency_key, metadata={**metadata, "result": result,
                "request_hash": request_hash, "service_result": service_result.model_dump(mode="json")},
        )
        self.receipts.record(receipt)
        if interruption is not None:
            raise interruption
        return service_result


def _decode_output(stdout: str) -> dict[str, Any]:
    import json
    try:
        value = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError):
        raise ValueError("symbolic worker returned invalid JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("result"), str) or not isinstance(value.get("operation"), str):
        raise ValueError("symbolic worker returned an invalid calculation result")
    return value


__all__ = ["AlgebraSpec", "AlgebraSymbol", "MathematicalComputationRequest", "MathematicalComputationResult", "MathematicalComputationService"]
