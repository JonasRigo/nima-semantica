"""Deterministic, framework-independent boundaries for user-owned flows.

Adapters never execute user expressions, infer citations, or admit graph data.
Langflow components own transport conversion; this module only accepts JSON.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel, canonical, identity
from .normalization_contracts import (
    FieldMapping, NormalizedRequest, NormalizedOutput, NormalizedProposal,
    NormalizationDiagnostic,
)
from .okf_contracts import GraphRevision


class AdapterContext(StrictModel):
    schema_version: Literal[1] = 1
    request: NormalizedRequest
    registry_revision: str | None = None
    source_region_ids: tuple[str, ...] = ()
    chunk_bindings: dict[str, str] = Field(default_factory=dict)
    relation_bindings: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scope(self):
        revision = self.request.graph_revision
        if revision is not None and (revision.corpus_id, revision.project_id) != (
            self.request.corpus_id, self.request.project_id
        ):
            raise ValueError("graph revision scope differs from request")
        if len(set(self.source_region_ids)) != len(self.source_region_ids):
            raise ValueError("duplicate source regions")
        if not set(self.chunk_bindings.values()).issubset(self.source_region_ids):
            raise ValueError("chunk bindings must reference declared source regions")
        return self


class AdaptedOutput(StrictModel):
    normalized: NormalizedOutput
    raw_output: Any
    raw_sha256: str
    mapped_output: dict[str, Any]
    # Not a receipt or evidence artifact: this is an in-memory adapter result.
    persisted: Literal[False] = False


def map_fields(value: dict, bindings: list[FieldMapping]) -> dict:
    """Explicit dotted paths; root is '$'. No eval, wildcards or coercion."""
    if not bindings:
        return copy.deepcopy(value)
    result = {}
    targets = []
    for binding in bindings:
        if binding.transform is not None or binding.lossy:
            raise ValueError("transforms and lossy mappings require a dedicated reviewed adapter")
        path = binding.target_path.split(".")
        if any(not item or item.startswith("_") or item == "$" for item in path):
            raise ValueError("invalid target path")
        if any(path[:len(old)] == old or old[:len(path)] == path for old in targets):
            raise ValueError("overlapping mapping targets")
        targets.append(path)
        item = value
        try:
            for key in (() if binding.source_path == "$" else binding.source_path.split(".")):
                item = item[key]
        except (KeyError, TypeError):
            if binding.required:
                raise ValueError(f"missing required field: {binding.source_path}") from None
            continue
        target = result
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = copy.deepcopy(item)
    return result


def input_context(value: Any, *, corpus_id: str, project_id: str | None = None,
                  request_id: str, adapter_id: str = "user-flow", objective: str | None = None,
                  graph_revision: GraphRevision | None = None, ontology_profile: str | None = None,
                  registry_revision: str | None = None, source_region_ids=(), chunk_bindings=None,
                  relation_bindings=None, bindings=()) -> tuple[dict, AdapterContext]:
    payload = value if isinstance(value, dict) else {"text": value} if isinstance(value, str) else {"rows": value}
    # canonical rejects values that cannot cross the JSON transport boundary.
    json.loads(canonical(payload))
    context = AdapterContext(
        request=NormalizedRequest(request_id=request_id, adapter_id=adapter_id,
            corpus_id=corpus_id, project_id=project_id, objective=objective,
            graph_revision=graph_revision, ontology_profile=ontology_profile, payload=copy.deepcopy(payload)),
        registry_revision=registry_revision, source_region_ids=source_region_ids,
        chunk_bindings=chunk_bindings or {},
        relation_bindings=relation_bindings or {},
    )
    return map_fields(payload, list(bindings)), context


def normalize_output(raw: Any, context: AdapterContext, *, preset="record", bindings=(), store=None) -> AdaptedOutput:
    """Preserve raw responses, including invalid graph output, without certification.

    Graph conversion is a separate evidence-validation step. A model's chunk IDs
    and 'discharged' fields are assertions, not authenticated NIMA evidence.
    """
    if preset not in ("text", "record", "graph_extraction"):
        raise ValueError("unknown output preset")
    raw_bytes = canonical(raw)
    diagnostics = []
    proposals = ()
    mapped = {}
    status = "complete"
    delta = None
    try:
        value = raw if isinstance(raw, dict) else {"text": raw} if isinstance(raw, str) else {"rows": raw}
        mapped = map_fields(value, list(bindings))
        if preset == "graph_extraction":
            # Preserve all reference-schema fields without treating foreign IDs
            # or scientific statuses as admitted graph claims.
            status = "partial"
            diagnostics.append(NormalizationDiagnostic(
                diagnostic_id=identity({"request": context.request.request_id, "code": "grounding_required"}),
                code="normalizer.grounding_required", severity="warning",
                message="Extraction retained as a record proposal; exact source and ontology validation is required before graph conversion.",
            ))
            if store is not None:
                from .reference_graph_adapter import reference_graph_delta
                try:
                    delta = reference_graph_delta(mapped, context, store)
                    status = "complete"
                    diagnostics.clear()
                except (ValueError, KeyError, TypeError) as error:
                    diagnostics[0] = diagnostics[0].model_copy(update={"message": str(error)})
        proposals = (NormalizedProposal(
            proposal_id=identity({"context": context.model_dump(mode="json"), "preset": preset, "output": mapped}),
            kind="artifact" if preset == "text" else "record", payload=mapped,
        ),)
    except (ValueError, TypeError) as error:
        status = "failed"
        diagnostics.append(NormalizationDiagnostic(
            diagnostic_id=identity({"request": context.request.request_id, "code": "mapping_failed"}),
            code="normalizer.mapping_failed", severity="error", message=str(error),
        ))
    req = context.request
    normalized = NormalizedOutput(request_id=req.request_id, adapter_id=req.adapter_id,
        corpus_id=req.corpus_id, project_id=req.project_id, graph_revision=req.graph_revision,
        status=status, proposals=proposals, diagnostics=diagnostics, graph_delta=delta,
        metadata={"preset": preset, "persisted": False, "raw_sha256": hashlib.sha256(raw_bytes).hexdigest()},
    )
    return AdaptedOutput(normalized=normalized, raw_output=copy.deepcopy(raw),
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(), mapped_output=mapped)


def retrieve_context(context: AdapterContext, query: str, *, store, provider, policy=None):
    """Invoke actual GraphRAG through its maintained service, never canned output."""
    from .workflow_contracts import GraphRetrievalNormalizerRequest, GraphRetrievalPolicy
    from .workflow_services import GraphRetrievalNormalizationService
    req = context.request
    if req.graph_revision is None:
        raise ValueError("retrieval requires a revision-bound input context")
    return GraphRetrievalNormalizationService().execute(store, provider,
        GraphRetrievalNormalizerRequest(request_id=req.request_id, corpus_id=req.corpus_id,
            project_id=req.project_id, graph_revision=req.graph_revision, query=query,
            policy=policy or GraphRetrievalPolicy()))
