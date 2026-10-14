"""Read-only index preflight and explicit complete projection recovery."""
from __future__ import annotations
import json
import os
from .artifact_diagnostics import SourceValidationError, ArtifactReadError
from .evidence_contracts import require_source_region
from .corpus import validate_vectors
from .graph_projection import GraphProjectionRequest, GraphProjectionService
from .models import identity, NimaError
from .projection_limits import ProjectionLimits
from .providers import ModelManifest


def check_index(store, *, corpus_id, project_id=None, limits=None):
    """Validate visible sources without receipts, embeddings or writer ownership."""
    limits = ProjectionLimits.model_validate(limits or {})
    scope = dict(corpus_id=corpus_id, project_id=project_id)
    diagnostics, regions, tokens = [], 0, 0
    for key, record in store.iter_records("SourceRegion", **scope):
        if record.project_id not in (None, project_id):
            continue
        regions += 1
        tokens += sum(1 for _ in GraphProjectionService._TOKEN.finditer(record.content.get("text", "").casefold()))
        try:
            require_source_region(store, key, **scope)
        except (SourceValidationError, ArtifactReadError) as exc:
            diagnostics.extend(getattr(exc, "diagnostics", (getattr(exc, "diagnostic", {}),)))
        except (ValueError, NimaError):
            diagnostics.append({"code": "source.invalid", "region_id": key,
                "message": "Source identity or exact source validation failed."})
    for _, batch in store.iter_records("EmbeddingBatch", **scope):
        if batch.project_id not in (None, project_id):
            continue
        try:
            validate_vectors(json.loads(store.read_artifact(batch.content["matrix_artifact"])),
                len(batch.content["region_ids"]), ModelManifest.model_validate(batch.content["manifest"]))
            for key in batch.content["region_ids"]:
                require_source_region(store, key, **scope)
        except (SourceValidationError, ArtifactReadError) as exc:
            diagnostics.extend(getattr(exc, "diagnostics", (getattr(exc, "diagnostic", {}),)))
        except (ValueError, NimaError, KeyError):
            diagnostics.append({"code": "embedding.invalid", "message": "Stored embedding identity, matrix or source validation failed."})
    for resource, count, maximum in (("token", tokens, limits.max_tokens), ("region", regions, limits.max_regions)):
        if count > maximum:
            from .projection_limits import ProjectionLimitError
            diagnostics.append(ProjectionLimitError(resource, maximum, count).diagnostic)
    # Deduplicate repeated artifact failures while retaining each affected region.
    diagnostics = list({identity(d): d for d in diagnostics}.values())
    return {**scope, "ready": not diagnostics, "regions": regions, "tokens": tokens,
        "limits": limits.model_dump(), "reader_uid": os.getuid(), "diagnostics": diagnostics,
        "graph_revision": store.graph_revision(**scope).model_dump(mode="json"),
        "source_revision": str(store.embedding_revision(corpus_id)),
        "authority": "read_only_preflight", "scientific_status": "uncertified"}


def rebuild_index(store, *, corpus_id, project_id=None, limits=None, attempt_id=None):
    limits = ProjectionLimits.model_validate(limits or {})
    checked = check_index(store, corpus_id=corpus_id, project_id=project_id, limits=limits)
    if not checked["ready"]:
        return {**checked, "status": "failed", "index_ready": False}
    request = GraphProjectionRequest(corpus_id=corpus_id, project_id=project_id,
        graph_revision=checked["graph_revision"], source_revision=checked["source_revision"],
        **limits.model_dump(), idempotency_key=attempt_id)
    result = GraphProjectionService(store).rebuild(request)
    if result.status == "completed":
        GraphProjectionService(store).get_current(result.projection_id, corpus_id=corpus_id, project_id=project_id)
    return {**checked, **result.model_dump(mode="json"), "index_ready": result.status == "completed"}
