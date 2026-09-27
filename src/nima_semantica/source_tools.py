"""Inspectable source preparation stages over the existing v2 services.

Each write stage is atomic and receipted. Later failures retain earlier completed
stages; a partial result is never advertised as a ready vector index.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from asyncio import CancelledError
from pathlib import PurePath
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .corpus import normalize, regions, validate_vectors
from .corpus_registry import CorpusRegistry
from .document_ingestion import DocumentIngestionService
from .embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
from .evidence_contracts import require_source_region
from .execution_receipts import ExecutionReceiptService
from .graph_projection import GraphProjectionRequest, GraphProjectionService
from .models import ConflictError, NimaError, StrictModel, identity
from .okf_contracts import GraphIdentifier
from .providers import ModelManifest, validate_manifest
from .receipts import ExecutionReceipt
from .registry_contracts import RegistryRevision
from .source_corpus import SourceDescriptor
from .tool_contracts import ToolResult


MEDIA = {".txt": "text/plain", ".md": "text/markdown", ".tex": "application/x-tex",
    ".json": "application/json", ".html": "text/html", ".htm": "text/html", ".pdf": "application/pdf"}


class SourceInput(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    text: str | None = Field(default=None, max_length=2_000_000)
    data_base64: str | None = Field(default=None, max_length=2_800_000)

    @model_validator(mode="after")
    def source_input(self):
        if "/" in self.name or "\\" in self.name or PurePath(self.name).suffix.lower() not in MEDIA:
            raise ValueError("source name must be a filename with a supported extension, never a path or URL")
        if (self.text is None) == (self.data_base64 is None):
            raise ValueError("provide exactly one of text or data_base64")
        return self

    def bytes(self):
        data = self.text.encode("utf-8") if self.text is not None else base64.b64decode(self.data_base64, validate=True)
        if not data or len(data) > 2_000_000:
            raise ValueError("source must contain 1..2000000 bytes")
        if self.name.lower().endswith(".pdf") and not data.startswith(b"%PDF-"):
            raise ValueError("invalid PDF header")
        return data


class AcquiredSourceInput(StrictModel):
    """A scoped, already archived acquisition; no source bytes in the request."""
    name: str = Field(min_length=1, max_length=256)
    acquired_name: str = Field(min_length=1, max_length=1024)
    artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    acquisition_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def source_name(self):
        if "/" in self.name or "\\" in self.name or PurePath(self.name).suffix.lower() not in MEDIA:
            raise ValueError("source name must be a filename with a supported extension, never a path or URL")
        return self


MAX_ACQUIRED_SOURCE_BYTES = 20_000_000


def _source_bytes(source, store, context):
    if isinstance(source, SourceInput):
        return source.bytes()
    if store is None or context is None:
        raise NimaError("acquired source requires a scoped store and receipted preparation")
    scope = {"corpus_id": context.corpus_id, "project_id": context.project_id}
    attempt = store.get(source.acquisition_id, **scope)
    document = store.get(source.document_id, **scope) if source.document_id else None
    if (attempt is None or attempt.kind != "AcquisitionAttempt" or
        attempt.project_id != context.project_id or attempt.content.get("status") != "completed" or
        attempt.content.get("artifact_id") != source.artifact_id or
        attempt.content.get("name") != source.acquired_name or
        (source.document_id is None and (not attempt.content.get("staging_only") or attempt.parents)) or
        (source.document_id is not None and (
            source.document_id not in attempt.parents or
            document is None or document.kind != "Document" or
            document.project_id != context.project_id or
            document.content.get("artifact_id") != source.artifact_id))):
        raise ConflictError("acquired source identity or scope does not match the archived acquisition")
    data = store.read_artifact(source.artifact_id)
    if not data or len(data) > MAX_ACQUIRED_SOURCE_BYTES:
        raise NimaError("acquired source exceeds preparation size limit")
    if source.name.lower().endswith(".pdf") and not data.startswith(b"%PDF-"):
        raise ValueError("invalid PDF header")
    return data


class PrepareSourcesRequest(StrictModel):
    mode: Literal["preview", "prepare_index"] = "preview"
    sources: tuple[SourceInput | AcquiredSourceInput, ...] = Field(min_length=1, max_length=16)
    index_mode: Literal["lexical", "vector"] = "lexical"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    expected_store_revision: GraphIdentifier | None = None

    @model_validator(mode="after")
    def controls(self):
        if self.mode == "prepare_index" and self.operation_id is None:
            raise ValueError("preparation requires an operation_id")
        if self.mode == "preview" and (self.operation_id is not None or self.expected_store_revision is not None):
            raise ValueError("preview does not accept write controls")
        if sum(len(item.text or item.data_base64 or "") for item in self.sources
            if isinstance(item, SourceInput)) > 8_000_000:
            raise ValueError("source request exceeds transport limit")
        return self


class SourceToolContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    actor: GraphIdentifier = "harness"
    allow_corpus_writes: StrictBool = False
    source_scope: Literal["corpus", "project"] = "corpus"
    allow_project_writes: StrictBool = False
    allow_embeddings: StrictBool = False
    allow_pdf: StrictBool = False


def failure(code, *, status="failed", data=None, receipts=()):
    return ToolResult(operation="Prepare and Index Sources", status=status, data=data or {}, receipt_ids=receipts,
        diagnostics=({"code": code, "message": "Stage did not complete. Inspect input, operator configuration, scope, and revision."},),
        note="Preparation/index readiness is not source fidelity certification or scientific verification.")


def _scope(context):
    return {"corpus_id": context.corpus_id, "project_id": context.project_id}


def _attempt(store, context, stage, operation_id, payload, work, *, run_id=None, expected=None, previous=None):
    if not (context.allow_corpus_writes if context.source_scope == "corpus" else context.allow_project_writes and context.project_id):
        return failure("sources.write_not_authorized", status="partial" if previous else "failed",
            data={**previous.data, "index_ready": False} if previous else None,
            receipts=previous.receipt_ids if previous else ())
    if store is None:
        return failure("sources.store_not_configured", status="unavailable")
    receipts = ExecutionReceiptService(store)
    identifier = identity({"stage": stage, "operation_id": operation_id, **_scope(context)})
    request_hash = identity({"payload": payload, "context": context})
    with store.joined_transaction():
        replay = receipts.replay(identifier, **_scope(context), request_hash=request_hash)
        if replay is not None:
            return ToolResult.model_validate(replay.metadata["result"])
    try:
        with store.joined_transaction(expected):
            replay = receipts.replay(identifier, **_scope(context), request_hash=request_hash)
            if replay is not None:
                return ToolResult.model_validate(replay.metadata["result"])
            if run_id is not None:
                from .research_run_service import ResearchRunService
                if ResearchRunService(store).get_run(run_id, **_scope(context)) is None:
                    raise ConflictError("run is outside the authorized scope")
            result = work()
            result = result.model_copy(update={"receipt_ids": tuple(dict.fromkeys((*result.receipt_ids, identifier)))})
            receipts.record(ExecutionReceipt(receipt_id=identifier, operation_id=operation_id, stage=stage,
                **_scope(context), run_id=run_id, status="completed", tool_version="source-tools-v1",
                metadata={"request_hash": request_hash, "result": result.model_dump(mode="json"), "actor": context.actor}))
            return result
    except (Exception, CancelledError, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, (CancelledError, KeyboardInterrupt))
        result = failure("sources." + stage + "." + type(exc).__name__,
            status="partial" if previous is not None else "failed",
            data={**(previous.data if previous else {}), "failed_stage": stage, "index_ready": False},
            receipts=(*(previous.receipt_ids if previous else ()), identifier))
        receipts.record(ExecutionReceipt(receipt_id=identifier, operation_id=operation_id, stage=stage,
            **_scope(context), run_id=run_id, status="interrupted" if interrupted else "failed",
            error="source pipeline stage failed", diagnostics=result.diagnostics, tool_version="source-tools-v1",
            metadata={"request_hash": request_hash, "result": result.model_dump(mode="json"), "actor": context.actor}))
        if interrupted:
            raise
        return result


def _normalize(source, pdf_normalizer, *, store=None, context=None):
    data = _source_bytes(source, store, context)
    text, diagnostics = normalize(data, source.name, pdf_normalizer)
    if not text.strip() or len(text.encode("utf-8")) > 4_000_000:
        raise NimaError("empty or oversized normalized source")
    diagnostics = copy.deepcopy(diagnostics)
    artifacts = {}
    for diagnostic in diagnostics:
        bundle = diagnostic.pop("artifact_bundle", [])
        if bundle:
            diagnostic["artifact_ids"] = []
        for artifact in bundle:
            content = base64.b64decode(artifact["data_base64"], validate=True)
            digest = hashlib.sha256(content).hexdigest()
            if digest != artifact["sha256"]:
                raise NimaError("PDF evidence artifact integrity mismatch")
            artifacts[digest] = content
            diagnostic["artifact_ids"].append(digest)
    if sum(len(data) for data in artifacts.values()) > 20_000_000:
        raise NimaError("PDF evidence bundle exceeds input limit")
    return data, text, diagnostics, artifacts


def prepare_sources(store, request: PrepareSourcesRequest, context: SourceToolContext, *, pdf_normalizer=None):
    request = PrepareSourcesRequest.model_validate(request.model_dump(mode="json"))
    context = SourceToolContext.model_validate(context.model_dump(mode="json"))
    if request.mode == "preview":
        # No external PDF worker or embedding calls in a no-receipt preview.
        try:
            previews = []
            for source in request.sources:
                if isinstance(source, AcquiredSourceInput):
                    return failure("sources.acquired_requires_receipted_preparation", status="unavailable")
                if source.name.lower().endswith(".pdf"):
                    return failure("sources.pdf_requires_receipted_preparation", status="unavailable")
                data, text, diagnostics, _ = _normalize(source, None)
                chunks = regions(text, hashlib.sha256(text.encode()).hexdigest(), "preview", context.corpus_id)
                previews.append({"name": source.name, "original_sha256": hashlib.sha256(data).hexdigest(),
                    "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(), "region_count": len(chunks),
                    "text_preview": text[:2000], "preview_truncated": len(text) > 2000,
                    "diagnostic_count": len(diagnostics)})
            return ToolResult(operation="Prepare and Index Sources", status="complete",
                data={"mode": "preview", "sources": previews, "index_ready": False},
                note="No source was stored, no provider called, and no index created. PDF requires explicit receipted preparation.")
        except (Exception,):
            return failure("sources.invalid_preview")

    def work():
        registry = CorpusRegistry(store)
        source_project = context.project_id if context.source_scope == "project" else None
        if registry.corpus(context.corpus_id) is None:
            raise ConflictError("corpus must be registered before preparation")
        if any(s.name.lower().endswith(".pdf") for s in request.sources) and (not context.allow_pdf or pdf_normalizer is None):
            raise NimaError("PDF worker is not explicitly configured and authorized")
        all_regions, prepared, service_receipts = [], [], []
        unresolved_region_count = 0
        for source in request.sources:
            data, text, diagnostics, artifacts = _normalize(source, pdf_normalizer, store=store, context=context)
            unresolved = sum(item.get("status") == "unresolved" for item in diagnostics)
            if source.name.lower().endswith(".pdf"):
                manifests = [item for item in diagnostics if item.get("kind") == "parser_manifest"]
                if manifests:
                    count = manifests[-1].get("mathematics", {}).get("unresolved_formula_count", 0)
                    if type(count) is not int or count < 0:
                        raise NimaError("invalid PDF unresolved formula count")
                    unresolved = max(unresolved, count)
            unresolved_region_count += unresolved
            original = hashlib.sha256(data).hexdigest()
            normalized = hashlib.sha256(text.encode()).hexdigest()
            source_identity = {"corpus_id": context.corpus_id, "artifact": original, "name": source.name}
            if source_project is not None:
                source_identity["project_id"] = source_project
            source_id = identity(source_identity)
            # A single immutable source identity owns normalization and region locators.
            descriptor = SourceDescriptor(source_id=source_id, corpus_id=context.corpus_id, project_id=source_project, artifact_id=original,
                source_revision=original, name=source.name, media_type=MEDIA[PurePath(source.name).suffix.lower()])
            resource_ids = tuple(dict.fromkeys((original, normalized, *artifacts)))
            existing = [registry.resolve(i, corpus_id=context.corpus_id, project_id=source_project, exact_scope=True) for i in resource_ids]
            revisions = {entry.registry_revision for entry in existing if entry is not None}
            if revisions:
                if len(revisions) != 1 or any(e is None or e.project_id != source_project for e in existing):
                    raise ConflictError("source artifacts have incompatible existing publication bindings")
                revision_id = revisions.pop()
            else:
                revisions = [RegistryRevision.model_validate(r.content) for _, r in store.records(
                    registry.REVISION_KIND, corpus_id=context.corpus_id)]
                parent = max(revisions, key=lambda r: r.sequence) if revisions else None
                revision_id = identity({"source": source_id, "resources": resource_ids, "parent": parent})
                registry.register_revision(RegistryRevision(revision_id=revision_id, corpus_id=context.corpus_id,
                    sequence=parent.sequence + 1 if parent else 0, parent_revision=parent.revision_id if parent else None,
                    changed_resource_ids=resource_ids))
            for digest, content in artifacts.items():
                ArtifactService(store).publish(content, ArtifactEnvelope(artifact_id=digest, content_hash=digest,
                    artifact_kind="pdf_evidence", media_type="application/octet-stream", corpus_id=context.corpus_id, project_id=source_project,
                    content={"source_id": source_id}), registry_revision=revision_id)
            # Reuse the worker result, not a second PDF invocation. Plain-text
            # normalization is deterministic and repeated by the ingestion service.
            result = DocumentIngestionService(store).ingest(data, descriptor,
                ArtifactEnvelope(artifact_id=original, content_hash=original, artifact_kind="source",
                    media_type=descriptor.media_type, corpus_id=context.corpus_id, project_id=source_project, source_revision=original),
                registry_revision=revision_id, pdf_normalizer=lambda _: (text, diagnostics))
            if result.normalized_artifact_id != normalized:
                raise NimaError("normalization identity changed before publication")
            for identifier in result.region_ids:
                require_source_region(store, identifier, **_scope(context))
            all_regions.extend(result.region_ids)
            if len(all_regions) > 4096:
                raise NimaError("too many regions in one preparation request")
            service_receipts.append(result.receipt_id)
            prepared.append({"source_id": source_id, "name": source.name, "original_artifact_id": original,
                "normalized_artifact_id": normalized, "region_count": len(result.region_ids),
                "diagnostic_count": len(diagnostics), "unresolved_region_count": unresolved,
                "normalization_complete": unresolved == 0, "registry_revision": revision_id})
        return ToolResult(operation="Prepare and Index Sources", status="complete", receipt_ids=tuple(service_receipts),
            data={"mode": request.mode, "stage": "source_preparation", "operation_id": request.operation_id,
                "run_id": request.run_id, "scope": _scope(context), "index_mode": request.index_mode,
                "sources": prepared, "region_ids": list(dict.fromkeys(all_regions)), "index_ready": False,
                "normalization_complete": unresolved_region_count == 0,
                "unresolved_region_count": unresolved_region_count},
            note="Immutable source preparation completed in the requested scope; indexing has not completed. PDF diagnostics remain attached to normalized documents/regions.")
    return _attempt(store, context, "source_preparation", request.operation_id, request, work,
        run_id=request.run_id, expected=request.expected_store_revision)


def _upstream(store, incoming, context, stage):
    incoming = ToolResult.model_validate(incoming.model_dump(mode="json"))
    if incoming.status != "complete" or incoming.data.get("mode") == "preview":
        return None
    if store is None or incoming.data.get("scope") != _scope(context) or incoming.data.get("stage") != stage:
        raise ConflictError("invalid upstream source scope or stage")
    if not incoming.receipt_ids:
        raise ConflictError("upstream source stage is not receipted")
    receipt = ExecutionReceiptService(store).get(incoming.receipt_ids[-1], **_scope(context))
    if receipt is None or receipt.status != "completed" or receipt.stage != stage or receipt.metadata.get("result") != incoming.model_dump(mode="json"):
        raise ConflictError("upstream source result does not match persisted attempt")
    return incoming


def embed_sources(store, incoming: ToolResult, context: SourceToolContext, *, provider=None, manifest: ModelManifest | None = None):
    context = SourceToolContext.model_validate(context.model_dump(mode="json"))
    try:
        upstream = _upstream(store, incoming, context, "source_preparation")
    except (ValueError, NimaError):
        return failure("sources.invalid_upstream")
    if upstream is None:
        return incoming
    def work():
        receipts = list(incoming.receipt_ids)
        batches = []
        if incoming.data["index_mode"] == "vector":
            if not context.allow_embeddings or provider is None or manifest is None:
                raise NimaError("embedding provider is not explicitly connected and authorized")
            validate_manifest(manifest)
            if manifest.dimension is None or not 1 <= manifest.dimension <= 4096:
                raise NimaError("embedding dimension is missing or unsupported")
            ids = incoming.data["region_ids"]
            for start in range(0, len(ids), 256):
                request = EmbeddingIndexRequest(**_scope(context), region_ids=tuple(ids[start:start + 256]),
                    manifest=manifest, idempotency_key=identity({"upstream": incoming.receipt_ids[-1],
                        "manifest": manifest, "offset": start, "stage": "source_embeddings"}))
                result = EmbeddingIndexService(store).embed_and_index(request, provider)
                if result.status != "completed":
                    raise NimaError("embedding publication failed; no new batches committed")
                batches.append(result.result["embedding_batch_id"])
                receipts.append(result.receipt_id)
        return ToolResult(operation="Prepare and Index Sources", status="complete", receipt_ids=tuple(receipts),
            data={**incoming.data, "stage": "source_embeddings", "embedding_batch_ids": batches,
                "embedding_manifest": manifest.model_dump(mode="json") if batches else None},
            note="Embeddings are bound to exact source-region text and an operator-declared model identity; lexical mode makes no embedding calls.")
    return _attempt(store, context, "source_embeddings", incoming.data["operation_id"],
        {"upstream": incoming, "manifest": manifest}, work, run_id=incoming.data.get("run_id"), previous=incoming)


def project_sources(store, incoming: ToolResult, context: SourceToolContext):
    context = SourceToolContext.model_validate(context.model_dump(mode="json"))
    try:
        upstream = _upstream(store, incoming, context, "source_embeddings")
    except (ValueError, NimaError):
        return failure("sources.invalid_upstream")
    if upstream is None:
        return incoming
    def work():
        service = GraphProjectionService(store)
        # Revalidate every visible source before including it in a derived index.
        for identifier, record in store.records("SourceRegion", **_scope(context)):
            if record.project_id in (None, context.project_id):
                require_source_region(store, identifier, **_scope(context))
        for _, batch in store.records("EmbeddingBatch", **_scope(context)):
            if batch.project_id not in (None, context.project_id):
                continue
            validate_vectors(json.loads(store.read_artifact(batch.content["matrix_artifact"])),
                len(batch.content["region_ids"]), ModelManifest.model_validate(batch.content["manifest"]))
            for identifier in batch.content["region_ids"]:
                require_source_region(store, identifier, **_scope(context))
        projected = service.rebuild(GraphProjectionRequest(**_scope(context), idempotency_key=identity({
            "upstream": incoming.receipt_ids[-1], "stage": "source_projection"})))
        if projected.status != "completed" or projected.manifest is None:
            raise NimaError("projection publication failed")
        service.get_current(projected.projection_id, **_scope(context))
        if incoming.data["index_mode"] == "vector":
            vector = projected.manifest.vector_manifest
            if vector is None or not set(incoming.data["region_ids"]) <= set(vector.indexed_artifact_ids):
                raise NimaError("requested source regions are not fully vector-indexed")
        return ToolResult(operation="Prepare and Index Sources",
            status="partial" if incoming.data.get("unresolved_region_count", 0) else "complete",
            receipt_ids=(*incoming.receipt_ids, projected.receipt_id), data={**incoming.data,
                "stage": "source_projection", "index_ready": True,
                "projection": projected.manifest.model_dump(mode="json")},
            note="Requested index mode completed at the returned revisions. Lexical readiness is not vector readiness; readiness never certifies scientific correctness or PDF transcription fidelity.")
    return _attempt(store, context, "source_projection", incoming.data["operation_id"], incoming, work,
        run_id=incoming.data.get("run_id"), previous=incoming)
