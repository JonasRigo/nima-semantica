from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.okf_contracts import OKFEdge, OKFNode, OKFSnapshot
from nima_semantica.ontology_services import OntologyRegistry, OntologyService, validate_okf_snapshot


def test_registry_lists_packaged_profiles_and_supports_digest_lookup():
    registry = OntologyRegistry()
    profiles = registry.list_profiles()

    assert {profile.name for profile in profiles} == {
        "claim_obligation",
        "literature_evidence",
        "literature_review",
        "theorem_dependencies",
    }
    selected = registry.get(name="claim_obligation", version="1.1.0")
    assert registry.by_digest(selected.digest) == selected


def test_graph_validation_returns_json_safe_report_for_valid_graph():
    profile = OntologyRegistry().get(name="claim_obligation", version="1.1.0")
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
        corpus_id="papers",
        ontology_profile=profile.digest,
        nodes=(
            OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers"),
            OKFNode(node_id="obligation-1", node_type="obligation", corpus_id="papers"),
        ),
        edges=(
            OKFEdge(
                edge_id="edge-1",
                relation="requires",
                source_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="claim-1"),
                target_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="obligation-1"),
                corpus_id="papers",
            ),
        ),
    )

    report = validate_okf_snapshot(snapshot, profile)

    assert report.valid is True
    assert report.model_dump(mode="json")["profile"]["digest"] == profile.digest


def test_graph_validation_reports_unknown_types_and_endpoint_mismatch():
    profile = OntologyRegistry().get(name="claim_obligation", version="1.1.0")
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None),
        corpus_id="papers",
        nodes=(
            OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers"),
            OKFNode(node_id="definition-1", node_type="definition", corpus_id="papers"),
        ),
        edges=(
            OKFEdge(
                edge_id="edge-1",
                relation="supports",
                source_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="claim-1"),
                target_id=GraphIdentity(corpus_id="papers", project_id=None, local_id="definition-1"),
                corpus_id="papers",
            ),
        ),
    )

    report = validate_okf_snapshot(snapshot, profile)

    assert report.valid is False
    assert "ontology.required_relation_missing" in {issue.code for issue in report.issues}
    assert "ontology.target_type_mismatch" in {issue.code for issue in report.issues}


def test_ontology_service_is_the_lookup_and_validation_boundary():
    service = OntologyService()
    profile = service.resolve("claim_obligation@1.1.0")
    assert service.get(name=profile.name, digest=profile.digest) == profile
    assert service.validate_snapshot(
        OKFSnapshot(
            snapshot_id="empty", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id=None, project_revision=None), corpus_id="papers",
            ontology_profile=profile.digest,
        )
    ).valid is False
