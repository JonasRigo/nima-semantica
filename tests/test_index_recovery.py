"""Recovery changes budgets without repeating preparation or weakening evidence."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from nima_semantica.storage import GraphStore
from nima_semantica.corpus_snapshot import CorpusSnapshot
from nima_semantica.cli import register_corpus
from nima_semantica.graph_projection import GraphProjectionService
from nima_semantica.index_recovery import check_index, rebuild_index
from nima_semantica.projection_limits import ProjectionLimitError, ProjectionLimits
from nima_semantica.models import ConflictError
from conftest import seed_region


def test_preflight_alongside_owner_never_writes_or_acquires_lease(tmp_path):
    owner = GraphStore(tmp_path)
    try:
        register_corpus(owner, "papers")
        seed_region(owner, "alpha beta alpha")
        revision = owner.revision
        with CorpusSnapshot(tmp_path, corpus_id="papers", project_id=None) as reader:
            report = check_index(reader, corpus_id="papers")
            assert report["ready"] and report["tokens"] == 3 and report["regions"] == 1
        assert owner.revision == revision
        with pytest.raises(ConflictError): GraphStore(tmp_path)
    finally: owner.close()


def test_default_million_token_limit_and_supported_five_million_recovery():
    region = SimpleNamespace(id="source", content={"text": "x " * 1_000_001})
    with pytest.raises(ProjectionLimitError) as caught:
        GraphProjectionService(None)._lexical([region], 1_000_000)
    assert caught.value.diagnostic["configured"] == 1_000_000
    assert caught.value.diagnostic["observed"] == 1_000_001
    recovered = GraphProjectionService(None)._lexical([region], 5_000_000)
    assert recovered["token_count"] == 1_000_001
    assert recovered["terms"] == {"x": ["source"]}


def test_changed_budget_recovers_pipeline_without_preparation(tmp_path):
    from nima_semantica.source_tools import SourceToolContext, PrepareSourcesRequest, SourceInput, prepare_sources, embed_sources, project_sources
    store = GraphStore(tmp_path)
    try:
        register_corpus(store, "papers")
        context = SourceToolContext(corpus_id="papers", allow_corpus_writes=True, projection_limits={"max_tokens": 2})
        request = PrepareSourcesRequest(mode="prepare_index", operation_id="source", sources=(SourceInput(name="paper.txt", text="alpha beta gamma delta"),))
        prepared = prepare_sources(store, request, context)
        embedded = embed_sources(store, prepared, context)
        before = [(key, row.content) for key, row in store.records("SourceRegion")]
        failure = project_sources(store, embedded, context)
        assert not failure.data["index_ready"]
        assert failure.diagnostics[0]["code"] == "projection.token_limit"
        receipts = [(key, row.content) for key, row in store.records("ExecutionReceipt")]
        larger = context.model_copy(update={"projection_limits": {"max_tokens": 10}})
        assert prepare_sources(store, request, larger) == prepared
        success = project_sources(store, embedded, larger)
        assert success.data["index_ready"]
        assert [(key, row.content) for key, row in store.records("SourceRegion")] == before
        assert all(store.get(key).content == payload for key, payload in receipts)
    finally: store.close()


@pytest.mark.parametrize("damage,code", [("permission", "artifact.permission_denied"), ("missing", "artifact.missing"), ("tampered", "artifact.integrity_failure")])
def test_preflight_distinguishes_artifact_failure_without_private_paths(tmp_path, monkeypatch, damage, code):
    store = GraphStore(tmp_path)
    try:
        region = seed_region(store)
        artifact = region.content["artifact_id"]
        path = tmp_path / "artifacts" / artifact
        if damage == "permission":
            original = Path.read_bytes
            def read(selected):
                if selected == path: raise PermissionError("PRIVATE PATH AND SECRET")
                return original(selected)
            monkeypatch.setattr(Path, "read_bytes", read)
        elif damage == "missing": path.unlink()
        else: path.write_bytes(b"changed")
        result = check_index(store, corpus_id="papers")
        assert not result["ready"]
        assert result["diagnostics"][0]["code"] == code
        assert result["diagnostics"][0]["artifact_id"] == artifact
        assert "PRIVATE" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    finally: store.close()


def test_rebuild_retains_source_and_graph_identity_and_rejects_divergent_attempt(tmp_path):
    store = GraphStore(tmp_path)
    try:
        region = seed_region(store, "alpha beta gamma")
        revision = store.graph_revision("papers", None)
        result = rebuild_index(store, corpus_id="papers", limits={"max_tokens": 10}, attempt_id="fixed")
        assert result["index_ready"] and store.get(region.id) == region
        assert store.graph_revision("papers", None) == revision
        assert rebuild_index(store, corpus_id="papers", limits={"max_tokens": 10}, attempt_id="fixed")["manifest"] == result["manifest"]
        with pytest.raises(ConflictError): rebuild_index(store, corpus_id="papers", limits={"max_tokens": 20}, attempt_id="fixed")
    finally: store.close()


def test_preflight_excludes_foreign_project_regions(tmp_path):
    store = GraphStore(tmp_path)
    try:
        seed_region(store, "public")
        seed_region(store, "selected", project_id="p")
        seed_region(store, "foreign", project_id="other")
        assert check_index(store, corpus_id="papers", project_id="p")["regions"] == 2
        assert check_index(store, corpus_id="papers", project_id=None)["regions"] == 1
    finally: store.close()


def test_owner_recovery_forwards_limits_and_checks_exact_manifest(tmp_path, monkeypatch):
    from nima_semantica.installation import Installation, ModelProfile
    from nima_semantica import owner_operations, cli
    from nima_semantica.tool_contracts import ToolResult
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(provider="ollama", model="test"))
    owner = GraphStore(tmp_path / "corpus")
    try:
        seed_region(owner, "public alpha")
        monkeypatch.setattr(cli, "project_binding", lambda *a: {"mcp_url": "http://existing-owner/mcp"})
        captured = []
        def call(url, operation, request):
            captured.append((url, operation, request))
            from nima_semantica.source_tools import prepare_sources, PrepareSourcesRequest, SourceToolContext
            return prepare_sources(owner, PrepareSourcesRequest.model_validate(request), SourceToolContext(
                corpus_id="papers", project_id="p", allow_corpus_writes=True))
        monkeypatch.setattr(owner_operations, "call_owner", call)
        result = cli.index_through_owner(config, "papers", "p", ProjectionLimits(max_tokens=20, max_regions=2))
        assert result["index_ready"] and result["manifest"]["project_id"] == "p"
        assert captured[0][2]["projection_max_regions"] == 2
        assert captured[0][2]["sources"] == []
        monkeypatch.setattr(owner_operations, "call_owner", lambda *a: ToolResult(operation="Prepare and Index Sources",
            status="complete", data={**result, "project_id": "other"}))
        with pytest.raises(ConflictError, match="scope mismatch"):
            cli.index_through_owner(config, "papers", "p", ProjectionLimits(max_tokens=20, max_regions=2))
    finally: owner.close()


def test_owner_reads_fenced_native_results_and_rejects_divergent_delivery():
    from nima_semantica.owner_operations import native_result
    from nima_semantica.tool_contracts import ToolResult
    result = ToolResult(operation="Save Research Analysis", status="complete", data={"record_id": "proposal", "scientific_admission": False})
    raw = result.model_dump_json(indent=2)
    response = SimpleNamespace(isError=False, structuredContent=None,
        content=[SimpleNamespace(type="text", text="```json\n"+raw+"\n```"),
            SimpleNamespace(type="text", text="```json\n\n"+raw.replace("\n", "\n\n")+"\n\n```")])
    assert native_result(response, "Save Research Analysis") == result
    response.content.append(SimpleNamespace(type="text", text=raw.replace('"proposal"', '"changed"')))
    with pytest.raises(ValueError, match="unambiguous"):
        native_result(response, "Save Research Analysis")
