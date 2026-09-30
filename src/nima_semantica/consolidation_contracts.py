"""Explicit, agent-selected reconciliation; never implicit identity guessing."""
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel
from .okf_contracts import Digest, GraphIdentifier, GraphName


class IdentityMerge(StrictModel):
    node_ids: tuple[GraphIdentifier, ...] = Field(min_length=2, max_length=128)
    canonical_id: GraphIdentifier
    rationale: str = Field(min_length=1, max_length=4000)


class CrossPaperLink(StrictModel):
    source_id: GraphIdentifier
    target_id: GraphIdentifier
    relation: GraphName
    region_ids: tuple[GraphIdentifier, ...] = Field(min_length=1, max_length=32)
    rationale: str = Field(min_length=1, max_length=4000)


class ConsolidationSelection(StrictModel):
    artifact_ids: tuple[Digest, ...] = Field(min_length=1, max_length=128,
        description="Saved graph candidates. Continue with the previous consolidated artifact plus new candidates; no fixed total paper count.")
    include_project_graph: StrictBool = True
    plan_hash: Digest | None = None
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=32, ge=1, le=128)
    edge_offset: StrictInt = Field(default=0, ge=0)
    edge_limit: StrictInt = Field(default=64, ge=1, le=256)
    query: str = Field(default="", max_length=1000)
    merges: tuple[IdentityMerge, ...] = Field(default=(), max_length=256)
    links: tuple[CrossPaperLink, ...] = Field(default=(), max_length=256)
    unresolved: tuple[str, ...] = Field(default=(), max_length=256,
        description="Identity ambiguities, missing batches, unsupported links and scope limitations; never a completeness claim.")

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("duplicate graph candidate; select each once")
        for merge in self.merges:
            if len(set(merge.node_ids)) != len(merge.node_ids) or merge.canonical_id not in merge.node_ids:
                raise ValueError("merge members must be unique and canonical_id must select a member")
        if any(not text.strip() or len(text) > 4000 for text in self.unresolved):
            raise ValueError("unresolved issues require bounded nonempty descriptions")
        return self
