"""Shared question and request contracts for bounded literature passes."""
from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel
from .okf_contracts import GraphIdentifier, GraphIdentity, GraphRevision, Digest


class ResearchQuestion(StrictModel):
    question_id: GraphIdentifier
    question: str=Field(min_length=1,max_length=6000)

class DeepResearchRequest(StrictModel):
    mode: Literal["preview","research"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    artifact_id: Digest | None=None
    questions: tuple[ResearchQuestion,...]=Field(default=(),max_length=6)
    graph_targets: tuple[GraphIdentity,...]=Field(default=(),max_length=8)
    source_region_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=32)
    required_assumptions: tuple[str,...]=Field(default=(),max_length=16)
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None or not self.questions):raise ValueError("research requires operation, exact revision and questions")
        for seq in (tuple(q.question_id for q in self.questions),self.graph_targets,self.source_region_ids):
            if len(seq)!=len(set(seq)):raise ValueError("duplicate scope entry")
        if any(not a.strip() or len(a)>2000 for a in self.required_assumptions):raise ValueError("invalid assumption")
        return self
