"""Mathematics-preserving source registration and manifest-checked embeddings."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .models import ConfigurationError, NimaError, Record, canonical
from .providers import validate_manifest


class _QuietChunkProgress:
    """Silence only this chunker's cosmetic progress, not global output."""
    def start_tracking(self, **kwargs):
        return None

    def update_tracking(self, *args, **kwargs):
        pass

    def stop_tracking(self, *args, **kwargs):
        pass


def normalize(data: bytes, name: str, pdf_normalizer=None) -> tuple[str, list[dict]]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        # Never treat generic PDF text as a mathematics-aware normalization.
        if pdf_normalizer is None:
            raise ConfigurationError("PDF normalization requires a Langflow-configured mathematics-aware parser")
        text, diagnostics = pdf_normalizer(data)
        if not isinstance(text, str) or not text.strip() or not isinstance(diagnostics, list):
            raise NimaError("invalid PDF normalization result")
        if not diagnostics or any(not item.get("provenance") for item in diagnostics):
            raise NimaError("PDF normalization must preserve page provenance")
        return text, diagnostics
    if suffix not in (".html", ".htm", ".tex", ".md", ".txt", ".json"):
        raise NimaError("unsupported source format")
    text = data.decode("utf-8", errors="strict")
    diagnostics = []
    if suffix in (".html", ".htm"):
        from .html_preparation import normalize_html
        text, diagnostics = normalize_html(text)
    elif suffix == ".json":
        json.loads(text)  # Validate without changing source-relative character offsets.
    for match in re.finditer(r"\$\$(.*?)\$\$|\\\[(.*?)\\\]|(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)", text, re.S):
        diagnostics.append({"original": match.group(), "latex": next(v for v in match.groups() if v is not None),
                            "start": match.start(), "end": match.end(), "status": "source_notation"})
    return text, diagnostics


def regions(text: str, normalized_artifact: str, document_id: str, corpus_id: str, *, project_id=None):
    try:
        from semantica.split import StructuralChunker
    except ModuleNotFoundError as exc:
        if exc.name == "semantica" or (exc.name or "").startswith("semantica."):
            raise ConfigurationError(
                "source chunking requires the declared semantica==0.6.8 dependency; "
                "use the pinned NIMA environment"
            ) from None
        raise
    chunker = StructuralChunker(max_chunk_size=1800)
    chunker.progress_tracker = _QuietChunkProgress()
    chunks = chunker.chunk(text)
    result = []
    cursor = 0
    for chunk in chunks:
        value = chunk.text if hasattr(chunk, "text") else chunk.content
        # StructuralChunker may reflow whitespace. Treat its output only as
        # boundary suggestions and retain the original source bytes/offsets.
        tokens = re.findall(r"\S+", value)
        if not tokens:
            continue
        match = re.compile(r"\s*" + r"\s+".join(re.escape(token) for token in tokens)).match(text, cursor)
        if match is None:
            # Non-whitespace changes or omissions are never repaired by guessing.
            raise NimaError("chunk does not preserve source locator")
        start, end = cursor, match.end()
        value = text[start:end]
        result.append(Record(kind="SourceRegion", corpus_id=corpus_id, project_id=project_id, parents=(document_id,), content={
            "text": value, "document_id": document_id, "normalized_artifact": normalized_artifact,
            "start": start, "end": end, "ordinal": len(result),
        }))
        cursor = end
    if text[cursor:].strip():
        raise NimaError("chunker omitted source content")
    if result and cursor < len(text):
        last = result[-1]
        result[-1] = last.model_copy(update={"content": {**last.content, "text": text[last.content["start"]:], "end": len(text)}})
    if not result and text.strip():
        result.append(Record(kind="SourceRegion", corpus_id=corpus_id, project_id=project_id, parents=(document_id,), content={
            "text": text, "document_id": document_id, "normalized_artifact": normalized_artifact,
            "start": 0, "end": len(text), "ordinal": 0,
        }))
    # Structural boundaries are suggestions, not a hard size guarantee.
    bounded = []
    for record in result:
        start, stop = record.content["start"], record.content["end"]
        while start < stop:
            end = min(start + 1800, stop)
            if end < stop:
                boundaries = list(re.finditer(r"\s+", text[start:end]))
                if boundaries and boundaries[-1].end() >= 900:
                    end = start + boundaries[-1].end()
            bounded.append(record.model_copy(update={"content": {
                **record.content, "start": start, "end": end,
                "text": text[start:end], "ordinal": len(bounded)}}))
            start = end
    return bounded


