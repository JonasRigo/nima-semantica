"""Revision-bound rebuildable projections for authorized corpus/project graphs."""

from __future__ import annotations

from .okf_contracts import GraphRevision

import re
from typing import Any, Literal

from pydantic import Field

from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, NimaError, Record, StrictModel, canonical, identity
from .receipts import ExecutionReceipt
from .retrieval_contracts import EmbeddingProjectionManifest


ProjectionKind = Literal["lexical", "vector", "structural", "summary"]


class GraphProjectionRequest(StrictModel):
    """Scope and revision pin for one complete projection rebuild."""

    corpus_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: str | None = Field(default=None, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
    graph_revision: GraphRevision | None = None
    source_revision: str | None = Field(default=None, min_length=1, max_length=256)
    projection_id: str | None = Field(default=None, min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, pattern=r"^\S+$", max_length=256)
    max_regions: int = Field(default=100_000, ge=1, le=1_000_000)
    max_tokens: int = Field(default=1_000_000, ge=1, le=10_000_000)


class GraphProjectionManifest(StrictModel):
    """Identity and provenance of a rebuildable projection bundle."""

    schema_version: Literal[1] = 1
    projection_id: str
    corpus_id: str
    project_id: str | None = None
    graph_revision: GraphRevision
    source_revision: str
    projection_revision: str
    kinds: tuple[ProjectionKind, ...] = ("lexical", "vector", "structural", "summary")
    artifact_ids: dict[str, str] = Field(min_length=4, max_length=4)
    vector_manifest: EmbeddingProjectionManifest | None = None


class GraphProjectionResult(StrictModel):
    operation_id: str
    projection_id: str
    corpus_id: str
    project_id: str | None = None
    status: Literal["completed", "failed"]
    manifest: GraphProjectionManifest | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_id: str


class GraphProjectionService:
    """Build immutable lexical/vector/structural/summary projection artifacts.

    The projection is derived control-plane data. It never changes the OKF graph,
    admits proposals, or treats an index as authoritative scientific evidence.
    """

    stage = "graph_projection"
    _TOKEN = re.compile(r"[\w][\w'’-]*", re.UNICODE)

    def __init__(self, store, *, receipts: ExecutionReceiptService | None = None):
        self.store = store
        self.receipts = receipts or ExecutionReceiptService(store)

    def rebuild(self, request: GraphProjectionRequest) -> GraphProjectionResult:
        # Implicit refreshes target current knowledge, not the first historical
        # invocation of an otherwise identical corpus/project request.
        if request.idempotency_key is None:
            request = request.model_copy(update={
                "graph_revision": request.graph_revision or self.store.graph_revision(request.corpus_id, request.project_id),
                "source_revision": request.source_revision or self._source_revision(request.corpus_id),
            })
        operation_id = request.idempotency_key or identity({"stage": self.stage, "request": request.model_dump(mode="json")})
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        previous = self.receipts.get(receipt_id, corpus_id=request.corpus_id, project_id=request.project_id)
        if previous is not None:
            metadata = previous.metadata
            manifest = metadata.get("manifest")
            return GraphProjectionResult(
                operation_id=operation_id,
                projection_id=(manifest or {}).get("projection_id", request.projection_id or operation_id),
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                status=previous.status,
                manifest=GraphProjectionManifest.model_validate(manifest) if manifest else None,
                diagnostics=previous.diagnostics,
                receipt_id=receipt_id,
            )

        try:
            store_revision = self.store.revision
            graph_revision = request.graph_revision or self.store.graph_revision(request.corpus_id, request.project_id)
            if graph_revision != self.store.graph_revision(request.corpus_id, request.project_id):
                raise ConflictError("projection request targets a stale graph revision")
            snapshot = self.store.read_okf_snapshot(
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                revision=graph_revision,
            )
            regions = self._regions(request)
            if len(regions) > request.max_regions:
                raise NimaError("projection region budget exceeded")
            source_revision = request.source_revision or self._source_revision(request.corpus_id)
            if request.source_revision is not None and request.source_revision != self._source_revision(request.corpus_id):
                raise ConflictError("projection request targets a stale source revision")

            lexical = self._lexical(regions, request.max_tokens)
            vector, vector_manifest = self._vector(regions, request)
            structural = self._structural(snapshot)
            summary = self._summary(snapshot, regions)
            payloads = {"lexical": lexical, "vector": vector, "structural": structural, "summary": summary}
            artifact_ids = {
                kind: self.store.artifact(canonical(payload))
                for kind, payload in payloads.items()
            }
            projection_id = request.projection_id or identity({
                "corpus_id": request.corpus_id,
                "project_id": request.project_id,
                "graph_revision": graph_revision,
                "source_revision": source_revision,
                "artifact_ids": artifact_ids,
            })
            projection_revision = identity({"projection_id": projection_id, "artifact_ids": artifact_ids})
            manifest = GraphProjectionManifest(
                projection_id=projection_id,
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                graph_revision=graph_revision,
                source_revision=source_revision,
                projection_revision=projection_revision,
                artifact_ids=artifact_ids,
                vector_manifest=vector_manifest,
            )
            record = Record(
                kind="GraphProjection",
                corpus_id=request.corpus_id,
                project_id=request.project_id,
                parents=tuple(sorted(set(artifact_ids.values()))),
                content={"manifest": manifest.model_dump(mode="json")},
            )
            with self.store.joined_transaction(store_revision):
                existing = self.store.get(record.id, corpus_id=request.corpus_id, project_id=request.project_id)
                if existing is None:
                    self.store.put(record)
            status, diagnostics, error = "completed", (), None
            output_ids = (record.id, *artifact_ids.values())
            result_manifest = manifest
        except Exception as exc:
            status, diagnostics, error = "failed", ({"code": type(exc).__name__, "message": str(exc)},), "graph projection rebuild failed"
            output_ids, result_manifest = (), None

        receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=operation_id,
            stage=self.stage,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            input_ids=(),
            output_ids=output_ids,
            status=status,
            error=error,
            diagnostics=diagnostics,
            tool_version="graph-projection-v1",
            idempotency_key=request.idempotency_key,
            metadata={"manifest": result_manifest.model_dump(mode="json") if result_manifest else None},
        )
        self.receipts.record(receipt)
        return GraphProjectionResult(
            operation_id=operation_id,
            projection_id=result_manifest.projection_id if result_manifest else request.projection_id or operation_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            status=status,
            manifest=result_manifest,
            diagnostics=diagnostics,
            receipt_id=receipt_id,
        )

    def get_current(self, projection_id: str, *, corpus_id: str, project_id: str | None = None) -> GraphProjectionManifest:
        """Resolve a projection only when it is current for its authorized scope."""
        records = self.store.records("GraphProjection", corpus_id=corpus_id, project_id=project_id)
        for _, record in records:
            manifest = GraphProjectionManifest.model_validate(record.content["manifest"])
            if manifest.projection_id != projection_id:
                continue
            if manifest.project_id != project_id:
                continue
            if not self._revision_is_current(manifest.graph_revision):
                raise ConflictError("projection is stale for the current graph revision")
            if manifest.source_revision != self._source_revision(corpus_id):
                raise ConflictError("projection is stale for the current source revision")
            return manifest
        raise NimaError("projection is unavailable in the requested scope")

    def _revision_is_current(self, graph_revision: GraphRevision) -> bool:
        return graph_revision == self.store.graph_revision(graph_revision.corpus_id, graph_revision.project_id)

    def _regions(self, request: GraphProjectionRequest) -> list[Record]:
        # Validate immutable records one at a time and retain only the original
        # canonical ID and text consumed by indexing. Book parser diagnostics
        # must not accumulate across thousands of regions.
        from types import SimpleNamespace
        return [SimpleNamespace(id=key, content={"text": record.content.get("text", "")})
            for key, record in self.store.iter_records("SourceRegion",
                corpus_id=request.corpus_id, project_id=request.project_id)
            if record.project_id in (None, request.project_id)]

    def _source_revision(self, corpus_id: str) -> str:
        return str(self.store.embedding_revision(corpus_id))

    def _lexical(self, regions: list[Record], max_tokens: int) -> dict[str, Any]:
        inverted: dict[str, list[str]] = {}
        token_count = 0
        for region in regions:
            text = region.content.get("text", "")
            if not isinstance(text, str):
                continue
            # Identity hashes the complete immutable record, including parser
            # diagnostics. Compute once per region, not twice for every token.
            region_id = region.id
            for token in self._TOKEN.findall(text.casefold()):
                token_count += 1
                if token_count > max_tokens:
                    raise NimaError("projection token budget exceeded")
                if region_id not in inverted.setdefault(token, []):
                    inverted[token].append(region_id)
        return {"version": 1, "token_count": token_count, "terms": {key: value for key, value in sorted(inverted.items())}}

    def _vector(self, regions: list[Record], request: GraphProjectionRequest) -> tuple[dict[str, Any], EmbeddingProjectionManifest | None]:
        batches = [record for _, record in self.store.records("EmbeddingBatch", corpus_id=request.corpus_id, project_id=request.project_id)
                   if record.project_id in (None, request.project_id)]
        if not batches:
            return {"version": 1, "batches": [], "indexed_region_ids": []}, None
        manifests = {canonical(batch.content.get("manifest")) for batch in batches}
        if len(manifests) != 1:
            raise ConflictError("vector projection contains mixed embedding manifests")
        manifest_data = batches[0].content["manifest"]
        region_ids = {region.id for region in regions}
        indexed = tuple(sorted({region_id for batch in batches for region_id in batch.content.get("region_ids", ()) if region_id in region_ids}))
        vector_manifest = EmbeddingProjectionManifest(
            projection_id=identity({"corpus_id": request.corpus_id, "project_id": request.project_id, "manifest": manifest_data, "regions": indexed}),
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            source_revision=self._source_revision(request.corpus_id),
            provider=manifest_data["provider"],
            model=manifest_data["model"],
            model_revision=manifest_data["revision"],
            dimension=manifest_data["dimension"],
            indexed_artifact_ids=indexed,
        )
        return {
            "version": 1,
            "batch_ids": sorted(batch.id for batch in batches),
            "indexed_region_ids": list(indexed),
            "manifest": manifest_data,
        }, vector_manifest

    @staticmethod
    def _structural(snapshot) -> dict[str, Any]:
        return {
            "version": 1,
            "graph_revision": snapshot.graph_revision,
            "nodes": [node.ref.model_dump(mode="json") for node in snapshot.nodes],
            "edges": [
                {"id": edge.ref, "source": edge.source_id, "target": edge.target_id, "relation": edge.relation}
                for edge in snapshot.edges
            ],
        }

    @staticmethod
    def _summary(snapshot, regions: list[Record]) -> dict[str, Any]:
        return {
            "version": 1,
            "corpus_id": snapshot.corpus_id,
            "project_id": snapshot.project_id,
            "graph_revision": snapshot.graph_revision,
            "node_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "source_region_count": len(regions),
            "node_types": {node_type: sum(node.node_type == node_type for node in snapshot.nodes)
                           for node_type in sorted({node.node_type for node in snapshot.nodes})},
            "relations": {relation: sum(edge.relation == relation for edge in snapshot.edges)
                          for relation in sorted({edge.relation for edge in snapshot.edges})},
        }


__all__ = [
    "GraphProjectionManifest",
    "GraphProjectionRequest",
    "GraphProjectionResult",
    "GraphProjectionService",
]
