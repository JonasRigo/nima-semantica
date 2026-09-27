"""Regression acceptance for the version 2 graph foundation."""

import pytest

from nima_semantica.evidence_provenance import EvidenceProvenanceService
from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.models import ConflictError, NimaError, Record
from nima_semantica.okf_contracts import GraphIdentity, GraphRevision, EvidenceReference, OKFDelta, OKFEdge, OKFNode, OKFSnapshot
from nima_semantica.okf_io import OKFBundle, OKFConceptDocument, export_bundle
from nima_semantica.okf_mapping import bundle_to_snapshot, snapshot_to_bundle

Failed = pytest.fail.Exception


def node(identifier, project=None, **kwargs):
    return OKFNode(node_id=identifier, node_type="claim", corpus_id="papers", project_id=project, **kwargs)


def edge(identifier, source, target, project=None):
    return OKFEdge(edge_id=identifier, source_id=GraphIdentity(corpus_id="papers", local_id=source), target_id=GraphIdentity(corpus_id="papers", local_id=target),
                   relation="supports", corpus_id="papers", project_id=project)


def commit(store, identifier, project=None, **changes):
    delta = OKFDelta(delta_id=identifier, base_revision=store.graph_revision("papers", project),
                     corpus_id="papers", project_id=project,
                     ontology_profile="claim_obligation", reason="Isolated audit fixture.", **changes)
    return commit_okf_delta(store, delta, OKFCommitApproval.for_delta(delta,
        approval_id=f"approval-{identifier}",
        approved_by="audit", rationale="Approve this scoped fixture only."))


def reference(store, project=None, quotation="Actual source text."):
    artifact = store.artifact(b"Actual source text.")
    from nima_semantica.source_corpus import SourceRegion, SourceDescriptor
    descriptor = SourceDescriptor(source_id="source", corpus_id="papers", artifact_id=artifact,
                                  source_revision=artifact, name="Fixture", media_type="text/plain")
    store.put(Record(kind="SourceDescriptor", corpus_id="papers", content=descriptor.model_dump(mode="json")))
    payload = SourceRegion(source_id="source", corpus_id="papers", project_id=project,
                           artifact_id=artifact, source_revision=artifact,
                           start=0, end=19, ordinal=0, text="Actual source text.")
    region = Record(kind="SourceRegion", corpus_id="papers", project_id=project,
                    content=payload.model_dump(mode="json"))
    store.put(region)
    return EvidenceReference(corpus_id="papers", project_id=project, artifact_id=artifact,
                             region_id=region.id, source_revision=artifact,
                             content_hash=artifact, quotation=quotation)


def test_project_delta_cannot_write_corpus_node(store):
    with pytest.raises((ValueError, NimaError)):
        commit(store, "project-write", "project-a", upsert_nodes=(node("corpus-node"),))


def test_project_delta_cannot_delete_corpus_edge(store):
    commit(store, "seed", upsert_nodes=(node("a"), node("b")), add_edges=(edge("e", "a", "b"),))
    try:
        commit(store, "delete", "project-a", remove_edge_ids=(GraphIdentity(corpus_id="papers", local_id="e"),))
    except (ValueError, NimaError):
        pass
    assert [item.edge_id for item in store.read_okf_snapshot(corpus_id="papers").edges] == ["e"]


@pytest.mark.parametrize("private_project,quotation", [(None, "Invented quotation."), ("project-b", "Actual source text.")])
def test_commit_rejects_false_or_out_of_scope_evidence(store, private_project, quotation):
    evidence = reference(store, private_project, quotation)
    candidate = node("claim", "project-a", evidence=(evidence,))
    assert not EvidenceProvenanceService(store).validate_reference(
        evidence, corpus_id="papers", project_id="project-a", target_id="claim").valid
    with pytest.raises((ValueError, NimaError)):
        commit(store, "invalid-evidence", "project-a", upsert_nodes=(candidate,))


def test_evidence_service_accepts_valid_edge_evidence(store):
    evidence = reference(store)
    relation = edge("e", "a", "b").model_copy(update={"evidence": (evidence,)})
    snapshot = OKFSnapshot(snapshot_id="s", graph_revision=store.graph_revision("papers"), corpus_id="papers",
                           nodes=(node("a"), node("b")), edges=(relation,))
    assert EvidenceProvenanceService(store).validate_snapshot(snapshot).valid


def test_commit_checks_endpoints_after_removal(store):
    commit(store, "seed", upsert_nodes=(node("a"), node("b")))
    with pytest.raises((ValueError, NimaError)):
        commit(store, "dangling", remove_node_ids=(GraphIdentity(corpus_id="papers", local_id="a"),), add_edges=(edge("e", "a", "b"),))


def test_corpus_delete_respects_project_dependencies(store):
    commit(store, "seed", upsert_nodes=(node("a"), node("b")))
    commit(store, "project-edge", "project-a", add_edges=(edge("e", "a", "b", "project-a"),))
    with pytest.raises((ValueError, NimaError)):
        commit(store, "delete-corpus", remove_node_ids=(GraphIdentity(corpus_id="papers", local_id="a"),))


def test_commit_validates_existing_endpoint_types(store):
    obligation = OKFNode(node_id="o", node_type="obligation", corpus_id="papers")
    commit(store, "seed", upsert_nodes=(obligation, node("c")))
    with pytest.raises((ValueError, NimaError)):
        commit(store, "invalid-relation", add_edges=(edge("e", "o", "c"),))


def test_graph_revision_remains_readable_after_update(store):
    first = commit(store, "first", upsert_nodes=(node("a", properties={"statement": "before"}),))
    commit(store, "second", upsert_nodes=(node("a", properties={"statement": "after"}),))
    previous = store.read_okf_snapshot(corpus_id="papers", revision=first.final_revision)
    assert previous.nodes[0].properties["statement"] == "before"


def test_bundle_preserves_snapshot_profile_and_metadata():
    snapshot = OKFSnapshot(snapshot_id="s", graph_revision=GraphRevision(corpus_id="papers"), corpus_id="papers",
                           ontology_profile="claim_obligation", metadata={"source": "audit"}, nodes=(node("a"),))
    restored = bundle_to_snapshot(snapshot_to_bundle(snapshot))
    assert restored.ontology_profile == snapshot.ontology_profile
    assert restored.metadata == snapshot.metadata


def test_bundle_preserves_project_view_with_corpus_references():
    snapshot = OKFSnapshot(snapshot_id="s", graph_revision=GraphRevision(corpus_id="papers", project_id="project-a", project_revision=0), corpus_id="papers", project_id="project-a",
                           nodes=(node("a"), node("b", "project-a")), edges=(edge("e", "a", "b", "project-a").model_copy(update={"target_id": node("b", "project-a").ref}),))
    restored = bundle_to_snapshot(snapshot_to_bundle(snapshot))
    assert restored.nodes == snapshot.nodes


def test_bundle_export_cannot_escape_root_through_symlink(tmp_path):
    root, outside = tmp_path / "bundle", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    bundle = OKFBundle(concepts=(OKFConceptDocument(concept_id="linked/claim", type="Claim"),))
    try:
        export_bundle(bundle, root)
    except (ValueError, OSError):
        pass
    assert not (outside / "claim.md").exists()
