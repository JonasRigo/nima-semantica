"""Validated embedding publication primitive; no operation registry."""
import json
from typing import Annotated
import numpy as np
from pydantic import Field, FiniteFloat, model_validator
from .models import StrictModel, NimaError, ConfigurationError, Record, canonical
from .providers import ModelManifest
from .corpus import validate_vectors
from .evidence_contracts import validate_reference
from .okf_contracts import EvidenceReference
Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Vector = Annotated[tuple[FiniteFloat, ...], Field(min_length=1, max_length=4096)]
class CorpusInput(StrictModel):
    corpus_id: Identifier = "default"
    project_id: Identifier | None = None
    expected_revision: str | None = None

class SourceIndexInput(CorpusInput):
    region_ids: tuple[Digest, ...] = Field(min_length=1, max_length=256)
    vectors: tuple[Vector, ...] = Field(min_length=1, max_length=256)
    manifest: ModelManifest

    @model_validator(mode="after")
    def aligned(self):
        if len(set(self.region_ids)) != len(self.region_ids):
            raise ValueError("duplicate region identifiers")
        if len(self.region_ids) != len(self.vectors):
            raise ValueError("one embedding vector is required per region")
        return self


def SourceIndex(payload: dict, store) -> dict:
    """Persist supplied embeddings after resolving exact region IDs in this corpus.

    Conflicting duplicate embeddings and mixed manifests are rejected because the
    existing retrieval primitive takes the first embedding for each region.
    """
    request = SourceIndexInput.model_validate(payload)
    matrix = validate_vectors(request.vectors, len(request.region_ids), request.manifest)
    manifest = request.manifest.model_dump(mode="json")
    with store.joined_transaction(request.expected_revision):
        regions = dict(store.get_selected(request.region_ids, kind="SourceRegion", corpus_id=request.corpus_id, project_id=request.project_id))
        if not set(request.region_ids) <= regions.keys():
            raise NimaError("index refers to missing regions or another corpus")
        if any(region.project_id not in (None, request.project_id) for region in regions.values()):
            raise NimaError("index requires the source region project scope")
        for region_id in request.region_ids:
            content = regions[region_id].content
            reference = EvidenceReference(corpus_id=request.corpus_id, project_id=regions[region_id].project_id,
                artifact_id=content["artifact_id"], region_id=region_id, content_hash=content["artifact_id"],
                source_revision=content["source_revision"], quotation=content["text"])
            if validate_reference(store, reference, corpus_id=request.corpus_id,
                                  project_id=request.project_id, target_id=region_id):
                raise NimaError("embedding source region failed exact evidence validation")
        supplied = dict(zip(request.region_ids, matrix))
        for _, batch in store.records("EmbeddingBatch", corpus_id=request.corpus_id, project_id=request.project_id):
            if batch.content["manifest"] != manifest:
                raise ConfigurationError("embedding manifest mismatch; explicit corpus reindex required")
            if not set(batch.content["region_ids"]) & supplied.keys():
                continue
            previous = validate_vectors(json.loads(store.read_artifact(batch.content["matrix_artifact"])),
                                        len(batch.content["region_ids"]), request.manifest)
            for region_id, vector in zip(batch.content["region_ids"], previous):
                if region_id in supplied and not np.array_equal(supplied[region_id], vector):
                    raise NimaError("region already indexed with a different embedding")
        matrix_artifact = store.artifact(canonical(matrix.tolist()))
        batch = Record(kind="EmbeddingBatch", corpus_id=request.corpus_id, project_id=request.project_id,
                       parents=tuple(sorted(request.region_ids)),
                       content={"region_ids": list(request.region_ids), "matrix_artifact": matrix_artifact,
                                "manifest": manifest, "metric": "cosine", "normalization_policy": "nima-normalize-v1"})
        store.put(batch)
    return {"corpus_id": request.corpus_id, "embedding_batch_id": batch.id,
            "matrix_artifact_id": matrix_artifact, "region_ids": list(request.region_ids),
            "region_count": len(request.region_ids)}
