"""Explicit project configuration and readback for optional vocabulary guidance."""
import base64
import json
import os
from pathlib import Path
import uuid
from .installation import project_binding, project_path, store_path
from .vocabulary_contracts import VocabularyPolicy, VocabularySnapshot
from .vocabulary_context import validate_snapshot, publish_snapshot, vocabulary_context, load_snapshot
from .models import ConflictError, identity, canonical


def configure_project(config, corpus, project, *, enabled, vocabulary_file=None, workspace=None):
    binding = project_binding(config, corpus, project)
    directory = project_path(config, corpus, project)
    scope = dict(corpus_id=corpus, project_id=project)
    policy = VocabularyPolicy.model_validate(binding.get("normalized_reasoning", {}))
    if vocabulary_file:
        if not enabled:
            raise ValueError("Bind a vocabulary with normalized reasoning enabled")
        path = Path(vocabulary_file)
        if path.is_symlink() or path.stat().st_size > 5_000_000:
            raise ValueError("Vocabulary must be a bounded regular JSON file")
        snapshot = VocabularySnapshot.model_validate_json(path.read_bytes())
        from .corpus_snapshot import CorpusSnapshot
        with CorpusSnapshot(store_path(config), **scope) as reader:
            validate_snapshot(reader, snapshot, **scope)
        from .storage import GraphStore
        try:
            store = GraphStore(store_path(config))
        except ConflictError as exc:
            if str(exc) != "another service owns this corpus": raise
            from .owner_operations import call_owner
            result = call_owner(binding.get("mcp_url"), "Save Research Analysis", {
                "mode": "save_vocabulary", "operation_id": identity(("vocabulary", snapshot)),
                "graph_revision": snapshot.graph_revision.model_dump(mode="json"),
                "vocabulary_snapshot": snapshot.model_dump(mode="json")})
            if result.status != "complete":
                raise ValueError("Vocabulary publication failed")
            policy = VocabularyPolicy.model_validate(result.data["normalized_reasoning"])
            with CorpusSnapshot(store_path(config), **scope) as reader:
                load_snapshot(reader, policy, **scope)
        else:
            try: policy = publish_snapshot(store, snapshot, **scope)
            finally: store.close()
    policy = policy.model_copy(update={"enabled": enabled})
    updated = {**binding, "normalized_reasoning": policy.model_dump(mode="json")}
    targets = {directory / "project.json": updated}
    for kind in ("math", "proof"):
        path = Path(binding[kind + "_config"])
        value = json.loads(path.read_text())
        if (value.get("corpus_id", "papers"), value.get("project_id")) != (corpus, project):
            raise ConflictError("Private configuration scope mismatch")
        targets[path] = {**value, "normalized_reasoning": policy.model_dump(mode="json")}
    if workspace:
        path = Path(workspace).resolve() / ".nima/project.json"
        if path.exists():
            value = json.loads(path.read_text())
            if (value.get("corpus_id"), value.get("project_id")) != (corpus, project):
                raise ConflictError("Workspace project scope mismatch")
            targets[path] = {**value, "normalized_reasoning": policy.model_dump(mode="json")}
    from .config_reconciliation import apply_reconciliation, file_identity
    changes = []
    for path, value in targets.items():
        raw = canonical(value) + b"\n"
        changes.append({"path": str(path.resolve()), "expected_sha256": file_identity(path),
            "payload_base64": base64.b64encode(raw).decode(), "after_sha256": __import__('hashlib').sha256(raw).hexdigest(),
            "mode": 0o600, "owner_uid": path.stat().st_uid, "owner_gid": path.stat().st_gid})
    preview = {"format_version": 1, "scope": {"corpus_id": corpus, "project_id": project}, "inputs": [], "changes": changes}
    preview["preview_hash"] = identity(preview)
    result = apply_reconciliation(preview, directory / "vocabulary-config-recovery" / uuid.uuid4().hex)
    return {**result, "normalized_reasoning": policy.model_dump(mode="json"),
        "note": "Settings apply to new investigations. Existing sessions retain their pin; restart configured owners to reload changed configuration files."}
