"""Standalone deterministic ontology tool acceptance; no model or benchmark calls."""
from asyncio import CancelledError
import pytest

from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import ConflictError
from nima_semantica.ontology_profiles import OntologyProfile, saved_profile
from nima_semantica.ontology_services import OntologyService
from nima_semantica.ontology_tools import (
    LoadOntologyRequest, SaveOntologyRequest, OntologyContext, load_ontology, save_ontology,
)
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryResourceKind
from nima_semantica.storage import GraphStore


@pytest.fixture
def store(tmp_path):
    value = GraphStore(tmp_path / "store")
    CorpusRegistry(value).register_corpus(CorpusDescriptor(corpus_id="papers", name="Test", corpus_revision="1"))
    yield value
    value.close()


def context(**kwargs):
    return OntologyContext(**({"corpus_id": "papers", "project_id": "research", "allow_writes": True} | kwargs))


def profile(**kwargs):
    return {"name": "custom", "version": "1.0.0", "node_types": [{"name": "claim", "description": "A claim"}], **kwargs}


def request(**kwargs):
    return SaveOntologyRequest(**({"mode": "save", "operation_id": "save-1", "profile": profile()} | kwargs))


def test_packaged_exact_lookup_and_bounded_list_are_pure(store):
    before = store.revision
    listing = load_ontology(store, LoadOntologyRequest(limit=1), context())
    assert listing.data["total"] == 4 and listing.data["has_more"]
    p = saved_profile()
    for selection in ({"name": p.name, "version": p.version}, {"digest": p.digest}):
        result = load_ontology(None, LoadOntologyRequest(mode="load", **selection), context())
        assert result.data["profile"] == p.model_dump(mode="json")
        assert result.receipt_ids == ()
    assert store.revision == before
    assert load_ontology(store, LoadOntologyRequest(mode="load", name=p.name, version="0.0.1"), context()).status == "unavailable"


@pytest.mark.parametrize("kwargs", [{"mode": "load"}, {"mode": "load", "name": "custom"},
    {"version": "1.0.0"}, {"limit": True}, {"limit": 201}, {"offset": -1}, {"digest": "bad"}])
def test_invalid_or_inexact_selection_rejected(kwargs):
    with pytest.raises(ValueError):
        LoadOntologyRequest(**kwargs)


@pytest.mark.parametrize("field", ["corpus_id", "project_id", "allow_writes", "actor", "store_path", "approved", "valid"])
def test_public_authority_fields_rejected(field):
    for model, base in ((LoadOntologyRequest, {}), (SaveOntologyRequest, {"profile": profile()})):
        with pytest.raises(ValueError):
            model(**base, **{field: "forged"})


def test_save_load_reopen_replay_registry_and_no_graph_mutation(store):
    before = store.graph_revision("papers", "research")
    result = save_ontology(store, request(), context())
    assert result.status == "complete", result
    revision = store.revision
    assert save_ontology(store, request(), context()) == result
    assert store.revision == revision
    loaded = load_ontology(store, LoadOntologyRequest(mode="load", digest=result.data["digest"]), context())
    assert loaded.data["profile"] == OntologyProfile.model_validate(profile()).model_dump(mode="json")
    assert loaded.data["registry_revision"] == result.data["registry_revision"]
    registry = CorpusRegistry(store)
    entry = next(e for e in registry.list(corpus_id="papers", project_id="research") if e.resource_kind == RegistryResourceKind.ONTOLOGY_PROFILE)
    assert entry.artifact_id == result.data["artifact_id"]
    assert store.graph_revision("papers", "research") == before
    assert OntologyService.from_store(store, corpus_id="papers", project_id="research").resolve(result.data["digest"]).name == "custom"
    path = store.root
    store.close()
    reopened = GraphStore(path)
    try:
        assert load_ontology(reopened, LoadOntologyRequest(mode="load", digest=result.data["digest"]), context()).data == loaded.data
    finally:
        reopened.close()


def test_scopes_do_not_leak_and_identical_profiles_can_exist_in_distinct_projects(store):
    first = save_ontology(store, request(), context())
    for c in (context(project_id="foreign"), context(project_id=None), context(corpus_id="other")):
        assert load_ontology(store, LoadOntologyRequest(mode="load", digest=first.data["digest"]), c).status == "unavailable"
    second = save_ontology(store, request(), context(project_id="foreign"))
    assert second.status == "complete"
    assert first.data["artifact_id"] != second.data["artifact_id"]
    shared = save_ontology(store, request(operation_id="shared", profile=profile(name="shared")), context(project_id=None))
    assert shared.status == "complete"
    assert load_ontology(store, LoadOntologyRequest(mode="load", digest=shared.data["digest"]), context()).status == "complete"


def test_version_is_immutable_and_new_versions_are_explicit(store):
    assert save_ontology(store, request(), context()).status == "complete"
    changed = profile(instructions="changed")
    denied = save_ontology(store, request(operation_id="replace", profile=changed), context())
    assert denied.status == "failed"
    assert save_ontology(store, request(operation_id="version-2", profile={**changed, "version": "2.0.0"}), context()).status == "complete"
    again = save_ontology(store, request(operation_id="same-profile"), context())
    assert again.status == "complete" and again.data["already_exists"]
    with pytest.raises(ConflictError):
        save_ontology(store, request(profile=changed), context())


