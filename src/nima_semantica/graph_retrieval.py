"""Framework-independent GraphRAG retrieval service boundary."""

from __future__ import annotations

from .okf_contracts import GraphRevision

from typing import Any, Literal

from pydantic import Field

from .models import Record, StrictModel
from .retrieval import retrieve
from .retrieval_contracts import RetrievalContextPacket


class GraphRetrievalResult(StrictModel):
    request_id: str | None = None
    corpus_id: str
    project_id: str | None = None
    graph_revision: GraphRevision
    selected: tuple[dict[str, Any], ...]
    context_packet: RetrievalContextPacket
    receipt_id: str
    authority: Literal["read_only_retrieval"] = "read_only_retrieval"


class GraphRetrievalRequest(StrictModel):
    """Explicit scope, revision, query, and budget for one GraphRAG read."""

    request_id: str | None = None
    corpus_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]+$")
    graph_revision: GraphRevision | None = None
    query: str = Field(min_length=1, max_length=20_000)
    max_hops: int = Field(default=2, ge=0, le=8)
    limit: int = Field(default=8, ge=1, le=256)
    max_nodes: int = Field(default=256, ge=1, le=10_000)
    max_edges: int = Field(default=1_024, ge=1, le=50_000)
    max_neighbors: int = Field(default=64, ge=1, le=1_000)
    max_results: int = Field(default=64, ge=1, le=10_000)
    max_omitted: int = Field(default=256, ge=0, le=10_000)
    prepared_only: bool = False


def context_packet_from_selection(selection: Record) -> RetrievalContextPacket:
    """Validate the typed context packet persisted inside a selection record."""
    packet = selection.content.get("context_packet")
    if packet is None:
        raise ValueError("selection record does not contain a retrieval context packet")
    return RetrievalContextPacket.model_validate(packet)


class GraphRetrievalService:
    """Perform bounded, scope-aware GraphRAG retrieval without graph mutation."""

    stage = "graph_retrieval"

    def __init__(self, store):
        self.store = store

    def search_projection(self, request, context, *, provider=None, manifest=None):
        """Native prepared-projection read; no lazy cache publication or index rebuild."""
        from .research_retrieval import projected_context
        return projected_context(self.store, request, context, provider=provider, manifest=manifest)

    def execute(self, request: GraphRetrievalRequest, provider, profile=None) -> GraphRetrievalResult:
        if request.graph_revision is not None and request.graph_revision != self.store.graph_revision(request.corpus_id, request.project_id):
            from .models import ConflictError

            raise ConflictError("retrieval request targets a stale graph revision")

        selected, selection = retrieve(
            self.store,
            provider,
            profile,
            request.query,
            request.project_id,
            request.corpus_id,
            request.max_hops,
            request.limit,
            max_nodes=request.max_nodes,
            max_edges=request.max_edges,
            max_neighbors=request.max_neighbors,
            max_results=request.max_results,
            max_omitted=request.max_omitted,
            prepared_only=request.prepared_only,
        )
        packet = context_packet_from_selection(selection)
        if packet.corpus_id != request.corpus_id or packet.project_id != request.project_id:
            raise ValueError("retrieval context scope differs from request")
        if request.graph_revision is not None and packet.graph_revision != request.graph_revision:
            from .models import ConflictError

            raise ConflictError("retrieval packet revision differs from request")
        return GraphRetrievalResult(
            request_id=request.request_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            graph_revision=packet.graph_revision,
            selected=tuple(selected),
            context_packet=packet,
            receipt_id=selection.id,
        )


def retrieve_context(store, provider, request, profile=None) -> GraphRetrievalResult:
    """Compatibility wrapper for callers using the former function boundary."""
    service_request = GraphRetrievalRequest.model_validate(request, from_attributes=True)
    return GraphRetrievalService(store).execute(service_request, provider, profile=profile)


__all__ = [
    "GraphRetrievalRequest",
    "GraphRetrievalResult",
    "GraphRetrievalService",
    "context_packet_from_selection",
    "retrieve_context",
]
