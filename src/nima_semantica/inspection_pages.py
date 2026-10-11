"""Whole-object inspection pages; exact material is explicitly retrievable."""
import base64
import hashlib
import json

from .models import canonical

PAGE_BYTES = 65536


def page_offset(cursor, *, session_id, revision):
    if cursor is None:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if set(value) != {"session_id", "revision", "offset"} or value["session_id"] != session_id or value["revision"] != revision:
            raise ValueError
        if type(value["offset"]) is not int or value["offset"] < 0:
            raise ValueError
        return value["offset"]
    except Exception:
        raise ValueError("invalid or stale inspection cursor") from None


def next_cursor(offset, *, session_id, revision):
    return base64.urlsafe_b64encode(canonical(dict(session_id=session_id, revision=revision, offset=offset))).decode()


def bounded_value(value, reference, *, max_bytes=PAGE_BYTES // 4):
    raw = canonical(value)
    if len(raw) <= max_bytes:
        return value
    return {"payload_reference": reference, "canonical_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_payload_bytes": len(raw), "full_payload_required": True,
            "note": "Exact content was omitted as a whole; request full_payload explicitly."}


def graph_page(graph, *, session_id, revision, cursor=None, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError("inspection page limit must be in [1, 100]")
    offset = page_offset(cursor, session_id=session_id, revision=revision)
    limit = min(limit, 25)
    entries = []
    page = {}
    metadata_count = max(1, len(graph))
    for key, value in graph.items():
        if isinstance(value, dict) and key in {"nodes", "receipts", "resolutions", "compiled_plans"}:
            page[key] = {}
            entries.extend((key, name, item) for name, item in sorted(value.items()))
        elif isinstance(value, list) and key in {"order", "obligations", "superseded", "actions"}:
            page[key] = []
            entries.extend((key, None, item) for item in value)
        else:
            page[key] = bounded_value(value, {"operation": "export", "session_id": session_id, "section": key, "full_payload": True}, max_bytes=8192 // metadata_count)
    remaining_budget = 24576
    for section, name, value in entries[offset:offset + limit]:
        reference = {"operation": "export", "session_id": session_id, "section": section, "identifier": name, "full_payload": True}
        if section in {"resolutions", "superseded"}:
            reference["state_section"] = section
        item = bounded_value(value, reference, max_bytes=max(0, min(8192, remaining_budget)))
        remaining_budget -= len(canonical(item))
        if name is None:
            page[section].append(item)
        else:
            page[section][name] = item
    has_more = offset + limit < len(entries)
    def omitted(value):
        return isinstance(value, dict) and (value.get("full_payload_required") is True or any(omitted(v) for v in value.values())) or isinstance(value, list) and any(omitted(v) for v in value)
    return page, {"has_more": has_more, "next_cursor": next_cursor(offset + limit, session_id=session_id,
        revision=revision) if has_more else None, "total_entries": len(entries), "offset": offset,
        "page_limit": limit, "response_limit_bytes": PAGE_BYTES, "partial_export": has_more or omitted(page)}
