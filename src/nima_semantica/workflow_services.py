"""Framework-independent services for the first NIMA reference workflows."""

from __future__ import annotations

from collections.abc import Callable

from .graph_service import GraphService
from .graph_retrieval import GraphRetrievalRequest, GraphRetrievalService
from .models import ConflictError, StrictModel
from .normalization_boundary import execute_normalizer
from .workflow_contracts import (
    GraphCommitOutput,
    GraphCommitRequest,
    GraphRetrievalNormalizerOutput,
    GraphRetrievalNormalizerRequest,
    InputNormalizerRequest,
    InputNormalizerOutput,
    WorkflowReceipt,
)


class InputNormalizationService:
    """Execute an imported adapter without granting it persistence authority."""

    def execute(
        self,
        request: InputNormalizerRequest,
        adapter: Callable,
    ) -> InputNormalizerOutput:
        return execute_normalizer(request.manifest, request.request, adapter)


class GraphRetrievalNormalizationService:
    """Adapt hybrid GraphRAG output to the standard retrieval packet."""

    def execute(self, store, provider, request: GraphRetrievalNormalizerRequest, profile=None):
        if request.graph_revision is not None and request.graph_revision != store.graph_revision(request.corpus_id, request.project_id):
            raise ConflictError("retrieval request targets a stale graph revision")
        retrieval_request = GraphRetrievalRequest(
            request_id=request.request_id,
            query=request.query,
            project_id=request.project_id,
            corpus_id=request.corpus_id,
            graph_revision=request.graph_revision,
            max_hops=request.policy.max_hops,
            limit=request.policy.limit,
            max_nodes=request.policy.max_nodes,
            max_edges=request.policy.max_edges,
            max_neighbors=request.policy.max_neighbors,
            max_results=request.policy.max_results,
            max_omitted=request.policy.max_omitted,
            prepared_only=request.policy.prepared_only,
        )
        result = GraphRetrievalService(store).execute(retrieval_request, provider, profile=profile)
        packet = result.context_packet
        if request.graph_revision is not None and packet.graph_revision != request.graph_revision:
            raise ConflictError("retrieval packet revision differs from request")
        return GraphRetrievalNormalizerOutput(
            request_id=request.request_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            graph_revision=packet.graph_revision,
            context_packet=packet,
            selected=tuple(result.selected),
            receipts=(WorkflowReceipt(receipt_id=result.receipt_id),),
        )


class GraphCommitService:
    """The only service in this group allowed to mutate the project KG."""

    def execute(self, store, request: GraphCommitRequest) -> GraphCommitOutput:
        result = GraphService(store).commit_delta(request.delta, request.approval)
        return GraphCommitOutput(
            request_id=request.request_id,
            corpus_id=request.delta.corpus_id,
            project_id=request.delta.project_id,
            base_revision=result.base_revision,
            final_revision=result.final_revision,
            result=result,
            receipts=(WorkflowReceipt(receipt_id=result.receipt_id),),
        )


__all__ = [
    "GraphCommitService",
    "GraphRetrievalNormalizationService",
    "InputNormalizationService",
]
