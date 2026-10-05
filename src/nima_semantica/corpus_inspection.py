"""Read-only scoped inventory; metadata readiness is not evidence validation."""
from pydantic import Field, StrictInt

from .models import StrictModel, NimaError
from .okf_contracts import GraphIdentifier
from .corpus_registry import CorpusRegistry
from .source_corpus import SourceCorpusService, SourceDescriptor, SourceRegion
from .graph_projection import GraphProjectionManifest
from .tool_contracts import ToolResult


class InspectCorpusRequest(StrictModel):
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=25, ge=1, le=100)
    expected_store_revision: GraphIdentifier | None = None


class InspectCorpusContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None


NOTE = "Read-only metadata inspection. Current projection metadata does not certify artifact integrity, source fidelity, retrieval quality, or scientific correctness. No provider calls or execution receipts."


def inspect_corpus(store, request: InspectCorpusRequest, context: InspectCorpusContext) -> ToolResult:
    request = InspectCorpusRequest.model_validate(request.model_dump(mode="json"))
    context = InspectCorpusContext.model_validate(context.model_dump(mode="json"))
    def unavailable(code, status="unavailable"):
        return ToolResult(operation="Inspect Corpus", status=status, note=NOTE,
            diagnostics=({"code": code, "message": "Check the configured scope and revision; restart pagination after a revision change."},))
    if store is None:
        return unavailable("corpus.store_unconfigured")
    try:
        # One coherent snapshot; no metadata writes, receipts, or artifact reads.
        with store.joined_transaction():
            revision = store.revision
            if request.expected_store_revision is not None and request.expected_store_revision != revision:
                return unavailable("corpus.stale_page", "failed")
            if CorpusRegistry(store).corpus(context.corpus_id) is None:
                return unavailable("corpus.unavailable")
            def visible(kind):
                # Keep only IDs and aggregate metadata; book regions can repeat large diagnostics.
                return ((key, record) for key, record in store.iter_records(kind, corpus_id=context.corpus_id,
                    project_id=context.project_id) if record.project_id in (None, context.project_id))
            sources = []
            for _, record in visible(SourceCorpusService.SOURCE_KIND):
                if record.project_id is not None:
                    raise ValueError("source descriptor must be corpus-wide")
                item = SourceDescriptor.model_validate(record.content)
                if item.corpus_id != context.corpus_id:
                    raise ValueError("descriptor scope mismatch")
                sources.append(item)
            sources.sort(key=lambda s: s.source_id)
            if len({s.source_id for s in sources}) != len(sources):
                raise ValueError("ambiguous source identity")
            by_source = {s.source_id: s for s in sources}
            counts = {s.source_id: 0 for s in sources}
            region_ids = set()
            for key, record in visible(SourceCorpusService.REGION_KIND):
                region = SourceRegion.model_validate(record.content)
                source = by_source.get(region.source_id)
                if (region.corpus_id != context.corpus_id or region.project_id != record.project_id
                    or source is None or source.source_revision != region.source_revision
                    or source.artifact_id != (region.source_artifact_id or region.artifact_id)):
                    raise ValueError("region binding mismatch")
                counts[region.source_id] += 1
                region_ids.add(key)
            graph_revision = store.graph_revision(context.corpus_id, context.project_id)
            source_revision = str(store.embedding_revision(context.corpus_id))
            current, stale, vector_coverage, lexical = 0, 0, 0, False
            for _, record in visible("GraphProjection"):
                # Projections are exact-scope, not inherited from the corpus.
                if record.project_id != context.project_id:
                    continue
                manifest = GraphProjectionManifest.model_validate(record.content["manifest"])
                if manifest.corpus_id != context.corpus_id or manifest.project_id != record.project_id:
                    raise ValueError("projection scope mismatch")
                if set(manifest.artifact_ids) != {"lexical", "vector", "structural", "summary"}:
                    raise ValueError("projection artifact metadata incomplete")
                if manifest.graph_revision != graph_revision or manifest.source_revision != source_revision:
                    stale += 1
                    continue
                current += 1
                lexical = lexical or "lexical" in manifest.kinds
                if "vector" in manifest.kinds and manifest.vector_manifest is not None:
                    vector_coverage = max(vector_coverage, len(region_ids.intersection(manifest.vector_manifest.indexed_artifact_ids)))
            page = sources[request.offset:request.offset + request.limit]
            has_more = request.offset + request.limit < len(sources)
            return ToolResult(operation="Inspect Corpus", status="complete", note=NOTE, data={
                "scope": context.model_dump(mode="json"), "store_revision": revision,
                "graph_revision": graph_revision.model_dump(mode="json"), "source_revision": source_revision,
                "sources": [{"source_id": s.source_id, "name": s.name, "media_type": s.media_type,
                    "artifact_id": s.artifact_id, "source_revision": s.source_revision,
                    "region_count": counts[s.source_id]} for s in page],
                "total_sources": len(sources), "total_regions": len(region_ids),
                "offset": request.offset, "limit": request.limit, "has_more": has_more,
                "next_request": {"offset": request.offset + request.limit, "limit": request.limit,
                    "expected_store_revision": revision} if has_more else None,
                "readiness": {"basis": "metadata_only", "prepared_sources": sum(n > 0 for n in counts.values()),
                    "current_projections": current, "stale_projections": stale,
                    "lexical_metadata_ready": bool(lexical and region_ids),
                    "vector_covered_regions": vector_coverage,
                    "vector_metadata_ready": bool(region_ids and vector_coverage == len(region_ids)),
                    "artifact_integrity_checked": False, "source_fidelity_checked": False},
            })
    except (ValueError, KeyError, TypeError, NimaError, OSError):
        return unavailable("corpus.invalid_metadata", "failed")
