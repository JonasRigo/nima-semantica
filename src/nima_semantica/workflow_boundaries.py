"""Validation boundaries used by the hypothesis Langflow flows.

Hypothesis generation and comparison are workflows, not domain services. These
functions provide reusable deterministic validation for their typed outputs
without invoking a model or mutating graph state.
"""

from __future__ import annotations

from collections.abc import Iterable

from .workflow_contracts import (
    HypothesisComparison,
    HypothesisComparisonOutput,
    HypothesisComparisonRequest,
    HypothesisGenerationOutput,
    HypothesisGenerationRequest,
    HypothesisProposal,
    WorkflowReceipt,
)


def _scope_references(request: HypothesisGenerationRequest, proposal: HypothesisProposal) -> None:
    references = (*proposal.supporting_references, *proposal.contradicting_references)
    for reference in references:
        if reference.corpus_id != request.corpus_id or reference.project_id not in (None, request.project_id):
            raise ValueError(f"hypothesis {proposal.proposal_id} contains an out-of-scope graph reference")
    for evidence in proposal.evidence:
        if evidence.corpus_id != request.corpus_id or evidence.project_id not in (None, request.project_id):
            raise ValueError(f"hypothesis {proposal.proposal_id} contains out-of-scope evidence")


def validate_hypothesis_generation(
    request: HypothesisGenerationRequest,
    proposals: Iterable[HypothesisProposal],
) -> HypothesisGenerationOutput:
    values = tuple(proposals)
    if not values:
        raise ValueError("hypothesis generation requires at least one proposal")
    for proposal in values:
        _scope_references(request, proposal)
    return HypothesisGenerationOutput(
        request_id=request.request_id,
        corpus_id=request.corpus_id,
        project_id=request.project_id,
        graph_revision=request.graph_revision,
        proposals=values,
        receipts=tuple(WorkflowReceipt(receipt_id=value) for value in request.receipt_ids),
    )


def validate_hypothesis_comparison(
    request: HypothesisComparisonRequest,
    comparisons: Iterable[HypothesisComparison],
) -> HypothesisComparisonOutput:
    values = tuple(comparisons)
    if not values:
        raise ValueError("hypothesis comparison requires at least one comparison")
    proposal_ids = {proposal.proposal_id for proposal in request.hypotheses}
    for comparison in values:
        if comparison.proposal_id not in proposal_ids:
            raise ValueError("comparison refers to an unknown hypothesis proposal")
        references = (*comparison.supporting_references, *comparison.contradicting_references)
        for reference in references:
            if reference.corpus_id != request.corpus_id or reference.project_id not in (None, request.project_id):
                raise ValueError(f"comparison for {comparison.proposal_id} contains an out-of-scope reference")
    return HypothesisComparisonOutput(
        request_id=request.request_id,
        corpus_id=request.corpus_id,
        project_id=request.project_id,
        graph_revision=request.graph_revision,
        comparisons=values,
        receipts=tuple(WorkflowReceipt(receipt_id=value) for value in request.receipt_ids),
    )


__all__ = ["validate_hypothesis_comparison", "validate_hypothesis_generation"]