def test_permissions_and_validate_mode_do_not_write(store):
    before = store.revision
    assert save_ontology(store, request(), context(allow_writes=False)).status == "failed"
    assert save_ontology(None, SaveOntologyRequest(profile=profile()), context()).data["valid"]
    assert save_ontology(None, request(), context()).status == "unavailable"
    assert store.revision == before


@pytest.mark.parametrize("bad", [profile(node_types=[]), profile(node_types=[{"name": "claim", "description": ""}, {"name": "Claim", "description": ""}]),
    profile(required_node_types=["absent"]), profile(relation_types=[{"name": "supports", "source_types": ["absent"], "target_types": ["claim"], "description": ""}]),
    profile(version="latest"), profile(unexpected="field")])
def test_invalid_profiles_are_rejected_and_authorized_attempts_recorded(store, bad):
    result = save_ontology(store, request(profile=bad), context())
    assert result.status == "failed" and result.receipt_ids
    assert save_ontology(store, request(profile=bad), context()) == result
    assert not store.records("ArtifactEnvelope")
    receipt = ExecutionReceiptService(store).get(result.receipt_ids[0], corpus_id="papers", project_id="research")
    assert receipt.status == "failed"


def test_stale_revision_unregistered_corpus_and_foreign_run_record_failure(store):
    for i, (req, ctx) in enumerate(((request(expected_store_revision="stale"), context()),
        (request(), context(corpus_id="unknown")), (request(run_id="foreign"), context()))):
        result = save_ontology(store, req.model_copy(update={"operation_id": f"fail-{i}"}), ctx)
        assert result.status == "failed" and result.receipt_ids
    assert not store.records("ArtifactEnvelope")


@pytest.mark.parametrize("error", [RuntimeError, CancelledError, KeyboardInterrupt])
def test_receipt_failure_rolls_back_publication_and_records_attempt(store, monkeypatch, error):
    original = ExecutionReceiptService.record
    def fail_success(self, receipt, **kwargs):
        if receipt.stage == "save_ontology" and receipt.status == "completed":
            raise error("injected after publication")
        return original(self, receipt, **kwargs)
    monkeypatch.setattr(ExecutionReceiptService, "record", fail_success)
    if error == RuntimeError:
        assert save_ontology(store, request(), context()).status == "failed"
    else:
        with pytest.raises(error):
            save_ontology(store, request(), context())
    assert not store.records("ArtifactEnvelope")
    assert not store.records("SystemRegistryEntry")
    assert not store.records("SystemRegistryRevision")
    attempts = store.records("ExecutionReceipt")
    assert len(attempts) == 1
    assert attempts[0][1].content["status"] == ("failed" if error == RuntimeError else "interrupted")
    assert load_ontology(store, LoadOntologyRequest(mode="load", name="custom", version="1.0.0"), context()).status == "unavailable"


def test_corrupt_artifact_fails_closed(store, monkeypatch):
    saved = save_ontology(store, request(), context())
    original = store.read_artifact
    def corrupt(identifier):
        return b"{}" if identifier == saved.data["artifact_id"] else original(identifier)
    monkeypatch.setattr(store, "read_artifact", corrupt)
    assert load_ontology(store, LoadOntologyRequest(), context()).status == "failed"


def test_corpus_publication_cannot_shadow_a_project_identity(store):
    original = save_ontology(store, request(), context())
    result = save_ontology(store, request(profile=profile(instructions="different")), context(project_id=None))
    assert result.status == "failed"
    assert load_ontology(store, LoadOntologyRequest(mode="load", digest=original.data["digest"]), context()).status == "complete"


def test_packaged_identity_cannot_be_redefined(store):
    p = saved_profile().model_dump(mode="json")
    result = save_ontology(store, request(profile={**p, "instructions": "overwrite"}), context())
    assert result.status == "failed"
    unchanged = save_ontology(store, request(operation_id="unchanged", profile=p), context())
    assert unchanged.status == "complete" and unchanged.data["already_exists"]
    assert not store.records("ArtifactEnvelope")


def test_name_version_and_digest_must_all_match(store):
    p = saved_profile()
    result = load_ontology(store, LoadOntologyRequest(mode="load", name="other", version=p.version, digest=p.digest), context())
    assert result.status == "unavailable"


def test_persistent_receipt_failure_never_claims_success(store, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("receipt store unavailable")
    monkeypatch.setattr(ExecutionReceiptService, "record", unavailable)
    with pytest.raises(OSError):
        save_ontology(store, request(), context())
    assert not store.records("ArtifactEnvelope")
    assert not store.records("SystemRegistryRevision")


def test_successful_receipt_is_visible_in_matching_research_run(store):
    from nima_semantica.research_contracts import ResearchRun
    from nima_semantica.research_run_service import ResearchRunService
    from nima_semantica.research_run_tool import ResearchRunContext, ResearchRunRequest, research_run
    ResearchRunService(store).create_run(ResearchRun(run_id="run", corpus_id="papers", project_id="research",
        objective="Test", skill_id="manual", skill_revision="1"))
    saved = save_ontology(store, request(run_id="run"), context())
    assert saved.status == "complete"
    inspected = research_run(store, ResearchRunRequest(run_id="run"),
        ResearchRunContext(corpus_id="papers", project_id="research", actor="harness"))
    assert [attempt["receipt_id"] for attempt in inspected.data["attempts"]] == list(saved.receipt_ids)
