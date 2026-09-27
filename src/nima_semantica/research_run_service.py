"""Append-only persistence for harness-selected research runs and transitions."""

from __future__ import annotations

from .corpus_registry import CorpusRegistry
from .models import ConflictError, NimaError, Record
from .research_contracts import ResearchRun, TaskTransition


class ResearchRunService:
    """Persist audit records without scheduling or executing tasks."""

    RUN_KIND = "ResearchRun"
    TRANSITION_KIND = "TaskTransition"
    _ALLOWED_TRANSITIONS = {
        "running": {"running", "paused", "completed", "failed", "cancelled"},
        "paused": {"paused", "running", "failed", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }

    def __init__(self, store, registry: CorpusRegistry | None = None):
        self.store = store
        self.registry = registry

    def create_run(self, run: ResearchRun, *, expected_store_revision: str | None = None) -> str:
        if run.run_revision != 0:
            raise ValueError("a new research run must start at run_revision zero")
        self._validate_registry_revision(run)
        with self.store.joined_transaction(expected_store_revision):
            current = self._latest_run(run.run_id, run.corpus_id, run.project_id)
            if current is not None:
                # Creation replay binds the initial record, not today's state.
                initial = [(record_id, ResearchRun.model_validate(record.content))
                    for record_id, record in self.store.records(self.RUN_KIND, corpus_id=run.corpus_id, project_id=run.project_id)
                    if record.content.get("run_id") == run.run_id and record.content.get("run_revision") == 0]
                if len(initial) != 1:
                    raise ConflictError("research run has no unique initial record")
                record_id, existing = initial[0]
                if existing != run:
                    raise ConflictError("research run ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind=self.RUN_KIND, corpus_id=run.corpus_id,
                project_id=run.project_id, content=run.model_dump(mode="json"),
            ))

    def append_transition(self, transition: TaskTransition, *, expected_store_revision: str | None = None) -> str:
        with self.store.joined_transaction(expected_store_revision):
            existing_transition = self._transition_by_id(
                transition.transition_id, transition.corpus_id, transition.project_id
            )
            if existing_transition is not None:
                record_id, existing = existing_transition
                if existing != transition:
                    raise ConflictError("transition ID is already bound to different metadata")
                return record_id

            current = self._latest_run(transition.run_id, transition.corpus_id, transition.project_id)
            if current is None:
                raise NimaError("task transition references an unknown research run")
            run_record_id, run = current
            if run.status != transition.from_state:
                raise ConflictError("task transition starts from a stale run state")
            if run.run_revision != transition.from_run_revision:
                raise ConflictError("task transition starts from a stale run revision")
            if transition.to_state not in self._ALLOWED_TRANSITIONS[run.status]:
                raise ConflictError("research run has no transition to the requested state")

            transition_record = Record(
                kind=self.TRANSITION_KIND, corpus_id=transition.corpus_id,
                project_id=transition.project_id, parents=(run_record_id,),
                content=transition.model_dump(mode="json"),
            )
            transition_record_id = self.store.put(transition_record)
            next_run = run.model_copy(update={
                "status": transition.to_state,
                "run_revision": transition.to_run_revision,
            })
            self.store.put(Record(
                kind=self.RUN_KIND, corpus_id=next_run.corpus_id,
                project_id=next_run.project_id,
                parents=(run_record_id, transition_record_id),
                content=next_run.model_dump(mode="json"),
            ))
            return transition_record_id

    def get_run(self, run_id: str, *, corpus_id: str, project_id: str) -> ResearchRun | None:
        current = self._latest_run(run_id, corpus_id, project_id)
        return current[1] if current is not None else None

    def transitions(self, run_id: str, *, corpus_id: str, project_id: str) -> tuple[TaskTransition, ...]:
        values = []
        for _, record in self.store.records(self.TRANSITION_KIND, corpus_id=corpus_id, project_id=project_id):
            transition = TaskTransition.model_validate(record.content)
            if transition.run_id == run_id and transition.corpus_id == corpus_id and transition.project_id == project_id:
                values.append(transition)
        return tuple(sorted(values, key=lambda item: (item.from_run_revision, item.transition_id)))

    def _validate_registry_revision(self, run: ResearchRun) -> None:
        if self.registry is not None and run.registry_revision is not None:
            if self.registry.revision(run.registry_revision, corpus_id=run.corpus_id) is None:
                raise ConflictError("research run references an unavailable registry revision")

    def _latest_run(self, run_id: str, corpus_id: str, project_id: str):
        values = []
        for record_id, record in self.store.records(self.RUN_KIND, corpus_id=corpus_id, project_id=project_id):
            run = ResearchRun.model_validate(record.content)
            if run.run_id == run_id and run.corpus_id == corpus_id and run.project_id == project_id:
                values.append((record_id, run))
        return max(values, key=lambda item: (item[1].run_revision, item[0])) if values else None

    def _transition_by_id(self, transition_id: str, corpus_id: str, project_id: str):
        for record_id, record in self.store.records(self.TRANSITION_KIND, corpus_id=corpus_id, project_id=project_id):
            transition = TaskTransition.model_validate(record.content)
            if transition.transition_id == transition_id:
                return record_id, transition
        return None


__all__ = ["ResearchRunService"]
