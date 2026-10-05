"""JSON-safe ontology lookup and OKF graph validation services."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .models import NimaError, StrictModel
from .okf_contracts import OKFSnapshot
from .ontology_profiles import OntologyProfile, saved_profile


DEFAULT_PROFILE_NAMES = ("claim_obligation", "literature_evidence", "literature_review", "theorem_dependencies")


class OntologyIssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class OntologyProfileSummary(StrictModel):
    name: str
    version: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    packaged: bool = True


class OntologyValidationIssue(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    severity: OntologyIssueSeverity
    message: str = Field(min_length=1, max_length=2_000)
    node_id: str | None = None
    edge_id: str | None = None


class OntologyValidationReport(StrictModel):
    schema_version: Literal[1] = 1
    profile: OntologyProfileSummary
    valid: bool
    issues: tuple[OntologyValidationIssue, ...] = ()
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)


class OntologyRegistry:
    """Immutable-by-convention registry suitable for service and Langflow use."""

    def __init__(self, profiles: tuple[OntologyProfile, ...] | None = None):
        selected = profiles or tuple(saved_profile(name) for name in DEFAULT_PROFILE_NAMES)
        keys = [(profile.name, profile.version) for profile in selected]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate ontology profile name/version")
        self._profiles = {key: profile for key, profile in zip(keys, selected)}

    def list_profiles(self) -> tuple[OntologyProfileSummary, ...]:
        return tuple(
            OntologyProfileSummary(name=profile.name, version=profile.version, digest=profile.digest)
            for profile in sorted(self._profiles.values(), key=lambda item: (item.name, item.version))
        )

    def get(self, *, name: str, version: str | None = None, digest: str | None = None) -> OntologyProfile:
        matches = [
            profile
            for profile in self._profiles.values()
            if profile.name == name
            and (version is None or profile.version == version)
            and (digest is None or profile.digest == digest)
        ]
        if len(matches) != 1:
            raise NimaError("ontology profile selection is missing or ambiguous")
        return matches[0]

    def by_digest(self, digest: str) -> OntologyProfile:
        matches = [profile for profile in self._profiles.values() if profile.digest == digest]
        if len(matches) != 1:
            raise NimaError("ontology profile digest not found")
        return matches[0]

    def resolve(self, identity: str) -> OntologyProfile:
        """Resolve a profile by digest, name, or ``name@version`` identity."""
        if "@" in identity:
            name, version = identity.split("@", 1)
            return self.get(name=name, version=version)
        try:
            return self.by_digest(identity)
        except NimaError:
            return self.get(name=identity)


class OntologyService:
    """Framework-independent ontology lookup and OKF validation boundary."""

    def __init__(self, registry: OntologyRegistry | None = None):
        self.registry = registry or OntologyRegistry()

    @classmethod
    def from_store(cls, store, *, corpus_id: str, project_id: str | None = None):
        """Resolve packaged and authorized saved profiles with integrity checks."""
        from .ontology_tools import scoped_profiles
        with store.joined_transaction():
            profiles = scoped_profiles(store, corpus_id=corpus_id, project_id=project_id)
        return cls(OntologyRegistry(tuple(item[0] for item in profiles)))

    def list_profiles(self) -> tuple[OntologyProfileSummary, ...]:
        return self.registry.list_profiles()

    def resolve(self, identity: str) -> OntologyProfile:
        return self.registry.resolve(identity)

    def get(
        self, *, name: str, version: str | None = None, digest: str | None = None
    ) -> OntologyProfile:
        return self.registry.get(name=name, version=version, digest=digest)

    def validate_delta(self, delta) -> OntologyValidationReport:
        return validate_delta_ontology(delta, self.registry)

    def validate_snapshot(
        self, snapshot: OKFSnapshot, *, profile: str | OntologyProfile | None = None
    ) -> OntologyValidationReport:
        selected = profile
        if selected is None and snapshot.ontology_profile is not None:
            selected = self.resolve(snapshot.ontology_profile)
        if selected is None:
            return OntologyValidationReport(
                profile=OntologyProfileSummary(
                    name="unbound", version="0.0.0", digest="0" * 64, packaged=False
                ),
                valid=False,
                issues=(OntologyValidationIssue(
                    code="ontology.profile_required",
                    severity=OntologyIssueSeverity.ERROR,
                    message="Validation requires a selected ontology profile.",
                ),),
                node_count=len(snapshot.nodes),
                edge_count=len(snapshot.edges),
            )
        if isinstance(selected, str):
            selected = self.resolve(selected)
        return validate_okf_snapshot(snapshot, selected)


def validate_delta_ontology(delta, registry: OntologyRegistry | None = None) -> OntologyValidationReport:
    """Validate a partial delta against its declared ontology vocabulary."""
    if delta.ontology_profile is None:
        return OntologyValidationReport(
            profile=OntologyProfileSummary(name="unbound", version="0.0.0", digest="0" * 64, packaged=False),
            valid=False,
            issues=(OntologyValidationIssue(
                code="ontology.profile_required",
                severity=OntologyIssueSeverity.ERROR,
                message="Graph deltas must declare an ontology profile identity.",
            ),),
            node_count=len(delta.upsert_nodes),
            edge_count=len(delta.add_edges),
        )
    registry = registry or OntologyRegistry()
    profile = registry.resolve(delta.ontology_profile)
    node_types = {item.name.casefold() for item in profile.node_types}
    relations = {item.name.casefold(): item for item in profile.relation_types}
    nodes = {node.ref: node for node in delta.upsert_nodes}
    issues: list[OntologyValidationIssue] = []
    for node in delta.upsert_nodes:
        if node.node_type.casefold() not in node_types:
            issues.append(OntologyValidationIssue(
                code="ontology.unknown_node_type",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Node type {node.node_type!r} is not declared by the profile.",
                node_id=node.node_id,
            ))
    from .mathematics_contracts import contract_issues
    issues.extend(OntologyValidationIssue(**v) for v in contract_issues(profile, delta.upsert_nodes, delta.add_edges))
    for edge in delta.add_edges:
        relation = relations.get(edge.relation.casefold())
        if relation is None:
            issues.append(OntologyValidationIssue(
                code="ontology.unknown_relation_type",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Relation type {edge.relation!r} is not declared by the profile.",
                edge_id=edge.edge_id,
            ))
            continue
        source, target = nodes.get(edge.source_id), nodes.get(edge.target_id)
        if source is not None and source.node_type.casefold() not in {item.casefold() for item in relation.source_types}:
            issues.append(OntologyValidationIssue(
                code="ontology.source_type_mismatch",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Source type {source.node_type!r} is invalid for relation {edge.relation!r}.",
                edge_id=edge.edge_id,
            ))
        if target is not None and target.node_type.casefold() not in {item.casefold() for item in relation.target_types}:
            issues.append(OntologyValidationIssue(
                code="ontology.target_type_mismatch",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Target type {target.node_type!r} is invalid for relation {edge.relation!r}.",
                edge_id=edge.edge_id,
            ))
    return OntologyValidationReport(
        profile=OntologyProfileSummary(name=profile.name, version=profile.version, digest=profile.digest),
        valid=not issues,
        issues=tuple(issues),
        node_count=len(delta.upsert_nodes),
        edge_count=len(delta.add_edges),
    )


def validate_okf_snapshot(snapshot: OKFSnapshot, profile: OntologyProfile) -> OntologyValidationReport:
    """Validate OKF graph vocabulary and relation endpoint compatibility."""
    profile_summary = OntologyProfileSummary(name=profile.name, version=profile.version, digest=profile.digest)
    issues: list[OntologyValidationIssue] = []
    node_types = {item.name.casefold(): item for item in profile.node_types}
    relation_types = {item.name.casefold(): item for item in profile.relation_types}

    if snapshot.ontology_profile is None:
        issues.append(OntologyValidationIssue(
            code="ontology.profile_unbound",
            severity=OntologyIssueSeverity.WARNING,
            message="Snapshot does not declare an ontology profile identity.",
        ))
    elif snapshot.ontology_profile not in {profile.name, f"{profile.name}@{profile.version}", profile.digest}:
        issues.append(OntologyValidationIssue(
            code="ontology.profile_mismatch",
            severity=OntologyIssueSeverity.ERROR,
            message="Snapshot ontology profile does not match the selected profile.",
        ))

    for node in snapshot.nodes:
        if node.node_type.casefold() not in node_types:
            issues.append(OntologyValidationIssue(
                code="ontology.unknown_node_type",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Node type {node.node_type!r} is not declared by the profile.",
                node_id=node.node_id,
            ))

    from .mathematics_contracts import contract_issues
    issues.extend(OntologyValidationIssue(**v) for v in contract_issues(profile, snapshot.nodes, snapshot.edges))
    present_node_types = {node.node_type.casefold() for node in snapshot.nodes}
    for required in profile.required_node_types:
        if required.casefold() not in present_node_types:
            issues.append(OntologyValidationIssue(
                code="ontology.required_node_type_missing",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Required node type {required!r} is absent from the snapshot.",
            ))

    nodes = {node.ref: node for node in snapshot.nodes}
    present_relations = {edge.relation.casefold() for edge in snapshot.edges}
    for required in profile.required_relation_types:
        if required.casefold() not in present_relations:
            issues.append(OntologyValidationIssue(
                code="ontology.required_relation_missing",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Required relation type {required!r} is absent from the snapshot.",
            ))

    for edge in snapshot.edges:
        relation = relation_types.get(edge.relation.casefold())
        if relation is None:
            issues.append(OntologyValidationIssue(
                code="ontology.unknown_relation_type",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Relation type {edge.relation!r} is not declared by the profile.",
                edge_id=edge.edge_id,
            ))
            continue
        source, target = nodes[edge.source_id], nodes[edge.target_id]
        if source.node_type.casefold() not in {item.casefold() for item in relation.source_types}:
            issues.append(OntologyValidationIssue(
                code="ontology.source_type_mismatch",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Source type {source.node_type!r} is invalid for relation {edge.relation!r}.",
                edge_id=edge.edge_id,
            ))
        if target.node_type.casefold() not in {item.casefold() for item in relation.target_types}:
            issues.append(OntologyValidationIssue(
                code="ontology.target_type_mismatch",
                severity=OntologyIssueSeverity.ERROR,
                message=f"Target type {target.node_type!r} is invalid for relation {edge.relation!r}.",
                edge_id=edge.edge_id,
            ))

    return OntologyValidationReport(
        profile=profile_summary,
        valid=not any(issue.severity == OntologyIssueSeverity.ERROR for issue in issues),
        issues=tuple(issues),
        node_count=len(snapshot.nodes),
        edge_count=len(snapshot.edges),
    )


__all__ = [
    "DEFAULT_PROFILE_NAMES",
    "OntologyIssueSeverity",
    "OntologyProfileSummary",
    "OntologyService",
    "OntologyRegistry",
    "OntologyValidationIssue",
    "OntologyValidationReport",
    "validate_delta_ontology",
    "validate_okf_snapshot",
]
