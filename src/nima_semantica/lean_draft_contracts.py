"""Harness-bound local formalization and operator-only capabilities."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel, canonical
from .okf_contracts import Digest, GraphIdentifier, GraphRevision
from .proof_contracts import ProofTarget, ProofDependency, ProofText
from .providers import ModelManifest
from .lean_project import LeanProjectRequest
from .lean_search import LeanName, LeanSearchPolicy, SearchLean
from .extraction_contracts import EmptyAction, ReadRegions
from .math_retrieval import MathRetrievalPolicy, RetrieveMathContext

VERSION = "draft-lean-v2"


class LeanSourceModule(StrictModel):
    name: LeanName = Field(description="Extensionless Lean module name, for example Main or Proof.Local.")
    source: str = Field(min_length=1, max_length=2*1024*1024)

    @model_validator(mode="after")
    def extensionless(self):
        if self.name.lower().endswith(".lean"):
            raise ValueError("module names are extensionless; remove the .lean suffix")
        return self


class DeclarationCheckObligation(StrictModel):
    obligation_id: GraphIdentifier
    declaration: LeanName
    require_discovery: StrictBool = True
    expected_local_status: Literal["available", "unavailable", "either"]
    require_source_citation: StrictBool = False

    @model_validator(mode="after")
    def coherent(self):
        if self.require_source_citation and self.expected_local_status == "unavailable":
            raise ValueError("an unavailable declaration cannot require a source citation")
        return self


class DraftLeanRequest(StrictModel):
    mode: Literal["preview", "draft", "repair"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    artifact_id: Digest | None = None
    target: ProofTarget | None = None
    dependencies: tuple[ProofDependency, ...] = Field(default=(), max_length=16)
    proof_dag_record_id: GraphIdentifier | None = None
    prior_artifact_ids: tuple[Digest, ...] = Field(default=(), max_length=4)
    source_region_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    target_record_id: GraphIdentifier | None = None
    parent_record_id: GraphIdentifier | None = None
    environment_digest: Digest | None = None
    targets: tuple[LeanName, ...] = Field(default=("target",), min_length=1, max_length=16)
    expected_declaration_type_fingerprints: dict[str, Digest] = Field(default_factory=dict, max_length=16)
    initial_modules: tuple[LeanSourceModule, ...] = Field(default=(), max_length=16)
    initial_root_modules: tuple[LeanName, ...] = Field(default=(), max_length=16)
    declaration_check_obligations: tuple[DeclarationCheckObligation, ...] = Field(default=(), max_length=16)
    allow_incomplete_workflow_obligations: StrictBool = False

    @model_validator(mode="after")
    def bound(self):
        if self.mode != "preview" and (not self.operation_id or self.target is None or self.graph_revision is None or not self.environment_digest):
            raise ValueError("exact local target, operation, graph revision and environment digest required")
        if self.target and set(self.target.dependencies) != {d.ref for d in self.dependencies}:
            raise ValueError("target dependency inventory differs")
        if len(set(self.targets)) != len(self.targets) or len({d.dependency_id for d in self.dependencies}) != len(self.dependencies):
            raise ValueError("duplicate targets or dependencies")
        if not set(self.expected_declaration_type_fingerprints) <= set(self.targets):
            raise ValueError("expected exact type fingerprints must name harness targets")
        if len({item.obligation_id for item in self.declaration_check_obligations}) != len(self.declaration_check_obligations):
            raise ValueError("duplicate declaration-check obligation")
        if self.mode == "repair" and not self.initial_modules:
            raise ValueError("repair requires exact initial source")
        if self.initial_modules:
            names = tuple(module.name for module in self.initial_modules)
            if len(names) != len(set(names)):raise ValueError("duplicate initial module name")
            if not self.initial_root_modules:raise ValueError("initial modules require root modules")
            LeanProjectRequest({module.name:module.source for module in self.initial_modules}, self.targets, self.initial_root_modules).validate()
        if len(canonical(self)) > 250000:
            raise ValueError("narrow the local formalization task")
        return self


class DraftLeanContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_model_calls: StrictBool = False
    allow_audit_writes: StrictBool = False
    max_actions: StrictInt = Field(default=24, ge=1, le=64)
    max_nodes: StrictInt = Field(default=256, ge=1, le=256)
    max_edges: StrictInt = Field(default=512, ge=0, le=512)
    max_read_regions: StrictInt = Field(default=32, ge=1, le=48)
    model_manifest: ModelManifest | None = None
    retrieval: MathRetrievalPolicy = Field(default_factory=MathRetrievalPolicy)
    allow_execution: StrictBool = False
    max_verifications: StrictInt = Field(default=6, ge=0, le=16)
    max_resolutions: StrictInt = Field(default=6, ge=0, le=16)
    lean_search: LeanSearchPolicy = Field(default_factory=LeanSearchPolicy)


class ProposeLean(StrictModel):
    modules: tuple[LeanSourceModule, ...] = Field(min_length=1, max_length=16,
        description="Complete source bundle in compilation order; module names are extensionless.")
    root_modules: tuple[LeanName, ...] = Field(min_length=1, max_length=16,
        description="Submitted modules whose environments contain the selected target declarations.")
    base_source_revision: Digest | None = None
    correspondence: str = Field(min_length=1, max_length=6000)
    introduced_assumptions: tuple[ProofText, ...] = Field(default=(), max_length=16)
    correspondence_gaps: tuple[ProofText, ...] = Field(default=(), max_length=16)
    evidence_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=16,
        description="Select only IDs listed under selectable_handles.evidence_ids.")
    resolution_ids: tuple[Digest, ...] = Field(default=(), max_length=16,
        description="Select only successful IDs listed under selectable_handles.resolution_ids.")
    supplied_premise_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=16,
        description="Select only IDs listed under selectable_handles.supplied_premise_ids.")
    correction_reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def bounded(self):
        if len(canonical(self))>150000: raise ValueError("narrow source proposal")
        names=tuple(module.name for module in self.modules)
        if len(names)!=len(set(names)):raise ValueError("duplicate module name")
        if len(self.root_modules)!=len(set(self.root_modules)) or not set(self.root_modules)<=set(names):
            raise ValueError("root_modules must select unique submitted module names")
        return self


class ResolveLean(StrictModel):
    declaration: LeanName
    imports: tuple[LeanName, ...] = Field(default=("Init",), min_length=1, max_length=8)


class SubmitLean(StrictModel):
    accept_unverified: StrictBool = False
    reason: str = Field(default="", max_length=2000)


SCHEMAS = {"propose_source": ProposeLean, "verify_source": EmptyAction,
    "resolve_declaration": ResolveLean, "search_lean": SearchLean,
    "read_regions": ReadRegions, "retrieve_context": RetrieveMathContext, "submit_result": SubmitLean}
