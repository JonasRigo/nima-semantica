"""Framework-independent, provenance-aware Semantica reasoning service."""

from __future__ import annotations

from asyncio import CancelledError

from .okf_contracts import GraphRevision

from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field

from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, StrictModel, identity
from .reasoning_kernel import (
    Atom,
    ContextPolicy,
    GraphSnapshot,
    InferenceReport,
    infer,
)
from .receipts import ExecutionReceipt

_HYPOTHESIS_JUSTIFICATION = "Exploratory hypothesis context; not admitted to the knowledge graph."


def _hypothesis_item(item):
    # Preserve distinct source supports when different assertions/rules share
    # the same atom or body. A temporary context must not erase their identity.
    return item.model_copy(update={"origin":"assumed",
        "justification":_HYPOTHESIS_JUSTIFICATION+" Source item: "+item.id})


class GraphReasoningRequest(StrictModel):
    """One revision-bound reasoning request over a translated logical graph."""

    graph: GraphSnapshot
    corpus_id: str = Field(min_length=1, pattern=r"^\S+$")
    project_id: str | None = Field(default=None, pattern=r"^\S+$")
    graph_revision: GraphRevision
    mode: Literal["strict", "hypothesizing"] = "strict"
    operation_id: str = Field(default="", pattern=r"^\S{0,256}$")
    run_id: str | None = Field(default=None, pattern=r"^\S+$")
    input_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$")


