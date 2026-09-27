"""Persistence service for model and tool execution receipts."""

from __future__ import annotations

from .models import ConflictError, Record
from .receipts import ExecutionReceipt
from .storage import GraphStore


class ExecutionReceiptService:
    """Persist attempts without asserting verification or graph admission."""

    KIND = "ExecutionReceipt"

    def __init__(self, store: GraphStore):
        self.store = store

    def replay(self, receipt_id, *, corpus_id, project_id, request_hash):
        """Fail closed on changed or unbound requests, including old receipts."""
        previous = self.get(receipt_id, corpus_id=corpus_id, project_id=project_id)
        if previous is not None and previous.metadata.get("request_hash") != request_hash:
            raise ConflictError("attempt ID is already bound to a different request")
        return previous

    def record(
        self,
        receipt: ExecutionReceipt,
        *,
        expected_store_revision: str | None = None,
    ) -> str:
        with self.store.joined_transaction(expected_store_revision):
            for record_id, existing in self.store.records(self.KIND, corpus_id=receipt.corpus_id):
                if existing.project_id != receipt.project_id:
                    continue
                if existing.content.get("receipt_id") != receipt.receipt_id:
                    continue
                if existing.content != receipt.model_dump(mode="json"):
                    raise ConflictError("receipt ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind=self.KIND,
                corpus_id=receipt.corpus_id,
                project_id=receipt.project_id,
                parents=receipt.input_ids,
                content=receipt.model_dump(mode="json"),
            ))

    def get(
        self,
        receipt_id: str,
        *,
        corpus_id: str,
        project_id: str | None = None,
    ) -> ExecutionReceipt | None:
        for _, record in self.store.records(self.KIND, corpus_id=corpus_id):
            if record.project_id == project_id and record.content.get("receipt_id") == receipt_id:
                return ExecutionReceipt.model_validate(record.content)
        return None


__all__ = ["ExecutionReceiptService"]
