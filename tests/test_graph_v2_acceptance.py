"""Standalone regressions for the maintained graph implementation."""
import hashlib
import json
from pathlib import Path

import pytest

from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.graph_service import GraphService
from nima_semantica.models import ConflictError, NimaError, Record, identity
from nima_semantica.okf_contracts import GraphIdentity, GraphRevision, OKFDelta, OKFEdge, OKFSnapshot
from nima_semantica.okf_io import OKFBundle, OKFConceptDocument, export_bundle, import_bundle
from nima_semantica.okf_mapping import bundle_to_snapshot, bundle_to_delta, snapshot_to_bundle
from nima_semantica.storage import GraphStore
from test_rewrite_audit_regressions import commit, node, reference


def test_local_ids_are_independent_across_corpus_and_projects(store):
    commit(store, "seed", upsert_nodes=(node("same"),))
    commit(store, "seed", "a", upsert_nodes=(node("same", "a"),))
    commit(store, "seed", "b", upsert_nodes=(node("same", "b"),))
    snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="a")
    assert {n.ref for n in snapshot.nodes} == {node("same").ref, node("same", "a").ref}
    commit(store, "remove", "a", remove_node_ids=(node("same", "a").ref,))
    assert len(store.read_okf_snapshot(corpus_id="papers", project_id="a").nodes) == 1
    assert len(store.read_okf_snapshot(corpus_id="papers", project_id="b").nodes) == 2
    assert store.read_okf_snapshot(corpus_id="papers", project_id="a", revision=snapshot.graph_revision) == snapshot


def test_history_survives_delete_and_reopen(tmp_path):
    store = GraphStore(tmp_path)
    first = commit(store, "add", upsert_nodes=(node("a"),))
    second = commit(store, "delete", remove_node_ids=(node("a").ref,))
    store.close()
    with_store = GraphStore(tmp_path)
    try:
        assert with_store.read_okf_snapshot(corpus_id="papers", revision=first.final_revision).nodes[0].node_id == "a"
        assert with_store.read_okf_snapshot(corpus_id="papers", revision=second.final_revision).nodes == ()
    finally:
        with_store.close()


def test_control_records_do_not_advance_graph_heads(store):
    before = store.graph_revision("papers", "a")
    store.put(Record(kind="ExecutionReceipt", corpus_id="papers", content={"event": "test"}))
    assert store.revision != "empty"
    assert store.graph_revision("papers", "a") == before
    assert store.graph_revision("other") == GraphRevision(corpus_id="other")


def test_approval_is_bound_to_payload_and_replay_is_exact(store):
    delta = OKFDelta(delta_id="d", corpus_id="papers", base_revision=store.graph_revision("papers"),
                     ontology_profile="claim_obligation", upsert_nodes=(node("a"),), reason="Reviewed")
    approval = OKFCommitApproval.for_delta(delta, approval_id="approval", approved_by="tester", rationale="Approved")
    result = commit_okf_delta(store, delta, approval)
    assert commit_okf_delta(store, delta, approval) == result
    for changed, authorized in ((delta.model_copy(update={"reason": "Changed"}), approval),
                                (delta, approval.model_copy(update={"approved_by": "someone-else"}))):
        with pytest.raises(ConflictError):
            commit_okf_delta(store, changed, authorized)
    assert store._db.execute("SELECT count(*) FROM graph_commits").fetchone()[0] == 1


def test_failed_commit_rolls_back_heads_receipts_and_history(store, monkeypatch):
    before = store.revision
    original = store.persist_graph_commit
    def fail(*args):
        original(*args)
        raise RuntimeError("injected failure before transaction commit")
    monkeypatch.setattr(store, "persist_graph_commit", fail)
    with pytest.raises(RuntimeError):
        commit(store, "failing", upsert_nodes=(node("a"),))
    assert store.revision == before
    assert store.graph_revision("papers").corpus_revision == 0
    for table in ("graph_heads", "graph_objects", "graph_versions", "graph_commits"):
        assert store._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_corpus_edge_cannot_name_private_endpoint():
    with pytest.raises(ValueError, match="scope"):
        OKFEdge(edge_id="e", corpus_id="papers", relation="supports",
                source_id=node("a", "private").ref, target_id=node("b").ref)


def test_node_type_update_revalidates_existing_edges(store):
    a, b = node("a"), node("b")
    relation = OKFEdge(edge_id="e", corpus_id="papers", relation="supports", source_id=a.ref, target_id=b.ref)
    commit(store, "seed", upsert_nodes=(a, b), add_edges=(relation,))
    before = store.graph_revision("papers")
    with pytest.raises(ConflictError, match="ontology"):
        commit(store, "invalid", upsert_nodes=(a.model_copy(update={"node_type": "obligation"}),))
    assert store.graph_revision("papers") == before