class ReasoningConclusion(StrictModel):
    """A consequence with explicit support and hypothesis taint."""

    atom: Atom
    support_ids: tuple[str, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()


class GraphReasoningResult(StrictModel):
    request_id: str
    corpus_id: str
    project_id: str | None = None
    graph_revision: GraphRevision
    mode: Literal["strict", "hypothesizing"]
    status: Literal["completed", "partial", "failed"]
    inference: InferenceReport | None = None
    admitted_conclusions: tuple[ReasoningConclusion, ...] = ()
    conditional_conclusions: tuple[ReasoningConclusion, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    contradiction_ids: tuple[str, ...] = ()
    context_item_sources: dict[str, str] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_id: str
    authority: Literal["read_only_reasoning"] = "read_only_reasoning"


def _hypothesizing_graph(graph: GraphSnapshot) -> GraphSnapshot:
    """Make a non-persistent exploratory context from all typed candidates."""
    return graph.model_copy(update={
        "policy": graph.policy.model_copy(update={"allow_assumptions": True}),
        "assertions": tuple(
            _hypothesis_item(assertion)
            for assertion in graph.assertions
        ),
        "rules": tuple(
            _hypothesis_item(rule)
            for rule in graph.rules
        ),
    })


def _strict_graph(graph: GraphSnapshot) -> GraphSnapshot:
    """Remove unadmitted assertions and rules before invoking the backend."""
    return graph.model_copy(update={
        "assertions": tuple(item for item in graph.assertions if item.origin == "assumed"),
        "rules": tuple(item for item in graph.rules if item.origin == "assumed"),
    })


class GraphReasoningService:
    """Run bounded Semantica inference without mutating the authoritative graph."""

    stage = "graph_reasoning"

    def __init__(self, store, *, backend: Callable[[GraphSnapshot], InferenceReport] | None = None,
                 receipts: ExecutionReceiptService | None = None):
        self.store = store
        self.backend = backend or infer
        self.receipts = receipts or ExecutionReceiptService(store)

    def execute(self, request: GraphReasoningRequest) -> GraphReasoningResult:
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
            return GraphReasoningResult.model_validate(previous.metadata["result"])

        diagnostics: tuple[dict[str, Any], ...] = ()
        inference: InferenceReport | None = None
        status: Literal["completed", "partial", "failed"] = "failed"
        error: str | None = None
        result_payload: dict[str, Any] = {}
        interruption = None
        try:
            if self.store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
                raise ConflictError("reasoning request targets a stale graph revision")
            logical_graph = (
                _hypothesizing_graph(request.graph)
                if request.mode == "hypothesizing"
                else _strict_graph(request.graph)
            )
            inference = self.backend(logical_graph)
            conclusions, conditional, hypotheses, contradictions = self._classify(
                request.graph, inference, request.mode
            )
            diagnostics = tuple(item.model_dump(mode="json") for item in inference.diagnostics)
            status = "completed" if inference.complete else "partial"
            result_payload = GraphReasoningResult(
                request_id=operation_id,
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                graph_revision=request.graph_revision,
                mode=request.mode,
                status=status,
                inference=inference,
                admitted_conclusions=conclusions,
                conditional_conclusions=conditional,
                hypothesis_ids=hypotheses,
                contradiction_ids=contradictions,
                context_item_sources={(_hypothesis_item(source).id if request.mode=="hypothesizing" else source.id):source.id
                    for source in (*request.graph.assertions,*request.graph.rules)
                    if request.mode=="hypothesizing" or source.origin=="assumed"},
                diagnostics=diagnostics,
                receipt_id=receipt_id,
            ).model_dump(mode="json")
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            status = "failed"
            diagnostics = ({"code": type(exc).__name__},)
            error = "graph reasoning failed"
            result_payload = GraphReasoningResult(
                request_id=operation_id,
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                graph_revision=request.graph_revision,
                mode=request.mode,
                status="failed",
                diagnostics=diagnostics,
                receipt_id=receipt_id,
            ).model_dump(mode="json")

        output_ids = tuple(
            conclusion["atom"]["predicate"] + ":" + ":".join(conclusion["atom"]["arguments"])
            for conclusion in result_payload.get("admitted_conclusions", ())
        )
        receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=operation_id,
            stage=self.stage,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            run_id=request.run_id,
            graph_revision=request.graph_revision,
            input_ids=request.input_ids,
            output_ids=output_ids,
            status="interrupted" if interruption is not None else status,
            error=error,
            diagnostics=diagnostics,
            provider="semantica",
            tool_version="semantica-horn-adapter-v1",
            idempotency_key=request.idempotency_key,
            metadata={"request_hash": request_hash, "mode": request.mode, "result": result_payload},
        )
        self.receipts.record(receipt)
        if interruption is not None:
            raise interruption
        return GraphReasoningResult.model_validate(result_payload)

    @staticmethod
    def _classify(
        source: GraphSnapshot,
        inference: InferenceReport,
        mode: Literal["strict", "hypothesizing"],
    ) -> tuple[
        tuple[ReasoningConclusion, ...],
        tuple[ReasoningConclusion, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        effective_assertions = {
            _hypothesis_item(assertion).id: assertion.id
            for assertion in source.assertions
        }
        effective_rules = {
            _hypothesis_item(rule).id: rule.id
            for rule in source.rules
        }
        hypothesis_assertions = {
            assertion.id for assertion in source.assertions if assertion.origin != "assumed"
        }
        hypothesis_rules = {rule.id for rule in source.rules if rule.origin != "assumed"}
        supports_by_conclusion: dict[str, list[Any]] = {}
        for support in inference.supports:
            supports_by_conclusion.setdefault(support.conclusion, []).append(support)

        def dependencies(atom_key: str, visiting: set[str] | None = None) -> set[str]:
            visiting = set() if visiting is None else visiting
            if atom_key in visiting:
                return set()
            visiting.add(atom_key)
            found = {
                effective_assertions.get(assertion_id, assertion_id)
                for assertion_id in inference.seed_supports.get(atom_key, ())
                if effective_assertions.get(assertion_id, assertion_id) in hypothesis_assertions
            }
            for support in supports_by_conclusion.get(atom_key, ()):
                source_rule_id = effective_rules.get(support.rule_id, support.rule_id)
                if source_rule_id in hypothesis_rules:
                    found.add(source_rule_id)
                for premise in support.premises:
                    found.update(dependencies(premise, visiting))
            return found

        blocked = set(inference.blocked)
        admitted: list[ReasoningConclusion] = []
        conditional: list[ReasoningConclusion] = []
        for atom in inference.atoms:
            if atom.key in blocked:
                continue
            dependency_ids = tuple(sorted(dependencies(atom.key)))
            support_ids = tuple(sorted({
                *(effective_assertions.get(ref, ref) for ref in inference.seed_supports.get(atom.key, ())),
                *(effective_rules.get(support.rule_id, support.rule_id)
                  for support in supports_by_conclusion.get(atom.key, ())),
            }))
            conclusion = ReasoningConclusion(
                atom=atom, support_ids=support_ids, hypothesis_ids=dependency_ids
            )
            if mode == "hypothesizing" and dependency_ids:
                conditional.append(conclusion)
            else:
                admitted.append(conclusion)

        contradictions = tuple(sorted({
            affected
            for diagnostic in inference.diagnostics
            if diagnostic.code == "explicit_contradiction"
            for affected in diagnostic.affected
        }))
        hypotheses = tuple(sorted(hypothesis_assertions | hypothesis_rules)) if mode == "hypothesizing" else ()
        return tuple(admitted), tuple(conditional), hypotheses, contradictions


__all__ = [
    "GraphReasoningRequest",
    "GraphReasoningResult",
    "GraphReasoningService",
    "ReasoningConclusion",
]
