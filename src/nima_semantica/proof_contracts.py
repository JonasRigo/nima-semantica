"""Target and dependency contracts for Draft Lean and historical artifact reading."""
from typing import Literal,Annotated
from pydantic import Field,model_validator
from .models import StrictModel,identity,canonical
from .okf_contracts import GraphIdentifier,GraphIdentity,GraphRevision,Digest
from .review_contracts import ReviewTarget

ProofText=Annotated[str,Field(min_length=1,max_length=2000)]

class ProofTarget(ReviewTarget):
    granularity: Literal["theorem","lemma","implication","case","calculation","step"]="lemma"
    definitions: tuple[ProofText,...]=Field(default=(),max_length=16)

class ProofDependency(StrictModel):
    dependency_id: GraphIdentifier
    ref: GraphIdentity
    content_revision: Digest
    statement: str=Field(min_length=1,max_length=4000)
    domain: str=Field(min_length=1,max_length=2000)
    assumptions: tuple[ProofText,...]=Field(default=(),max_length=16)
    status: Literal["assumed","proposed","verification_evidence_supplied","unresolved"]="unresolved"
    artifact_ids: tuple[Digest,...]=Field(default=(),max_length=8)
    @model_validator(mode="after")
    def pinned(self):
        if self.content_revision!=identity(self.model_dump(mode="json",exclude={"content_revision"})):raise ValueError("dependency content revision differs")
        if self.status=="verification_evidence_supplied" and not self.artifact_ids:raise ValueError("verification evidence reference required; not automatic acceptance")
        return self

class ProofContextRequest(StrictModel):
    mode: Literal["preview","draft","attempt"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    artifact_id: Digest | None=None
    target: ProofTarget | None=None
    selected_route: str=Field(default="",max_length=6000)
    dependencies: tuple[ProofDependency,...]=Field(default=(),max_length=16)
    prior_artifact_ids: tuple[Digest,...]=Field(default=(),max_length=4)
    proof_dag_record_id: GraphIdentifier | None=None
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=32)
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None
    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None or self.target is None):raise ValueError("operation, graph revision and exact local target required")
        if self.mode=="attempt" and not self.selected_route.strip():raise ValueError("attempt requires a harness-selected route")
        if len({d.dependency_id for d in self.dependencies})!=len(self.dependencies):raise ValueError("duplicate dependency")
        if self.target and set(self.target.dependencies)!={d.ref for d in self.dependencies}:raise ValueError("target dependency inventory must match supplied contracts")
        if len(canonical(self))>150000:raise ValueError("proof input exceeds bound")
        return self
