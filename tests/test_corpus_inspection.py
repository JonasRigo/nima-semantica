"""Deterministic native inventory acceptance; no paid providers."""
import pytest
from nima_semantica.corpus_inspection import InspectCorpusRequest, InspectCorpusContext, inspect_corpus
from nima_semantica.models import Record
from nima_semantica.source_tools import prepare_sources
from test_source_pipeline import store, pipeline, request, context, Provider


def inspect(store, project="research", **kwargs):
    return inspect_corpus(store, InspectCorpusRequest(**kwargs), InspectCorpusContext(corpus_id="papers", project_id=project))


def test_unconfigured_and_empty(store):
    assert inspect(None).diagnostics[0]["code"] == "corpus.store_unconfigured"
    result = inspect(store)
    assert result.status == "complete" and result.data["total_sources"] == 0
    assert not result.data["readiness"]["lexical_metadata_ready"]
    assert not result.receipt_ids


def test_unregistered_scope(store):
    result = inspect_corpus(store, InspectCorpusRequest(), InspectCorpusContext(corpus_id="absent"))
    assert result.status == "unavailable" and not result.data


@pytest.mark.parametrize("payload", [{"corpus_id": "other"}, {"project_id": "other"}, {"allow_writes": True},
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": "1"}, {"limit": 1.5}])
def test_strict_request(payload):
    with pytest.raises(ValueError):
        InspectCorpusRequest.model_validate(payload)


@pytest.mark.parametrize("vector", [False, True])
def test_current_readiness_and_no_writes(store, vector, monkeypatch):
    pipeline(store, request(index_mode="vector" if vector else "lexical"), provider=Provider() if vector else None)
    before = (store.revision, store.records())
    def forbidden(*args, **kwargs):
        raise AssertionError("inspection must not write or read artifact bytes")
    for name in ("put", "artifact", "read_artifact"):
        monkeypatch.setattr(store, name, forbidden)
    result = inspect(store)
    assert result.status == "complete", result
    ready = result.data["readiness"]
    assert ready["lexical_metadata_ready"]
    assert ready["vector_metadata_ready"] == vector
    assert not ready["source_fidelity_checked"] and not ready["artifact_integrity_checked"]
    assert (store.revision, store.records()) == before
    assert not result.receipt_ids and not result.artifacts


def test_stale_projection_and_pagination(store):
    pipeline(store)
    prepare_sources(store, request(operation_id="second", sources=[{"name": "b.md", "text": "Second source"}]), context())
    first = inspect(store, limit=1)
    assert first.data["readiness"]["stale_projections"] == 1
    assert not first.data["readiness"]["lexical_metadata_ready"]
    second = inspect(store, **first.data["next_request"])
    assert not second.data["has_more"]
    assert first.data["sources"][0]["source_id"] < second.data["sources"][0]["source_id"]
    prepare_sources(store, request(operation_id="third", sources=[{"name": "c.md", "text": "Third source"}]), context())
    assert inspect(store, **first.data["next_request"]).diagnostics[0]["code"] == "corpus.stale_page"
    assert inspect(store, offset=100).data["sources"] == []


def test_foreign_project_metadata_never_exposed_or_parsed(store):
    pipeline(store)
    for kind in ("SourceRegion", "GraphProjection", "EmbeddingBatch"):
        store.put(Record(kind=kind, corpus_id="papers", project_id="secret", content={"secret": "DO NOT LEAK"}))
    for project in (None, "research"):
        result = inspect(store, project)
        assert result.status == "complete"
        assert "DO NOT LEAK" not in result.model_dump_json()
        assert result.data["total_sources"] == 1
    assert inspect(store, None).data["readiness"]["current_projections"] == 0


def test_corrupt_visible_metadata_sanitized(store):
    store.put(Record(kind="SourceRegion", corpus_id="papers", content={"secret": "PRIVATE DETAIL"}))
    result = inspect(store)
    assert result.status == "failed" and not result.data
    assert "PRIVATE DETAIL" not in result.model_dump_json()


@pytest.mark.parametrize("kind,stage", [("SourceDescriptor", "source_descriptors"),
    ("SourceRegion", "source_regions"), ("GraphProjection", "projections")])
def test_invalid_metadata_identifies_stage_without_payload(store, kind, stage):
    store.put(Record(kind=kind, corpus_id="papers", project_id="research",
        content={"secret": "PRIVATE DETAIL"}))
    before = (store.revision, store.records())
    result = inspect(store)
    assert result.status == "failed" and not result.data
    assert result.diagnostics[0]["code"] == "corpus.invalid_metadata"
    assert result.diagnostics[0]["metadata_stage"] == stage
    assert "PRIVATE DETAIL" not in result.model_dump_json()
    assert not result.receipt_ids and not result.artifacts
    assert (store.revision, store.records()) == before


def test_ingested_project_notes_are_visible_only_in_their_project(store, monkeypatch):
    pipeline(store, request(operation_id="foreign-note", source_scope="project",
        sources=[{"name": "foreign.txt", "text": "Foreign project content."}]),
        context(project_id="other", allow_project_writes=True))
    pipeline(store)
    pipeline(store, request(operation_id="project-note", source_scope="project", index_mode="vector",
        sources=[{"name": "note.txt", "text": "Private project note."}]),
        context(allow_project_writes=True), provider=Provider())
    before = (store.revision, store.records())
    def forbidden(*args, **kwargs):
        raise AssertionError("inspection must not write or read artifact bytes")
    for name in ("put", "artifact", "read_artifact"):
        monkeypatch.setattr(store, name, forbidden)
    own = inspect(store, limit=1)
    assert own.status == "complete", own
    following = inspect(store, **own.data["next_request"])
    assert following.status == "complete" and not following.data["has_more"]
    assert {s["name"] for s in own.data["sources"] + following.data["sources"]} == {"source.md", "note.txt"}
    assert own.data["total_sources"] == own.data["total_regions"] == 2
    assert own.data["readiness"]["lexical_metadata_ready"]
    assert own.data["readiness"]["vector_covered_regions"] == 1
    for project, names in ((None, {"source.md"}), ("other", {"source.md", "foreign.txt"})):
        result = inspect(store, project)
        assert result.status == "complete", result
        assert {s["name"] for s in result.data["sources"]} == names
    assert (store.revision, store.records()) == before


@pytest.mark.parametrize("stored_project,descriptor_project", [(None, "research"),
    ("research", None), ("research", "other")])
def test_descriptor_scope_must_match_record(store, stored_project, descriptor_project):
    pipeline(store)
    original = store.records("SourceDescriptor")[0][1]
    store.put(original.model_copy(update={"project_id": stored_project,
        "content": {**original.content, "project_id": descriptor_project}}))
    result = inspect(store)
    assert result.status == "failed" and not result.data
    assert result.diagnostics[0]["metadata_stage"] == "source_descriptors"


def test_project_note_region_cannot_be_corpus_wide(store):
    pipeline(store, request(source_scope="project"), context(allow_project_writes=True))
    original = store.records("SourceRegion")[0][1]
    store.put(original.model_copy(update={"project_id": None,
        "content": {**original.content, "project_id": None}}))
    result = inspect(store)
    assert result.status == "failed" and not result.data
    assert result.diagnostics[0]["metadata_stage"] == "source_regions"


def test_project_note_inventory_through_read_only_snapshot(store):
    from nima_semantica.corpus_snapshot import CorpusSnapshot
    pipeline(store, request(source_scope="project"), context(allow_project_writes=True))
    expected = inspect(store)
    assert expected.status == "complete", expected
    before = (store.revision, store.records())
    with CorpusSnapshot(store.root, corpus_id="papers", project_id="research") as snapshot:
        assert inspect(snapshot) == expected
        assert snapshot._db.total_changes == 0
    assert (store.revision, store.records()) == before


def test_foreign_corpus_ignored(store):
    for kind in ("SourceDescriptor", "SourceRegion", "GraphProjection"):
        store.put(Record(kind=kind, corpus_id="secret", content={"secret": "PRIVATE DETAIL"}))
    result = inspect(store)
    assert result.status == "complete" and result.data["total_sources"] == 0
    assert "PRIVATE DETAIL" not in result.model_dump_json()


def test_partial_vector_coverage_is_not_ready(store):
    pipeline(store, request(index_mode="vector"), provider=Provider())
    pipeline(store, request(operation_id="second", sources=[{"name": "b.md", "text": "Unembedded source"}]))
    result = inspect(store)
    assert result.status == "complete", result
    assert result.data["readiness"]["lexical_metadata_ready"]
    assert result.data["readiness"]["vector_covered_regions"] > 0
    assert not result.data["readiness"]["vector_metadata_ready"]


def test_large_diagnostics_are_streamed_without_payload_materialization(store, monkeypatch):
    """Many repeated diagnostic payloads must never accumulate in an inventory list."""
    import tracemalloc
    from conftest import seed_region
    from nima_semantica.source_corpus import SourceCorpusService, SourceRegion
    first = seed_region(store)
    payload = {**first.content, "metadata": {"diagnostics": ["Conversion gap. " * 20000]}}
    for ordinal in range(64):
        SourceCorpusService(store).register_region(SourceRegion.model_validate({**payload, "ordinal": ordinal + 1}))
    original = store.records
    def no_region_list(kind=None, *args, **kwargs):
        if kind == "SourceRegion":
            raise AssertionError("SourceRegion payloads must be streamed")
        return original(kind, *args, **kwargs)
    monkeypatch.setattr(store, "records", no_region_list)
    before = store.revision
    tracemalloc.start()
    try:
        result = inspect(store)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.status == "complete", result
    assert result.data["total_regions"] == 65
    assert store.revision == before
    # A list would retain >19 MB of decoded diagnostics alone. Allow decoding overhead.
    assert peak < 12_000_000, peak
