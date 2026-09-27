"""Canonical Open Knowledge Format graph contracts.

This module describes logical graph data independently of SQLite, GraphRAG,
Langflow, or Semantica's reasoning interfaces.  Storage and retrieval may use
derived representations, but they must preserve these identities, scopes,
statuses, and provenance references.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel


GraphIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Stable logical identifier without whitespace.",
    ),
]

GraphName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z](?:[a-z0-9_]{0,63})$",
        description="Registered lowercase snake_case node or relation name.",
    ),
]

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class GraphIdentity(StrictModel):
    """A logical identity; equal local names in distinct scopes remain distinct."""
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    local_id: GraphIdentifier

    @property
    def key(self) -> tuple[str, str | None, str]:
        return self.corpus_id, self.project_id, self.local_id


class GraphRevision(StrictModel):
    """A view containing a corpus head and optional project head."""
    corpus_id: GraphIdentifier
    corpus_revision: int = Field(default=0, ge=0)
    project_id: GraphIdentifier | None = None
    project_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def scoped_head(self):
        if (self.project_id is None) != (self.project_revision is None):
            raise ValueError("project identity and revision must occur together")
        return self


class OKFStatus(StrEnum):
    """Scientific or workflow status carried by an OKF object."""

    PROPOSED = "proposed"
    OBSERVED = "observed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    PARTIAL = "partial"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    VERIFIED = "verified"
    REFUTED = "refuted"
    PROMOTED = "promoted"
    UNKNOWN = "unknown"


class OKFReferenceKind(StrEnum):
    ARTIFACT = "artifact"
    SOURCE_REGION = "source_region"
    RECORD = "record"
    EXTERNAL = "external"


class OKFReference(StrictModel):
    """A provenance-bearing pointer to immutable or revisioned evidence."""

    reference_kind: OKFReferenceKind
    target_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    revision: str | None = Field(default=None, min_length=1, max_length=256)
    content_hash: Digest | None = None
    locator: dict[str, Any] = Field(default_factory=dict, max_length=32)


class EvidenceReference(StrictModel):
    """A source-region binding that can be checked against immutable storage."""

    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    artifact_id: Digest
    region_id: GraphIdentifier
    source_revision: GraphIdentifier
    content_hash: Digest
    locator: dict[str, Any] = Field(default_factory=dict, max_length=32)
    quotation: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def immutable_identity(self) -> "EvidenceReference":
        if self.artifact_id != self.content_hash:
            raise ValueError("artifact identity and content hash must match")
        return self


class PromotionOrigin(StrictModel):
    """Immutable origin metadata retained when project knowledge is promoted."""

    proposal_id: GraphIdentifier
    source_project_id: GraphIdentifier
    source_snapshot_id: GraphIdentifier
    source_graph_revision: GraphRevision
    source_id: GraphIdentifier


class OKFNode(StrictModel):
    """A typed, scoped OKF entity.

    ``node_id`` is logical identity and must remain stable across graph
    revisions.  Mutable properties belong to a later graph revision; they do
    not replace an earlier node or its evidence.
    """

    schema_version: Literal[2] = 2
    node_id: GraphIdentifier
    node_type: GraphName
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    status: OKFStatus = OKFStatus.PROPOSED
    properties: dict[str, Any] = Field(default_factory=dict, max_length=128)
    provenance: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=256)
    promotion_origin: PromotionOrigin | None = None
    parents: tuple[GraphIdentity, ...] = Field(default=(), max_length=256)
    ontology_profile: GraphIdentifier | None = None
    producer: GraphIdentifier | None = None
    producer_version: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def ref(self) -> GraphIdentity:
        return GraphIdentity(corpus_id=self.corpus_id, project_id=self.project_id, local_id=self.node_id)

    @model_validator(mode="after")
    def unique_links(self) -> "OKFNode":
        reference_ids = [
            (reference.reference_kind, reference.target_id, reference.revision)
            for reference in self.provenance
        ]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("duplicate OKF provenance references")
        if len(self.parents) != len(set(self.parents)):
            raise ValueError("duplicate OKF parent identifiers")
        if any(p.corpus_id != self.corpus_id or p.project_id not in (None, self.project_id) for p in self.parents):
            raise ValueError("parent is outside the node scope")
        return self


class OKFEdge(StrictModel):
    """A typed, scoped relation between two logical OKF nodes."""

    schema_version: Literal[2] = 2
    edge_id: GraphIdentifier
    relation: GraphName
    source_id: GraphIdentity
    target_id: GraphIdentity
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    status: OKFStatus = OKFStatus.PROPOSED
    provenance: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=256)
    promotion_origin: PromotionOrigin | None = None
    properties: dict[str, Any] = Field(default_factory=dict, max_length=64)
    ontology_profile: GraphIdentifier | None = None
    producer: GraphIdentifier | None = None
    producer_version: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def ref(self) -> GraphIdentity:
        return GraphIdentity(corpus_id=self.corpus_id, project_id=self.project_id, local_id=self.edge_id)

    @model_validator(mode="after")
    def endpoint_scope(self):
        for endpoint in (self.source_id, self.target_id):
            if endpoint.corpus_id != self.corpus_id or endpoint.project_id not in (None, self.project_id):
                raise ValueError("edge endpoint is outside its owning scope")
        return self

    @model_validator(mode="after")
    def unique_provenance(self) -> "OKFEdge":
        reference_ids = [
            (reference.reference_kind, reference.target_id, reference.revision)
            for reference in self.provenance
        ]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("duplicate OKF provenance references")
        return self


class OKFSnapshot(StrictModel):
    """An immutable, self-contained OKF graph state at one revision."""

    schema_version: Literal[2] = 2
    snapshot_id: GraphIdentifier
    graph_revision: GraphRevision
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    nodes: tuple[OKFNode, ...] = Field(default=(), max_length=100_000)
    edges: tuple[OKFEdge, ...] = Field(default=(), max_length=200_000)
    ontology_profile: GraphIdentifier | None = None
    provenance: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    producer: GraphIdentifier | None = None
    producer_version: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def validate_graph(self) -> "OKFSnapshot":
        if (self.graph_revision.corpus_id, self.graph_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("snapshot revision scope differs")
        node_ids = [node.ref for node in self.nodes]
        edge_ids = [edge.ref for edge in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate OKF node identifiers in snapshot")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate OKF edge identifiers in snapshot")
        known_nodes = set(node_ids)
        for node in self.nodes:
            if any(parent not in known_nodes for parent in node.parents):
                raise ValueError("snapshot node references an absent parent")
            if node.corpus_id != self.corpus_id:
                raise ValueError("snapshot node corpus scope differs from snapshot")
            if node.project_id not in (None, self.project_id):
                raise ValueError("snapshot node project scope differs from snapshot")
        for edge in self.edges:
            if edge.source_id not in known_nodes or edge.target_id not in known_nodes:
                raise ValueError("snapshot edge references an absent node")
            if edge.corpus_id != self.corpus_id:
                raise ValueError("snapshot edge corpus scope differs from snapshot")
            if edge.project_id not in (None, self.project_id):
                raise ValueError("snapshot edge project scope differs from snapshot")
        return self


class OKFDelta(StrictModel):
    """A proposed, revision-bound change to an OKF graph.

    A delta is data for an explicit graph-update operation.  Constructing or
    normalizing one never changes storage or admits any scientific claim.
    """

    schema_version: Literal[2] = 2
    delta_id: GraphIdentifier
    base_revision: GraphRevision
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    ontology_profile: GraphIdentifier | None = None
    upsert_nodes: tuple[OKFNode, ...] = Field(default=(), max_length=100_000)
    remove_node_ids: tuple[GraphIdentity, ...] = Field(default=(), max_length=100_000)
    add_edges: tuple[OKFEdge, ...] = Field(default=(), max_length=200_000)
    remove_edge_ids: tuple[GraphIdentity, ...] = Field(default=(), max_length=200_000)
    provenance: tuple[OKFReference, ...] = Field(default=(), max_length=256)
    reason: Annotated[str, Field(min_length=1, max_length=4_000)]
    producer: GraphIdentifier | None = None
    producer_version: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def validate_delta(self) -> "OKFDelta":
        if (self.base_revision.corpus_id, self.base_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("delta revision scope differs")
        for ref in (*self.remove_node_ids, *self.remove_edge_ids):
            if (ref.corpus_id, ref.project_id) != (self.corpus_id, self.project_id):
                raise ValueError("removal scope differs from delta")
        node_ids = [node.ref for node in self.upsert_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate OKF node identifiers in delta")
        if len(self.remove_node_ids) != len(set(self.remove_node_ids)):
            raise ValueError("duplicate removed OKF node identifiers in delta")
        if set(node_ids) & set(self.remove_node_ids):
            raise ValueError("an OKF node cannot be upserted and removed in one delta")
        edge_ids = [edge.ref for edge in self.add_edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate OKF edge identifiers in delta")
        if len(self.remove_edge_ids) != len(set(self.remove_edge_ids)):
            raise ValueError("duplicate removed OKF edge identifiers in delta")
        if set(edge_ids) & set(self.remove_edge_ids):
            raise ValueError("an OKF edge cannot be added and removed in one delta")
        for node in self.upsert_nodes:
            if node.corpus_id != self.corpus_id:
                raise ValueError("delta node corpus scope differs from delta")
            if node.project_id != self.project_id:
                raise ValueError("delta node project scope differs from delta")
        for edge in self.add_edges:
            if edge.corpus_id != self.corpus_id:
                raise ValueError("delta edge corpus scope differs from delta")
            if edge.project_id != self.project_id:
                raise ValueError("delta edge project scope differs from delta")
        return self


__all__ = [
    "GraphIdentity",
    "GraphRevision",
    "Digest",
    "EvidenceReference",
    "GraphIdentifier",
    "GraphName",
    "OKFEdge",
    "OKFNode",
    "OKFDelta",
    "OKFReference",
    "OKFReferenceKind",
    "OKFSnapshot",
    "OKFStatus",
    "PromotionOrigin",
]
