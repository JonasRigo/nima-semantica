"""Content-addressed private session material with scope and digest checks."""
import hashlib
import json

from .models import canonical


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS private_payloads (scope TEXT NOT NULL, digest TEXT NOT NULL, "
               "payload BLOB NOT NULL, PRIMARY KEY(scope,digest))")


def compact(db, scope, value):
    if isinstance(value, dict):
        if set(value) == {"private_payload_reference"}:
            # Escape literal caller material before interpreting any references.
            raw = canonical(value)
            digest = hashlib.sha256(raw).hexdigest()
            db.execute("INSERT OR IGNORE INTO private_payloads VALUES (?,?,?)", (scope, digest, raw))
            return {"private_payload_reference": {"schema_version": 1, "sha256": digest,
                "bytes": len(raw), "scope": scope}}
        return {key: _item(db, scope, key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [compact(db, scope, item) for item in value]
    return value


def _item(db, scope, key, value):
    # Keep graph structure explicit. Large source/response material is immutable.
    if key in {"source", "execution", "output", "stdout", "stderr", "value", "compiler_logs",
               "source_text", "retrieval_context", "retrieval_receipt", "compiled_source"}:
        raw = canonical(value)
        if len(raw) > 8192 or (key == "value" and isinstance(value, dict) and "private_payload_reference" in value):
            digest = hashlib.sha256(raw).hexdigest()
            db.execute("INSERT OR IGNORE INTO private_payloads VALUES (?,?,?)", (scope, digest, raw))
            return {"private_payload_reference": {"schema_version": 1, "sha256": digest,
                                                   "bytes": len(raw), "scope": scope}}
    return compact(db, scope, value)


def expand(db, scope, value):
    if isinstance(value, dict) and set(value) == {"private_payload_reference"}:
        reference = value["private_payload_reference"]
        selected_scope = reference.get("scope")
        def bound_scope(raw):
            parsed = json.loads(raw)
            parsed.pop("retrieval_projection_id", None)
            return parsed
        if reference.get("schema_version") != 1 or not isinstance(selected_scope, str) or bound_scope(selected_scope) != bound_scope(scope):
            raise ValueError("private payload reference scope/version mismatch")
        row = db.execute("SELECT payload FROM private_payloads WHERE scope=? AND digest=?", (selected_scope, reference["sha256"])).fetchone()
        if row is None:
            raise ValueError("private payload is unavailable")
        raw = bytes(row[0])
        if len(raw) != reference["bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("private payload digest/size mismatch")
        return json.loads(raw)
    if isinstance(value, dict):
        return {key: expand(db, scope, item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(db, scope, item) for item in value]
    return value