def test_corpus_node_cannot_use_private_evidence_even_in_project_view(store):
    evidence = reference(store, "a")
    from nima_semantica.evidence_provenance import EvidenceProvenanceService
    snapshot = OKFSnapshot(snapshot_id="s", corpus_id="papers", project_id="a",
                           graph_revision=store.graph_revision("papers", "a"), nodes=(node("a", evidence=(evidence,)),))
    assert not EvidenceProvenanceService(store).validate_snapshot(snapshot).valid


@pytest.mark.parametrize("project", [None, "a"])
def test_empty_snapshot_round_trip_in_memory_and_directory(tmp_path, project):
    revision = GraphRevision(corpus_id="papers", project_id=project, project_revision=0 if project else None)
    snapshot = OKFSnapshot(snapshot_id="empty", corpus_id="papers", project_id=project,
                           graph_revision=revision, metadata={"empty": True}, ontology_profile="claim_obligation")
    bundle = snapshot_to_bundle(snapshot)
    export_bundle(bundle, tmp_path / "bundle")
    assert bundle_to_snapshot(import_bundle(tmp_path / "bundle")) == snapshot


def test_mixed_scope_bundle_import_does_not_write_corpus_references(store):
    commit(store, "corpus", upsert_nodes=(node("same"),))
    commit(store, "project", "a", upsert_nodes=(node("same", "a"),))
    snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="a")
    bundle = snapshot_to_bundle(snapshot)
    assert bundle_to_snapshot(bundle) == snapshot
    delta = GraphService(store).prepare_bundle_delta(bundle, delta_id="import", reason="Reviewed")
    assert {n.project_id for n in delta.upsert_nodes} == {"a"}
    changed = snapshot.model_copy(update={"nodes": tuple(n.model_copy(update={"properties": {"changed": True}}) for n in snapshot.nodes)})
    with pytest.raises(ConflictError, match="corpus"):
        bundle_to_delta(snapshot_to_bundle(changed), delta_id="bad", reason="No corpus approval",
                        base_revision=snapshot.graph_revision, current_snapshot=snapshot)


@pytest.mark.parametrize("identifier", ["a//b", "a/./b", "a/../b", "/absolute", "a/", "a\\b"])
def test_noncanonical_paths_rejected(identifier):
    with pytest.raises(ValueError):
        OKFConceptDocument(concept_id=identifier, type="Claim")


def test_import_refuses_file_and_directory_symlinks(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntype: Claim\n---\nSECRET")
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "claim.md").symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        import_bundle(root)
    with pytest.raises((ValueError, OSError)):
        import_bundle(root / "claim.md")
    assert outside.read_text().endswith("SECRET")


def test_export_rejects_existing_target_and_symlinked_ancestor(tmp_path):
    bundle = OKFBundle(concepts=(OKFConceptDocument(concept_id="a", type="Claim"),))
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(FileExistsError):
        export_bundle(bundle, target)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        export_bundle(bundle, link / "new")
    assert list(target.iterdir()) == []




def test_generated_contract_catalog_matches_current_source():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("document_contracts", root / "deploy/document_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current = module.catalog()
    assert current == json.loads((root / "docs/reference/contracts.json").read_text())
    assert current["tools"] == json.loads((root / "docs/reference/tools.json").read_text())


def test_incompatible_sqlite_is_rejected_without_modification(tmp_path):
    import sqlite3
    path = tmp_path / "graph.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE legacy_data(value TEXT)")
        db.execute("INSERT INTO legacy_data VALUES ('preserve')")
    before = path.read_bytes()
    with pytest.raises(NimaError, match="fresh development directory"):
        GraphStore(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "writer.lock").exists()


def test_audit_history_retains_exact_approved_and_admitted_delta(store):
    commit(store, "audit", upsert_nodes=(node("a"),))
    digest, approved, admitted = store._db.execute("SELECT delta_hash,delta,admitted_delta FROM graph_commits").fetchone()
    assert identity(json.loads(approved)) == digest
    assert json.loads(approved)["ontology_profile"] == "claim_obligation"
    assert len(json.loads(admitted)["ontology_profile"]) == 64


