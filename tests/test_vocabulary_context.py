"""Scoped optional context, exact readback and private-session compatibility."""
import json
import pytest
from test_source_pipeline import store
from nima_semantica.models import ConflictError, NimaError, canonical, identity
from nima_semantica.vocabulary_contracts import VocabularyPolicy
from nima_semantica.vocabulary_context import seal_vocabulary, publish_snapshot, vocabulary_context, load_snapshot, render_vocabulary


def snapshot(store, *, project="research", entries=None):
    from nima_semantica.vocabulary_context import vocabulary_header
    graph = store.read_okf_snapshot(corpus_id="papers", project_id=project)
    return seal_vocabulary({**vocabulary_header(store, corpus_id="papers", project_id=project), "corpus_id": "papers", "project_id": project,
        "graph_revision": graph.graph_revision.model_dump(mode="json"),
        "graph_snapshot_hash": identity(graph), "generated_at": "2026-10-14T00:00:00Z",
        "coverage_limitations": ["Definitions are provisional project conventions."],
        "entries": entries or [{"entry_id": "gap", "term": "gap", "definition": "The specified spectral gap Δ, under the stated assumptions.",
            "notation": "Δ", "assumptions": ["Specified operator and domain"],
            "distinctions": ["A transport threshold is a separate observable."], "status": "proposed"}]})


def test_disabled_never_reads_vocabulary_or_project_policy(monkeypatch):
    from nima_semantica import vocabulary_context as module
    monkeypatch.setattr(module, "project_policy", lambda *a: pytest.fail("off must bypass project vocabulary"))
    assert vocabulary_context(object(), corpus_id="papers", project_id="research", override=False) == {"status": "disabled", "enabled": False}
    assert vocabulary_context(object(), corpus_id="papers", project_id="research", configured=VocabularyPolicy()) == {"status": "disabled", "enabled": False}


def test_missing_vocabulary_is_nonblocking_without_model_calls(monkeypatch):
    result = vocabulary_context(None, corpus_id="papers", project_id="research", configured=VocabularyPolicy(enabled=True))
    assert result["status"] == "unavailable" and result["enabled"]
    from nima_semantica.vocabulary_context import private_vocabulary
    monkeypatch.setattr("nima_semantica.corpus_snapshot.CorpusSnapshot", lambda *a, **k: pytest.fail("Missing vocabulary must not require a corpus connection"))
    assert private_vocabulary("papers", "research", "Investigate", configured=VocabularyPolicy(enabled=True), root="/unavailable")["status"] == "unavailable"


def test_math_vocabulary_inspection_selects_session_pin_without_an_object_identifier():
    from nima_semantica.math_mcp_contracts import Inspect
    assert Inspect(session_id="session", kind="vocabulary").identifier is None
    for kind in ("node", "receipt", "action"):
        with pytest.raises(ValueError):
            Inspect(session_id="session", kind=kind)


def test_bounded_context_omits_whole_definitions_and_exact_readback_preserves_them(store):
    snap = snapshot(store, entries=[{"entry_id": "large", "term": "large", "definition": "λ = α + β. " * 1000, "status": "proposed"}])
    policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research").model_copy(update={"max_context_bytes": 1024})
    result = vocabulary_context(store, corpus_id="papers", project_id="research", query="large", configured=policy)
    assert result["status"] == "active" and result["entries"] == [] and result["omitted_count"] == 1
    assert len(canonical(result)) <= 1024
    loaded = load_snapshot(store, policy, corpus_id="papers", project_id="research")
    assert loaded == snap and loaded.entries[0].definition == snap.entries[0].definition
    assert snap.entries[0].definition in render_vocabulary(snap)


def test_binding_rejects_foreign_and_tampered_hash_and_preserves_revision(store):
    snap = snapshot(store)
    revision = store.graph_revision("papers", "research")
    policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    assert publish_snapshot(store, snap, corpus_id="papers", project_id="research") == policy
    assert store.graph_revision("papers", "research") == revision
    with pytest.raises(NimaError):
        load_snapshot(store, policy, corpus_id="papers", project_id="other")
    with pytest.raises(ConflictError):
        vocabulary_context(store, corpus_id="papers", project_id="research", configured=policy.model_copy(update={"snapshot_hash": "0"*64}))
    damaged = snap.model_dump(mode="json"); damaged["entries"][0]["definition"] = "Changed"
    from nima_semantica.vocabulary_contracts import VocabularySnapshot
    with pytest.raises(ValueError): VocabularySnapshot.model_validate(damaged)


def test_stale_new_context_skips_guidance_while_exact_old_pin_remains_readable(store, monkeypatch):
    snap = snapshot(store)
    policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    changed = snap.graph_revision.model_copy(update={"project_revision": snap.graph_revision.project_revision + 1})
    monkeypatch.setattr(store, "graph_revision", lambda *a, **k: changed)
    assert vocabulary_context(store, corpus_id="papers", project_id="research", configured=policy)["status"] == "unavailable"
    assert load_snapshot(store, policy, corpus_id="papers", project_id="research", current=False) == snap


def test_private_math_and_proof_sessions_keep_old_identity_when_policy_changes(tmp_path):
    from nima_semantica.math_session_service import MathSessionService, MathServiceConfig
    from nima_semantica.proof_mcp import ProofService, ProofConfig
    math = MathSessionService(MathServiceConfig(database_path=str(tmp_path/"math.sqlite"), project_id="p", run_id="r"))
    opened = math.open("Explore", ["$"], request_id="old", normalized_reasoning=False)
    sid = opened["session_id"]; old_scope = math._scope(); old_hash = opened["result"]["binding_hash"]
    changed = MathSessionService(math.config.model_copy(update={"normalized_reasoning": VocabularyPolicy(enabled=True)}))
    assert changed._scope() == old_scope
    status = changed.read(sid, "status")
    assert status["result"]["binding_hash"] == old_hash and status["result"]["normalized_reasoning"]["status"] == "disabled"
    new = changed.open("Another investigation", ["$"], request_id="new")
    assert new["result"]["normalized_reasoning"]["status"] == "unavailable"
    proof = ProofService(ProofConfig(database_path=str(tmp_path/"proof.sqlite"), project_id="p"))
    old = proof.invoke("open", {"request_id": "old", "target": "Explore", "normalized_reasoning": False})
    new_proof = ProofService(proof.config.model_copy(update={"normalized_reasoning": {"enabled": True}}))
    assert new_proof.read("status", {"session_id": old["session_id"]})["normalized_reasoning"]["status"] == "disabled"
    assert new_proof.invoke("open", {"request_id": "old", "target": "Explore", "normalized_reasoning": False})["session_id"] == old["session_id"]


def test_project_configuration_is_recoverable_and_never_admits_graph(lexical_installation, tmp_path):
    from nima_semantica.cli import main
    from nima_semantica.vocabulary_installation import configure_project
    from nima_semantica.installation import project_path, project_binding
    from nima_semantica.config_reconciliation import recover_reconciliation
    config = lexical_installation
    assert main(["project", "init", "research", "--corpus", "papers", "--offline", "--path", str(tmp_path/"workspace")]) == 0
    directory = project_path(config, "papers", "research")
    original = {p: p.read_bytes() for p in (directory/"project.json", directory/"math.json", directory/"proof.json")}
    changed = configure_project(config, "papers", "research", enabled=True)
    assert project_binding(config, "papers", "research")["normalized_reasoning"]["enabled"]
    assert changed["normalized_reasoning"]["snapshot_artifact_id"] is None
    recover_reconciliation(changed["recovery_manifest"], "restore")
    assert all(p.read_bytes() == data for p, data in original.items())
