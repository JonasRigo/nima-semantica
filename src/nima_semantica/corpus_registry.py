"""Deterministic persistence boundary for the corpus-wide system registry."""

from __future__ import annotations

from contextlib import contextmanager

from .models import ConflictError, Record
from .registry_contracts import CorpusDescriptor, RegistryEntry, RegistryRevision


class CorpusRegistry:
    """Catalog immutable system resources without placing them in the project KG."""

    RECORD_KIND = "SystemRegistryEntry"
    CORPUS_KIND = "CorpusDescriptor"
    REVISION_KIND = "SystemRegistryRevision"

    def __init__(self, store):
        self.store = store

    def register_corpus(self, descriptor: CorpusDescriptor) -> str:
        with self._transaction():
            records = self.store.records(self.CORPUS_KIND, corpus_id=descriptor.corpus_id)
            if records:
                existing_id, existing = records[0]
                current = CorpusDescriptor.model_validate(existing.content)
                if current != descriptor:
                    raise ConflictError("corpus ID is already bound to different metadata")
                return existing_id
            return self.store.put(Record(
                kind=self.CORPUS_KIND,
                corpus_id=descriptor.corpus_id,
                content=descriptor.model_dump(mode="json"),
            ))

    def register_revision(self, revision: RegistryRevision) -> str:
        with self._transaction():
            self._require_corpus(revision.corpus_id)
            records = self.store.records(self.REVISION_KIND, corpus_id=revision.corpus_id)
            for record_id, record in records:
                current = RegistryRevision.model_validate(record.content)
                if current.revision_id == revision.revision_id:
                    if current != revision:
                        raise ConflictError("registry revision ID is already bound to different metadata")
                    return record_id
                if current.sequence == revision.sequence:
                    raise ConflictError("registry sequence is already occupied")
            if revision.parent_revision is not None:
                parent = self._revision(revision.parent_revision, revision.corpus_id)
                if parent is None:
                    raise ConflictError("registry revision parent is unavailable")
                if parent.sequence + 1 != revision.sequence:
                    raise ConflictError("registry revision sequence does not follow its parent")
            return self.store.put(Record(
                kind=self.REVISION_KIND,
                corpus_id=revision.corpus_id,
                content=revision.model_dump(mode="json"),
            ))

    def register(self, entry: RegistryEntry) -> str:
        with self._transaction():
            self._require_corpus(entry.corpus_id)
            revision = self._revision(entry.registry_revision, entry.corpus_id)
            if revision is None:
                raise ConflictError("registry entry references an unavailable revision")
            if entry.resource_id not in revision.changed_resource_ids:
                raise ConflictError("registry revision does not declare this resource change")
            matches = [
                (record_id, RegistryEntry.model_validate(record.content))
                for record_id, record in self.store.records(self.RECORD_KIND, corpus_id=entry.corpus_id)
                if record.content.get("resource_id") == entry.resource_id
                and (entry.resource_kind.value != "artifact" or record.project_id == entry.project_id)
            ]
            if matches:
                record_id, current = matches[0]
                if current != entry:
                    raise ConflictError("registry resource ID is already bound to different metadata")
                return record_id
            return self.store.put(Record(
                kind=self.RECORD_KIND,
                corpus_id=entry.corpus_id,
                project_id=entry.project_id,
                content=entry.model_dump(mode="json"),
            ))

    def resolve(self, resource_id: str, *, corpus_id: str, project_id: str | None = None, exact_scope: bool = False) -> RegistryEntry | None:
        records = self.store.records(self.RECORD_KIND, corpus_id=corpus_id, project_id=project_id)
        for _, record in sorted(records, key=lambda item: item[1].project_id != project_id):
            if record.project_id not in (None, project_id):
                continue
            if exact_scope and record.project_id != project_id:
                continue
            if record.content.get("resource_id") == resource_id:
                return self._checked_entry(record, corpus_id)
        return None

    def list(self, *, corpus_id: str, project_id: str | None = None) -> tuple[RegistryEntry, ...]:
        values = [self._checked_entry(record, corpus_id)
                  for _, record in self.store.records(self.RECORD_KIND, corpus_id=corpus_id, project_id=project_id)
                  if record.project_id in (None, project_id)]
        return tuple(sorted(values, key=lambda item: item.resource_id))

    @staticmethod
    def _checked_entry(record, corpus_id):
        entry = RegistryEntry.model_validate(record.content)
        if entry.corpus_id != corpus_id or entry.project_id != record.project_id:
            raise ConflictError("registry entry scope differs from stored scope")
        return entry

    def corpus(self, corpus_id: str) -> CorpusDescriptor | None:
        records = self.store.records(self.CORPUS_KIND, corpus_id=corpus_id)
        return CorpusDescriptor.model_validate(records[0][1].content) if records else None

    def revision(self, revision_id: str, *, corpus_id: str) -> RegistryRevision | None:
        return self._revision(revision_id, corpus_id)

    def _require_corpus(self, corpus_id: str) -> CorpusDescriptor:
        descriptor = self.corpus(corpus_id)
        if descriptor is None:
            raise ConflictError("registry operation references an unregistered corpus")
        return descriptor

    def _revision(self, revision_id: str, corpus_id: str) -> RegistryRevision | None:
        for _, record in self.store.records(self.REVISION_KIND, corpus_id=corpus_id):
            revision = RegistryRevision.model_validate(record.content)
            if revision.revision_id == revision_id:
                return revision
        return None

    @contextmanager
    def _transaction(self):
        """Join a caller transaction while retaining standalone atomic writes."""
        with self.store.joined_transaction():
            yield


__all__ = ["CorpusRegistry"]
