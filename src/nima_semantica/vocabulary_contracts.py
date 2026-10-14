"""Optional project terminology, provisional extensions and advisory findings."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel, identity
from .okf_contracts import Digest, GraphIdentifier, GraphIdentity, GraphRevision, EvidenceReference


class VocabularyEntry(StrictModel):
    entry_id: GraphIdentifier
    term: str = Field(min_length=1, max_length=512)
    definition: str = Field(min_length=1, max_length=24000)
    aliases: tuple[str, ...] = Field(default=(), max_length=32)
    concept_ids: tuple[GraphIdentity, ...] = Field(default=(), max_length=32)
    notation: str = Field(default="", max_length=4000)
    domain: str = Field(default="", max_length=4000)
    assumptions: tuple[str, ...] = Field(default=(), max_length=32)
    qualifiers: tuple[str, ...] = Field(default=(), max_length=32)
    distinctions: tuple[str, ...] = Field(default=(), max_length=32)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)
    status: Literal["source_defined", "project_convention", "proposed", "unresolved"] = "unresolved"

    @model_validator(mode="after")
    def grounded(self):
        if not self.term.strip() or not self.definition.strip():
            raise ValueError("nonempty vocabulary term and definition required")
        if self.status == "source_defined" and not self.evidence:
            raise ValueError("source-defined terminology requires exact evidence")
        if any(not s.strip() or len(s) > 4000 for s in (*self.aliases, *self.assumptions, *self.qualifiers, *self.distinctions)):
            raise ValueError("vocabulary qualifier/alias is empty or exceeds its bound")
        return self


class VocabularySnapshot(StrictModel):
    schema_version: Literal[1] = 1
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    graph_snapshot_hash: Digest
    ontology_digests: tuple[Digest, ...] = Field(default=(), max_length=32)
    generated_at: str = Field(min_length=1, max_length=128)
    coverage_limitations: tuple[str, ...] = Field(default=(), max_length=64)
    entries: tuple[VocabularyEntry, ...] = Field(default=(), max_length=10000)
    content_hash: Digest

    @model_validator(mode="after")
    def coherent(self):
        if (self.graph_revision.corpus_id, self.graph_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("vocabulary graph scope mismatch")
        if len({x.entry_id for x in self.entries}) != len(self.entries):
            raise ValueError("duplicate vocabulary entry identity")
        if identity(self.model_dump(mode="json", exclude={"content_hash"})) != self.content_hash:
            raise ValueError("vocabulary content hash mismatch")
        if any(not s.strip() or len(s) > 4000 for s in self.coverage_limitations):
            raise ValueError("invalid vocabulary coverage limitation")
        return self


class VocabularyPolicy(StrictModel):
    enabled: StrictBool = False
    snapshot_artifact_id: Digest | None = None
    snapshot_hash: Digest | None = None
    max_context_bytes: StrictInt = Field(default=16384, ge=1024, le=65536)


class VocabularyExtension(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: GraphIdentifier
    revision: StrictInt = Field(default=0, ge=0)
    supersedes: GraphIdentifier | None = None
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    base_snapshot_hash: Digest
    task_id: GraphIdentifier
    kind: Literal["new_term", "new_concept", "distinction", "definition_challenge", "borrowed_terminology"]
    term: str = Field(min_length=1, max_length=512)
    definition: str = Field(min_length=1, max_length=24000)
    motivation: str = Field(min_length=1, max_length=8000)
    related_entry_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    changed_assumptions: tuple[str, ...] = Field(default=(), max_length=32)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)
    speculative_basis: str = Field(default="", max_length=8000)
    competing_interpretations: tuple[str, ...] = Field(default=(), max_length=32)
    open_questions: tuple[str, ...] = Field(default=(), max_length=32)
    disposition: Literal["proposed", "withdrawn"] = "proposed"
    scientific_status: Literal["uncertified"] = "uncertified"

    @model_validator(mode="after")
    def basis(self):
        if not self.evidence and not self.speculative_basis.strip():
            raise ValueError("extension needs evidence or an explicit speculative basis")
        if self.revision and not self.supersedes:
            raise ValueError("extension revision must retain its previous proposal")
        return self


class VocabularyFinding(StrictModel):
    finding_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    snapshot_hash: Digest
    target_id: GraphIdentifier
    argument_hash: Digest
    kind: Literal["meaning_shift", "unsupported_equivalence", "missing_qualifier", "changed_assumption", "status_inflation", "unresolved_definition"]
    explanation: str = Field(min_length=1, max_length=8000)
    entry_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)
    assessment: Literal["concern", "unresolved"] = "unresolved"
    authority: Literal["advisory"] = "advisory"


class VocabularyObservation(StrictModel):
    kind: Literal["meaning_shift", "unsupported_equivalence", "missing_qualifier", "changed_assumption", "status_inflation", "unresolved_definition"]
    explanation: str = Field(min_length=1, max_length=8000)
    entry_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)
    assessment: Literal["concern", "unresolved"] = "unresolved"


def compatible_dump(model):
    """Keep old request fingerprints identical when new options are omitted."""
    optional = {"normalized_reasoning", "vocabulary_snapshot", "vocabulary_extension", "vocabulary_findings", "vocabulary_extensions", "vocabulary_assessment"}
    exclude = {k for k in optional if k in type(model).model_fields and getattr(model, k) in (None, (), [])}
    return model.model_dump(mode="json", exclude=exclude)


def vocabulary_schema(schema, *, enabled):
    """Disabled model calls retain their ordinary action schema and framing."""
    if enabled:
        return schema
    import copy
    schema = copy.deepcopy(schema)
    for key in ("vocabulary_assessment", "vocabulary_findings", "vocabulary_extensions"):
        schema.get("properties", {}).pop(key, None)
    for key in list(schema.get("$defs", {})):
        if key.startswith("Vocabulary"):
            schema["$defs"].pop(key)
    return schema
