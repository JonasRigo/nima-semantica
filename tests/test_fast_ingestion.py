"""Fast indexed sources retain provenance across retrieval, upgrades and sharing."""
import json
import pytest

from nima_semantica.cli import main
from nima_semantica.installation import Installation, ModelProfile, write_json
from nima_semantica.source_tools import PrepareSourcesRequest
from nima_semantica.source_quality import PROVISIONAL, evidence_locator
from nima_semantica.source_corpus import SourceRegion
from nima_semantica.evidence_contracts import validate_reference, build_promotion_proposal, commit_promotion, PromotionApproval
from nima_semantica.okf_contracts import EvidenceReference, OKFNode
from nima_semantica.models import identity
from test_source_pipeline import store, pipeline, request, context, Provider
from test_research_retrieval import retrieve, QueryProvider, MANIFEST
from test_rewrite_audit_regressions import commit

TEXT = "The paper states a claim about Pauli operator propagation under explicit assumptions. " * 8


def fast_request(**kw):
    return request(preparation_mode="fast", sources=[{"name":"paper.md", "text":TEXT}], **kw)


def test_short_fast_source_reports_limit_and_retains_failure(store):
    from nima_semantica.source_tools import prepare_sources
    req = request(preparation_mode="fast", sources=[{"name":"short.md", "text":"Private short text"}])
    result = prepare_sources(store, req, context())
    assert result.status == "failed"
    assert result.diagnostics[0]["code"] == "sources.fast_text_insufficient"
    assert "200" in result.diagnostics[0]["message"]
    assert "Private" not in result.model_dump_json()
    assert result.receipt_ids and not store.records("SourceDescriptor")
    assert prepare_sources(store, req, context()) == result


def test_fast_source_searchable_labelled_and_filterable(store):
    prepared, _, result = pipeline(store, fast_request())
    assert result.data["index_ready"] and not prepared.data["normalization_complete"]
    found = retrieve(store, query="Pauli")
    assert found.status == "complete"
    assert found.data["regions"]
    for item in found.data["regions"]:
        assert item["preparation_quality"] == PROVISIONAL
        assert item["evidence"]["locator"]["preparation_quality"] == PROVISIONAL
    assert not retrieve(store, query="Pauli", include_provisional=False).data["regions"]
    assert not store.records("ResearchFastPassage")


def test_fast_vector_and_hybrid_preserve_quality(store):
    pipeline(store, fast_request(index_mode="vector"), provider=Provider())
    result = retrieve(store, query="Pauli", mode="hybrid", operation_id="hybrid-fast", provider=QueryProvider())
    assert result.status == "complete", result
    assert all(r["preparation_quality"] == PROVISIONAL for r in result.data["regions"])
    filtered = retrieve(store, query="Pauli", mode="vector", operation_id="vector-filter",
                        provider=QueryProvider(), include_provisional=False)
    assert filtered.status == "complete" and not filtered.data["regions"]


def test_full_upgrade_preserves_fast_regions_and_records_link(store):
    fast, _, _ = pipeline(store, fast_request())
    old = {key: store.get(key) for key in fast.data["region_ids"]}
    full, _, result = pipeline(store, request(operation_id="full-upgrade", sources=[{"name":"paper.md", "text":TEXT}]))
    assert result.data["index_ready"], result
    assert full.data["sources"][0]["preparation_quality"] == "full"
    assert full.data["sources"][0]["source_id"] != fast.data["sources"][0]["source_id"]
    assert all(store.get(key) == value for key, value in old.items())
    upgrade = store.records("SourcePreparationUpgrade")[0][1]
    assert upgrade.content["from_source_id"] == fast.data["sources"][0]["source_id"]
    assert not upgrade.content["graph_updated"]
    assert retrieve(store, query="Pauli", include_provisional=False).data["regions"]


