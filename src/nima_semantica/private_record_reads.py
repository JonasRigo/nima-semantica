"""Exact scoped record reads and complete enumeration for private MCP clients."""
from pydantic import BaseModel, ConfigDict, Field


class EnumerateRecords(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    kind: str = Field(min_length=1, max_length=128)
    cursor: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=25, ge=1, le=100)
    expected_store_revision: str | None = None


class ReadRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    record_id: str = Field(min_length=1, max_length=256)
    full_payload: bool = False


def read_records(store, operation, args):
    if operation == "enumerate_records":
        return store.enumerate_records(kind=args["kind"], after=args.get("cursor"), limit=args.get("limit", 25),
                                       expected_revision=args.get("expected_store_revision"))
    from .inspection_pages import bounded_value
    from .models import identity
    record = store.get(args["record_id"])
    if record is None:
        raise ValueError("unknown or foreign corpus/project record")
    payload = record.model_dump(mode="json")
    return {"record_id": record.id, "canonical_record_sha256": identity(payload), "store_revision": store.revision,
        "record": payload if args.get("full_payload") else bounded_value(payload,
            {"session_id": args["session_id"], "record_id": record.id, "full_payload": True})}
