from .okf_contracts import GraphRevision
"""Immutable attempt receipts, including failed and interrupted operations."""
from importlib.metadata import version
from uuid import uuid4
from typing import Any, Literal

from pydantic import Field

from .models import Record, StrictModel, now
from . import __version__
from .okf_contracts import GraphIdentifier


class ExecutionReceipt(StrictModel):
    """Versioned evidence that one bounded operation was attempted."""

    schema_version: Literal[2] = 2
    receipt_id: GraphIdentifier
    operation_id: GraphIdentifier
    stage: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    source_revision: GraphIdentifier | None = None
    input_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    output_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=10_000)
    status: Literal["completed", "failed", "interrupted", "partial"] = "completed"
    error: str | None = Field(default=None, max_length=4_000)
    diagnostics: tuple[dict[str, Any], ...] = Field(default=(), max_length=1_024)
    provider: GraphIdentifier | None = None
    model: GraphIdentifier | None = None
    tool_version: GraphIdentifier | None = None
    idempotency_key: GraphIdentifier | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=128)


class ReceiptReference(StrictModel):
    """Authenticated identity in an operator-selected authoritative store."""
    schema_version: Literal[1] = 1
    receipt_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    payload_record_id: GraphIdentifier
    canonical_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_payload_bytes: int = Field(ge=0)
    summary: dict[str, str | None] = Field(max_length=8)


def receipt(stage, run_id, project_id, corpus_id, inputs, outputs=(), status="completed", error=None, metadata=None):
    return Record(kind="Receipt", project_id=project_id, corpus_id=corpus_id, parents=tuple(inputs), content={
        "schema_version": 1, "invocation_id": uuid4().hex, "stage": stage,
        "run_id": run_id, "outputs": list(outputs), "status": status,
        "error": error, "recorded_at": now(), "semantica_version": version("semantica"),
        "nima_version": __version__, "metadata": metadata or {},
    })


__all__ = ["ExecutionReceipt", "receipt"]