def test_promotion_preserves_provisional_citations_and_status(store):
    prepared, _, _ = pipeline(store, fast_request())
    key = prepared.data["region_ids"][0]
    region = SourceRegion.model_validate(store.get(key).content)
    ref = EvidenceReference(corpus_id="papers", artifact_id=region.artifact_id, content_hash=region.artifact_id,
        region_id=key, source_revision=region.source_revision, locator=evidence_locator(region))
    assert not validate_reference(store, ref, corpus_id="papers", project_id="research", target_id="claim")
    stripped = ref.model_copy(update={"locator":{}})
    assert validate_reference(store, stripped, corpus_id="papers", project_id="research", target_id="claim")[0].code == "evidence.quality_label_missing"
    node = OKFNode(node_id="claim", node_type="claim", corpus_id="papers", project_id="research", evidence=(ref,))
    commit(store, "fast-claim", "research", upsert_nodes=(node,))
    snapshot = store.read_okf_snapshot(corpus_id="papers", project_id="research")
    proposal = build_promotion_proposal(snapshot, proposal_id="share-fast", target_corpus_id="papers", rationale="Share provisional evidence")
    result = commit_promotion(store, snapshot, proposal, PromotionApproval(proposal_id=proposal.proposal_id,
        proposal_hash=identity(proposal), approval_id="approved-share", approved_by="operator", rationale="Share without quality upgrade"))
    assert result.status == "committed"
    promoted = store.read_okf_snapshot(corpus_id="papers").nodes[0]
    assert promoted.status == node.status
    assert promoted.evidence[0].locator["preparation_quality"] == PROVISIONAL
    assert promoted.promotion_origin.source_project_id == "research"
    from nima_semantica.okf_mapping import snapshot_to_bundle, bundle_to_snapshot
    shared = store.read_okf_snapshot(corpus_id="papers")
    assert bundle_to_snapshot(snapshot_to_bundle(shared)) == shared


