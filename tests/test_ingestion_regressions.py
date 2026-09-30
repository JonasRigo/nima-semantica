"""Large structural regions, safe errors and retry/progress regressions."""
import json

import httpx
import pytest

from nima_semantica.corpus import regions
from nima_semantica.source_tools import prepare_sources, embed_sources
from test_source_pipeline import store, context, request, pipeline, Provider, MANIFEST


def test_validation_diagnostics_never_echo_inputs():
    from nima_semantica.ingestion_diagnostics import diagnostic
    from nima_semantica.okf_contracts import EvidenceReference
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc:
        EvidenceReference(quotation="secret" * 10000)
    result = diagnostic(exc.value)
    assert "secret" not in json.dumps(result)
    assert {"field": "quotation", "rule": "string_too_long"} in result["fields"]


@pytest.mark.parametrize("text", ["αβ " * 18000, "x" * 51000, "paragraph\n" + " " * 25000])
def test_hard_bounds_preserve_exact_coverage(text, capsys):
    result = regions(text, "a" * 64, "b" * 64, "papers")
    assert "".join(r.content["text"] for r in result) == text
    assert max(len(r.content["text"]) for r in result) <= 1800
    assert all(text[r.content["start"]:r.content["end"]] == r.content["text"] for r in result)
    assert [r.content["ordinal"] for r in result] == list(range(len(result)))
    assert "0.0%" not in capsys.readouterr().out


def test_legacy_oversized_regions_can_publish_without_quotation_copy(store, monkeypatch):
    import nima_semantica.document_ingestion as ingestion
    from nima_semantica.models import Record
    def legacy(text, artifact, document, corpus, **kwargs):
        return [Record(kind="SourceRegion", corpus_id=corpus, content={
            "start": 0, "end": len(text), "ordinal": 0, "text": text})]
    monkeypatch.setattr(ingestion, "regions", legacy)
    prepared, _, result = pipeline(store, request(index_mode="vector",
        sources=[{"name": "long.md", "text": "large paragraph " * 4000}]), provider=Provider())
    assert result.data["index_ready"], result
    assert len(store.get(prepared.data["region_ids"][0]).content["text"]) > 20000


def test_provider_failure_is_actionable_safe_and_persisted(store):
    class Broken:
        def embed(self, *args):
            req = httpx.Request("POST", "http://localhost/secret-token")
            raise httpx.HTTPStatusError("secret-token", request=req,
                response=httpx.Response(413, request=req, text="private paper text"))
    prepared = prepare_sources(store, request(index_mode="vector"), context())
    result = embed_sources(store, prepared, context(), provider=Broken(), manifest=MANIFEST)
    assert result.data["failed_stage"] == "source_embeddings"
    assert result.diagnostics[0]["http_status"] == 413
    assert "--retry" in result.data["retry_hint"]
    serialized = result.model_dump_json()
    assert "secret-token" not in serialized and "private paper text" not in serialized
    assert not store.records("EmbeddingBatch")
    saved = [r for _,r in store.records("ExecutionReceipt") if r.content["stage"] == "source_embeddings"]
    assert saved[0].content["diagnostics"][0]["http_status"] == 413


def test_cli_retry_new_attempt_clean_json_and_stage_progress(tmp_path, capsys):
    from nima_semantica.cli import main
    from nima_semantica.installation import Installation, ModelProfile, write_json
    config = tmp_path / "config.json"
    write_json(config, Installation(data_root=str(tmp_path / "data"), llm=ModelProfile(model="fixture")).model_dump())
    paper = tmp_path / "paper.md"
    paper.write_text("Evidence sentence. " * 300)
    args = ["--config", str(config), "ingest", "papers", str(paper), "--mode", "fast"]
    assert main(args) == 0
    first = capsys.readouterr()
    original = json.loads(first.out)
    assert "Preparation:" in first.err and "index_ready=True" in first.err
    assert "0.0%" not in first.out + first.err
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == original
    assert main(args + ["--retry"]) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["data"]["operation_id"] != original["data"]["operation_id"]
    assert retried["data"]["region_ids"] == original["data"]["region_ids"]
