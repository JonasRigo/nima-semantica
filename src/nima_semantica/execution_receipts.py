"""Persistence service for model and tool execution receipts."""

from __future__ import annotations

from .models import ConflictError, Record, canonical
from .receipts import ExecutionReceipt, ReceiptReference
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
            selected = self.store.receipt_record(receipt.receipt_id,
                corpus_id=receipt.corpus_id, project_id=receipt.project_id)
            if selected is not None:
                record_id, existing = selected
                if self._payload(existing) != receipt.model_dump(mode="json"):
                    raise ConflictError("receipt ID is already bound to different metadata")
                return record_id
            payload = receipt.model_dump(mode="json")
            raw = canonical(payload)
            if len(raw) > 16384:
                artifact_id = self.store.artifact(raw)
                payload = {k: payload[k] for k in ("receipt_id", "operation_id", "stage", "corpus_id", "project_id", "status", "tool_version")}
                payload["authoritative_payload"] = {"schema_version": 1, "artifact_id": artifact_id,
                    "canonical_payload_sha256": artifact_id, "canonical_payload_bytes": len(raw)}
            return self.store.put(Record(
                kind=self.KIND,
                corpus_id=receipt.corpus_id,
                project_id=receipt.project_id,
                parents=receipt.input_ids,
                content=payload,
            ))

    def get(
        self,
        receipt_id: str,
        *,
        corpus_id: str,
        project_id: str | None = None,
    ) -> ExecutionReceipt | None:
        selected = self.store.receipt_record(receipt_id, corpus_id=corpus_id, project_id=project_id)
        return ExecutionReceipt.model_validate(self._payload(selected[1])) if selected is not None else None

    def _payload(self, record):
        import hashlib
        import json
        content = record.content
        reference = content.get("authoritative_payload")
        if reference is None:
            return content
        raw = self.store.read_artifact(reference["artifact_id"])
        if hashlib.sha256(raw).hexdigest() != reference["canonical_payload_sha256"] or len(raw) != reference["canonical_payload_bytes"]:
            raise ValueError("authoritative receipt artifact digest/size mismatch")
        payload = json.loads(raw)
        receipt = ExecutionReceipt.model_validate(payload)
        if canonical(payload) != raw or (receipt.corpus_id, receipt.project_id, receipt.receipt_id) != (
                record.corpus_id, record.project_id, content["receipt_id"]):
            raise ValueError("authoritative receipt artifact identity/scope mismatch")
        return payload

    def reference(self, receipt_id, *, corpus_id, project_id):
        import hashlib
        selected = self.store.receipt_record(receipt_id, corpus_id=corpus_id, project_id=project_id)
        if selected is None:
            raise ValueError("authoritative receipt is missing or foreign to the configured scope")
        record_id, record = selected
        receipt = ExecutionReceipt.model_validate(self._payload(record))
        data = canonical(receipt.model_dump(mode="json"))
        return ReceiptReference(receipt_id=receipt.receipt_id, corpus_id=corpus_id,
            project_id=project_id, payload_record_id=record_id,
            canonical_payload_sha256=hashlib.sha256(data).hexdigest(), canonical_payload_bytes=len(data),
            summary={k: getattr(receipt, k) for k in ("stage", "status", "tool_version")})

    def resolve(self, reference, *, corpus_id, project_id):
        """Legacy campaign paths are never used to choose a store."""
        import hashlib
        value = reference.model_dump(mode="json") if isinstance(reference, ReceiptReference) else dict(reference)
        if value.get("schema_version") == 1:
            value = ReceiptReference.model_validate(value).model_dump(mode="json")
        if (value.get("corpus_id"), value.get("project_id")) != (corpus_id, project_id):
            raise ValueError("receipt reference scope mismatch")
        receipt = self.get(value["receipt_id"], corpus_id=corpus_id, project_id=project_id)
        if receipt is None:
            raise ValueError("authoritative receipt payload is unavailable")
        raw = canonical(receipt.model_dump(mode="json"))
        if hashlib.sha256(raw).hexdigest() != value.get("canonical_payload_sha256") or len(raw) != value.get("canonical_payload_bytes"):
            raise ValueError("receipt reference payload digest or size mismatch")
        if "payload_record_id" in value:
            selected = self.store.receipt_record(receipt.receipt_id, corpus_id=corpus_id, project_id=project_id)
            if selected[0] != value["payload_record_id"]:
                raise ValueError("receipt reference authoritative identity mismatch")
        return receipt


__all__ = ["ExecutionReceiptService"]