def test_cli_batch_continues_after_missing_file(tmp_path, capsys):
    config = tmp_path/"config.json"
    write_json(config, Installation(data_root=str(tmp_path/"data"), llm=ModelProfile(model="fixture")).model_dump())
    paper = tmp_path/"paper.md"; paper.write_text(TEXT)
    assert main(["--config",str(config),"ingest","papers",str(tmp_path/"missing.pdf"),str(paper),"--mode","fast"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["papers"][0]["status"] == "failed"
    assert result["papers"][1]["result"]["data"]["index_ready"]


def test_cli_arxiv_html_retains_acquisition_and_no_fallback(tmp_path, monkeypatch, capsys):
    from nima_semantica.storage import GraphStore
    config = tmp_path/"config.json"
    write_json(config, Installation(data_root=str(tmp_path/"data"), llm=ModelProfile(model="fixture")).model_dump())
    address = "https://arxiv.org/html/2501.12345v2"
    calls = []
    def acquire(url, policy):
        calls.append(url)
        return ("<html><article><h1>Title</h1><p>"+TEXT+"</p></article></html>").encode(), "2501.12345v2", {"resolved_url":url}
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", acquire)
    assert main(["--config",str(config),"ingest","papers",address,"--mode","fast","--format","html"]) == 0
    capsys.readouterr()
    assert calls == [address]
    with_store = GraphStore(tmp_path/"data/corpus")
    try:
        descriptor = with_store.records("SourceDescriptor")[0][1].content
        provenance = json.loads(with_store.read_artifact(descriptor["metadata"]["provenance_artifact_id"]))
        assert provenance["url"] == address and provenance["metadata"]["resolved_url"] == address
    finally:
        with_store.close()


def test_full_html_preserves_structure_math_and_unresolved_diagnostics():
    from nima_semantica.corpus import normalize
    text, diagnostics = normalize(b'<html><article><h2 id="s1">Results</h2><math alttext="x^2"><mi>x</mi></math><a href="#ref1">Reference</a><img src="figure.png"/></article></html>', 'paper.html')
    assert '##' in text and 'Results' in text and r'\[x^2\]' in text and '[Reference](#ref1)' in text
    assert any(d.get("element_id") == "s1" for d in diagnostics)
    assert any(d.get("kind") == "html_image" and d["status"] == "unresolved" for d in diagnostics)


def test_fast_pdf_does_not_call_advanced_parser_and_preserves_method(store, monkeypatch):
    import base64
    from nima_semantica.source_tools import prepare_sources
    from test_source_pipeline import context
    monkeypatch.setattr("nima_semantica.deep_research_fast.fast_text", lambda *args: (TEXT, "fixture-text-layer"))
    req = request(preparation_mode="fast", sources=[{"name":"paper.pdf",
        "data_base64":base64.b64encode(b"%PDF-test").decode()}])
    result = prepare_sources(store, req, context(allow_pdf=True),
                             pdf_normalizer=lambda _: pytest.fail("advanced parser must not run"))
    assert result.status == "complete"
    region = SourceRegion.model_validate(store.get(result.data["region_ids"][0]).content)
    assert evidence_locator(region)["extraction_method"] == "fixture-text-layer"
    assert region.metadata["preparation_quality"] == PROVISIONAL


def test_fast_failure_never_falls_back_to_full(store, monkeypatch):
    from nima_semantica.source_tools import prepare_sources
    def unavailable(*args):
        raise ValueError("text layer unavailable")
    monkeypatch.setattr("nima_semantica.deep_research_fast.fast_text", unavailable)
    from test_source_pipeline import context
    result = prepare_sources(store, fast_request(), context())
    assert result.status == "failed" and not store.records("SourceRegion")


def test_derived_claim_cannot_drop_preparation_warning(store):
    from nima_semantica.evidence_contracts import validate_delta_evidence
    from nima_semantica.okf_contracts import OKFDelta
    parent = OKFNode(node_id="parent", node_type="claim", corpus_id="papers", project_id="research",
                    properties={"preparation_quality":PROVISIONAL})
    child = OKFNode(node_id="child", node_type="claim", corpus_id="papers", project_id="research", parents=(parent.ref,))
    delta = OKFDelta(delta_id="derived", corpus_id="papers", project_id="research",
        base_revision=store.graph_revision("papers","research"), upsert_nodes=(parent,child), reason="Test provenance propagation")
    assert not validate_delta_evidence(store,delta).valid
    labelled = child.model_copy(update={"properties":{"preparation_quality":PROVISIONAL}})
    assert validate_delta_evidence(store,delta.model_copy(update={"upsert_nodes":(parent,labelled)})).valid


def test_cli_rejects_format_mismatch_and_disallowed_url_before_fetch(tmp_path, monkeypatch, capsys):
    config = tmp_path/"config.json"
    write_json(config, Installation(data_root=str(tmp_path/"data"), llm=ModelProfile(model="fixture")).model_dump())
    paper = tmp_path/"paper.md"; paper.write_text(TEXT)
    assert main(["--config",str(config),"ingest","papers",str(paper),"--format","pdf"]) == 2
    monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", lambda *args: pytest.fail("not authorized"))
    assert main(["--config",str(config),"ingest","papers","https://evil.example/paper.html"]) == 2


def test_extraction_retains_quality_in_every_evidence_reference(store):
    from nima_semantica.graph_extraction import GraphExtractionRequest, GraphExtractionService, GraphExtractionCandidate, ExtractedNode
    prepared, _, _ = pipeline(store, fast_request())
    key = prepared.data["region_ids"][0]
    req = GraphExtractionRequest(corpus_id="papers", project_id="research",
        graph_revision=store.graph_revision("papers","research"), ontology_profile="claim_obligation",
        registry_revision=prepared.data["sources"][0]["registry_revision"], source_region_ids=(key,), question="What is claimed?")
    candidate = GraphExtractionCandidate(nodes=(ExtractedNode(node_id="claim",node_type="claim",source_region_ids=(key,)),))
    artifact = GraphExtractionService(store).prepare_candidate(req,candidate)
    assert artifact.delta.upsert_nodes[0].evidence[0].locator["preparation_quality"] == PROVISIONAL


def test_cli_promotion_requires_exact_approval_and_refreshes_projects(tmp_path, capsys):
    from nima_semantica.storage import GraphStore
    config = tmp_path/"config.json"
    data = tmp_path/"data"
    write_json(config, Installation(data_root=str(data), llm=ModelProfile(model="fixture")).model_dump())
    prefix = ["--config",str(config)]
    assert main(prefix+["project","init","research","--corpus","papers","--path",str(tmp_path/"work"),"--offline"]) == 0
    capsys.readouterr()
    local = GraphStore(data/"corpus")
    try:
        prepared, _, _ = pipeline(local,fast_request())
        key = prepared.data["region_ids"][0]
        region = SourceRegion.model_validate(local.get(key).content)
        ref = EvidenceReference(corpus_id="papers",artifact_id=region.artifact_id,content_hash=region.artifact_id,
            region_id=key,source_revision=region.source_revision,locator=evidence_locator(region))
        commit(local,"seed","research",upsert_nodes=(OKFNode(node_id="claim",node_type="claim",corpus_id="papers",
            project_id="research",evidence=(ref,)),))
    finally:
        local.close()
    command = prefix+["project","promote","research","--corpus","papers","--rationale","Share provisional findings"]
    assert main(command) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not preview["committed"]
    assert main(command+["--approve-proposal","wrong","--approved-by","operator"]) == 2
    capsys.readouterr()
    assert main(command+["--approve-proposal",preview["proposal_hash"],"--approved-by","operator"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["committed"] and result["projection_ready"], result