def test_promotion_remaps_scoped_endpoints_and_replays_exactly(store):
    from nima_semantica.evidence_contracts import build_promotion_proposal, commit_promotion, PromotionApproval
    evidence = reference(store)
    a, b = node("a", "project", evidence=(evidence,)), node("b", "project", evidence=(evidence,))
    edge = OKFEdge(edge_id="e", corpus_id="papers", project_id="project", source_id=a.ref,
                   target_id=b.ref, relation="supports", evidence=(evidence,))
    commit(store, "seed", "project", upsert_nodes=(a, b), add_edges=(edge,))
    snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="project")
    proposal = build_promotion_proposal(snapshot, proposal_id="promote", target_corpus_id="papers", rationale="Reviewed")
    approval = PromotionApproval(proposal_id=proposal.proposal_id, proposal_hash=identity(proposal),
                                 approval_id="approve-promotion", approved_by="tester", rationale="Approved")
    first = commit_promotion(store, snapshot, proposal, approval)
    assert commit_promotion(store, snapshot, proposal, approval) == first
    promoted = store.read_okf_snapshot(corpus_id="papers")
    assert all(n.node_id.startswith("promoted-") and n.project_id is None for n in promoted.nodes)
    assert {promoted.edges[0].source_id, promoted.edges[0].target_id} == {n.ref for n in promoted.nodes}
    assert promoted.edges[0].promotion_origin.source_id == "e"


def test_retrieval_traverses_authoritative_scoped_graph(store):
    pytest.importorskip("semantica.vector_store")
    from conftest import seed_region
    from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
    from nima_semantica.graph_retrieval import GraphRetrievalRequest, GraphRetrievalService
    from nima_semantica.okf_contracts import EvidenceReference
    from nima_semantica.providers import ModelManifest
    regions = [seed_region(store, text) for text in ("Seed source.", "Related source.")]
    manifest = ModelManifest(provider="fixture", model="embedding", revision="1", parameters={}, dimension=2)
    indexed = EmbeddingIndexService(store).index(EmbeddingIndexRequest(corpus_id="papers",
        region_ids=tuple(r.id for r in regions), manifest=manifest), [[1.0, 0.0], [0.0, 1.0]])
    assert indexed.status == "completed"
    def grounded(identifier, region):
        return node(identifier, evidence=(EvidenceReference(corpus_id="papers", region_id=region.id,
            artifact_id=region.content["artifact_id"], content_hash=region.content["artifact_id"],
            source_revision=region.content["source_revision"], quotation=region.content["text"]),))
    a, b = grounded("a", regions[0]), grounded("b", regions[1])
    commit(store, "seed", upsert_nodes=(a, b), add_edges=(OKFEdge(edge_id="e", relation="supports",
        corpus_id="papers", source_id=a.ref, target_id=b.ref),))
    commit(store, "private", "other", upsert_nodes=(node("a", "other"),))
    class Provider:
        def embed_query(self, profile, query):
            return [[1.0, 0.0]], manifest
    result = GraphRetrievalService(store).execute(GraphRetrievalRequest(corpus_id="papers", query="seed",
        limit=1, max_hops=3), Provider())
    assert {r["id"] for r in result.selected} == {r.id for r in regions}
    assert set(result.context_packet.selected_graph_refs) == {a.ref, b.ref}


def test_extraction_and_embedding_do_not_send_out_of_scope_text_to_provider(store):
    from conftest import seed_region
    from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
    from nima_semantica.graph_extraction import GraphExtractionRequest, GraphExtractionService
    from nima_semantica.providers import ModelManifest
    region = seed_region(store, "Private source", project_id="private")
    manifest = ModelManifest(provider="fixture", model="embedding", revision="1", parameters={}, dimension=2)
    class Provider:
        def embed(self, *args):
            pytest.fail("private text reached provider")
    result = EmbeddingIndexService(store).embed_and_index(EmbeddingIndexRequest(corpus_id="papers",
        region_ids=(region.id,), manifest=manifest), Provider())
    assert result.status == "failed"
    assert result.diagnostics[0]["code"] == "ConflictError"
    assert result.diagnostics[0]["message"]
    assert not store.records("EmbeddingBatch", corpus_id="papers")
    assert store.records("ExecutionReceipt", corpus_id="papers")[0][1].content["status"] == "failed"
    request = GraphExtractionRequest(corpus_id="papers", project_id="authorized", graph_revision=store.graph_revision("papers", "authorized"),
        registry_revision="r", ontology_profile="claim_obligation", source_region_ids=(region.id,), question="extract")
    with pytest.raises(NimaError, match="scope"):
        GraphExtractionService(store)._source_regions(request)
