import pytest

from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.models import ConflictError
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision
from nima_semantica.research_contracts import ResearchRun, TaskTransition
from nima_semantica.research_run_service import ResearchRunService
from nima_semantica.storage import GraphStore


def run(registry_revision="registry:1"):
    return ResearchRun(
        run_id="run-1", corpus_id="papers", project_id="project-a",
        skill_id="nima-research", skill_revision="skill:1",
        registry_revision=registry_revision, objective="Investigate the claim.",
    )


def transition(*, transition_id="transition-1", from_state="running", to_state="paused",
               from_revision=0, reason="Pause for review"):
    return TaskTransition(
        transition_id=transition_id, run_id="run-1", corpus_id="papers",
        project_id="project-a", from_state=from_state, to_state=to_state,
        from_run_revision=from_revision, to_run_revision=from_revision + 1,
        reason=reason, actor="harness", doubt_or_review="Inspect the evidence.",
    )


def configured_service(store):
    registry = CorpusRegistry(store)
    registry.register_corpus(CorpusDescriptor(
        corpus_id="papers", name="Papers", corpus_revision="corpus:1"
    ))
    registry.register_revision(RegistryRevision(
        revision_id="registry:1", corpus_id="papers", sequence=1,
        changed_resource_ids=(),
    ))
    return ResearchRunService(store, registry)


def test_run_and_transition_persistence_is_append_only_and_idempotent(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = configured_service(store)
        run_record_id = service.create_run(run())
        assert service.create_run(run()) == run_record_id
        assert service.get_run("run-1", corpus_id="papers", project_id="project-a").run_revision == 0

        paused_id = service.append_transition(transition())
        assert service.append_transition(transition()) == paused_id
        current = service.get_run("run-1", corpus_id="papers", project_id="project-a")
        assert current.status == "paused" and current.run_revision == 1
        assert len(store.records("ResearchRun", corpus_id="papers", project_id="project-a")) == 2
        assert service.transitions("run-1", corpus_id="papers", project_id="project-a")[0].transition_id == "transition-1"
    finally:
        store.close()


def test_run_and_transition_reject_divergent_replays_and_stale_state(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = configured_service(store)
        service.create_run(run())
        service.append_transition(transition())
        with pytest.raises(ConflictError, match="transition ID"):
            service.append_transition(transition(reason="Different reason"))
        with pytest.raises(ConflictError, match="stale run state"):
            service.append_transition(transition(
                transition_id="transition-2", from_state="running", to_state="completed"
            ))
    finally:
        store.close()


def test_terminal_runs_cannot_transition_and_store_revision_is_checked(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = configured_service(store)
        service.create_run(run())
        before = store.revision
        service.append_transition(transition(to_state="completed", reason="Finished"), expected_store_revision=before)
        with pytest.raises(ConflictError, match="no transition"):
            service.append_transition(transition(
                transition_id="transition-2", from_state="completed", to_state="running",
                from_revision=1,
            ))
        with pytest.raises(ConflictError, match="stale store revision"):
            service.append_transition(transition(
                transition_id="transition-3", from_state="completed", to_state="running",
                from_revision=1,
            ), expected_store_revision=before)
    finally:
        store.close()


def test_registry_revision_is_required_when_registry_is_configured(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = configured_service(store)
        with pytest.raises(ConflictError, match="unavailable registry revision"):
            service.create_run(run(registry_revision="registry:missing"))
    finally:
        store.close()