def validate_vectors(vectors, count, manifest):
    validate_manifest(manifest)
    with np.errstate(over="ignore", invalid="ignore"):
        matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape != (count, manifest.dimension) or not np.isfinite(matrix).all():
        raise NimaError("embedding dimensions or numeric values invalid")
    norms = np.linalg.norm(matrix.astype(np.float64), axis=1)
    if np.any(norms == 0):
        raise NimaError("zero embedding rejected")
    if np.any(norms > np.sqrt(np.finfo(np.float32).max)):
        raise NimaError("embedding magnitude exceeds safe float32 normalization range")
    return matrix


def prepare_source(store, provider, profile, source, corpus_id):
    data = store.read_artifact(source["artifact_id"])
    project_id = source.get("project_id")
    document = Record(kind="Document", corpus_id=corpus_id, project_id=project_id, content={"artifact_id": source["artifact_id"]})
    pdf_normalizer = (lambda value: provider.normalize_pdf(profile, value)) if hasattr(provider, "normalize_pdf") else None
    text, diagnostics = normalize(data, source["name"], pdf_normalizer)
    # Persist PDF crops and recognition receipts in this application's artifact store.
    # No external parser cache becomes canonical evidence storage.
    import base64
    import hashlib
    for diagnostic in diagnostics:
        if "artifact_bundle" not in diagnostic:
            continue
        artifacts = diagnostic.pop("artifact_bundle")
        diagnostic["artifact_ids"] = []
        for artifact in artifacts:
            content = base64.b64decode(artifact["data_base64"], validate=True)
            if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                raise NimaError("PDF evidence artifact integrity mismatch")
            diagnostic["artifact_ids"].append(store.artifact(content))
    normalized_id = store.artifact(text.encode())
    normalized = Record(kind="NormalizedDocument", corpus_id=corpus_id, project_id=project_id, parents=(document.id,), content={
        "artifact_id": normalized_id, "policy": "nima-normalize-v1", "diagnostics": diagnostics})
    selected = regions(text, normalized_id, document.id, corpus_id, project_id=project_id)
    if not selected:
        raise NimaError("source contains no indexable regions")
    return [document, normalized, *selected]


def ingest(store, provider, profile, source, corpus_id, *, region_budget=None, prepared=None):
    document = Record(kind="Document", corpus_id=corpus_id, project_id=source.get("project_id"), content={"artifact_id": source["artifact_id"]})
    existing = [(i, r) for i, r in store.records("EmbeddingBatch", corpus_id=corpus_id) if document.id in r.parents]
    if existing:
        # The active embedding identity is checked again when querying.
        return [], existing[-1][1].content["region_ids"], 0
    preparation = prepared or prepare_source(store, provider, profile, source, corpus_id)
    document, normalized, *selected = preparation
    if region_budget is not None and len(selected) > region_budget:
        from .models import BudgetExceeded
        raise BudgetExceeded("embedded_regions")
    vectors, manifest = provider.embed(profile, [r.content["text"] for r in selected])
    matrix = validate_vectors(vectors, len(selected), manifest)
    matrix_artifact = store.artifact(canonical(matrix.tolist()))
    batch = Record(kind="EmbeddingBatch", corpus_id=corpus_id, project_id=document.project_id, parents=(document.id, normalized.id), content={
        "region_ids": [r.id for r in selected], "matrix_artifact": matrix_artifact,
        "manifest": manifest.model_dump(mode="json"), "metric": "cosine", "normalization_policy": "nima-normalize-v1",
    })
    return [*preparation, batch], [r.id for r in selected], len(selected)
