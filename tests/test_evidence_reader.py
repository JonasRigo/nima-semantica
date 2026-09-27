"""Exact artifact/region reads and sanitized provenance, without model calls."""
import base64
import hashlib
import pytest
from nima_semantica.evidence_reader import ReadEvidenceRequest, ReadEvidenceContext, read_evidence
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.registry_contracts import RegistryRevision
from nima_semantica.models import Record
from test_source_pipeline import store, pipeline, request


def read(store, project="research", **kwargs):
    return read_evidence(store,ReadEvidenceRequest(**kwargs),ReadEvidenceContext(corpus_id="papers",project_id=project))


def publish(store, data, *, project=None, sources=(), provenance=(), status="available", media="text/plain"):
    digest=hashlib.sha256(data).hexdigest()
    registry=CorpusRegistry(store)
    revisions=[RegistryRevision.model_validate(r.content) for _,r in store.records(registry.REVISION_KIND,corpus_id="papers")]
    parent=max(revisions,key=lambda r:r.sequence) if revisions else None
    revision=RegistryRevision(revision_id=digest,corpus_id="papers",sequence=parent.sequence+1 if parent else 0,
        parent_revision=parent.revision_id if parent else None,changed_resource_ids=(digest,))
    registry.register_revision(revision)
    ArtifactService(store).publish(data,ArtifactEnvelope(artifact_id=digest,content_hash=digest,artifact_kind="test_evidence",
        media_type=media,corpus_id="papers",project_id=project,source_artifact_ids=sources,provenance=provenance,status=status),registry_revision=revision.revision_id)
    return digest


@pytest.mark.parametrize("payload", [{}, {"artifact_id":"../secret"}, {"region_id":"r","artifact_id":"0"*64},
    {"region_id":"r","reference":{}}, {"region_id":"r","encoding":"base64"},
    {"artifact_id":"0"*64,"encoding":"base64","render":"escaped_html"},
    {"region_id":"r","max_bytes":0}, {"region_id":"r","max_bytes":True},
    {"region_id":"r","max_links":129}, {"region_id":"r","corpus_id":"foreign"},
    {"region_id":"r","project_id":"foreign"}, {"region_id":"r","allow_writes":True},
    {"region_id":"r","max_bytes":5000001}])
def test_invalid_request(payload):
    with pytest.raises(ValueError):
        ReadEvidenceRequest.model_validate(payload)


def test_unconfigured_and_unregistered_bytes(store):
    assert read(None,artifact_id="0"*64).status == "unavailable"
    digest=store.artifact(b"Not published")
    assert read(store,artifact_id=digest).status == "failed"


def test_region_roundtrip_reference_and_read_only(store,monkeypatch):
    text="Definition Ω: $x \\neq 0$.\nTherefore $x/x=1$."
    prepared,_,_=pipeline(store,request(sources=[{"name":"math.tex","text":text}]))
    region_id=prepared.data["region_ids"][0]
    before=(store.revision,store.records())
    def forbidden(*args,**kwargs):
        raise AssertionError("read evidence must not write")
    monkeypatch.setattr(store,"put",forbidden);monkeypatch.setattr(store,"artifact",forbidden)
    result=read(store,region_id=region_id)
    assert result.status == "complete",result
    assert result.data["content"] == text and result.data["bytes"] == len(text.encode())
    assert result.data["exact_source_checked"] and result.data["content_complete"]
    assert read(store,reference=result.data["reference"]).data == result.data
    assert not result.receipt_ids and not result.artifacts
    assert (store.revision,store.records()) == before


def test_artifact_exact_encoding_and_safe_render(store):
    content='<script>alert("bad")</script>\n```\n$α=1$ & <x>'
    digest=publish(store,content.encode(),media="text/html")
    result=read(store,artifact_id=digest,render="escaped_html")
    assert result.status == "complete"
    assert result.data["content"] == content
    assert "<script>" not in result.data["rendered"]["content"]
    assert "&lt;script&gt;" in result.data["rendered"]["content"]
    assert not result.data["exact_source_checked"]
    encoded=read(store,artifact_id=digest,encoding="base64")
    assert base64.b64decode(encoded.data["content"]) == content.encode()


def test_binary_pdf_bytes_are_not_parsed_or_executed(store):
    content=b"%PDF-1.7\n\xff\x00binary"
    digest=publish(store,content,media="application/pdf")
    assert read(store,artifact_id=digest).status == "failed"
    result=read(store,artifact_id=digest,encoding="base64")
    assert result.status == "complete" and base64.b64decode(result.data["content"]) == content


def test_limits_and_revision_pins(store):
    prepared,_,_=pipeline(store)
    region_id=prepared.data["region_ids"][0]
    result=read(store,region_id=region_id)
    assert read(store,region_id=region_id,max_bytes=1).diagnostics[0]["code"] == "evidence.read_limit_exceeded"
    assert read(store,region_id=region_id,expected_store_revision="sqlite:0").status == "failed"
    assert read(store,region_id=region_id,expected_source_revision="wrong").status == "failed"
    assert read(store,region_id=region_id,expected_source_revision=result.data["source_revision"],expected_store_revision=store.revision).status == "complete"


@pytest.mark.parametrize("change", [{"quotation":"Invented quote"}, {"locator":{"start":999}},
    {"project_id":"private"}, {"corpus_id":"other"}, {"source_revision":"wrong"}])
def test_supplied_reference_must_match(store,change):
    prepared,_,_=pipeline(store)
    reference=read(store,region_id=prepared.data["region_ids"][0]).data["reference"]
    result=read(store,reference=reference|change)
    assert result.status == "failed" and not result.data


def test_scope_guards_and_hidden_provenance(store):
    private=publish(store,b"PRIVATE CONTENT",project="private")
    assert read(store,project=None,artifact_id=private).status == "failed"
    assert read(store,artifact_id=private).status == "failed"
    assert read(store,project="private",artifact_id=private).data["content"] == "PRIVATE CONTENT"
    hidden=store.put(Record(kind="PrivateNote",corpus_id="papers",project_id="private",content={"text":"secret"}))
    shared=publish(store,b"Shared content",sources=(private,),provenance=(hidden,))
    result=read(store,artifact_id=shared)
    assert result.status == "partial" and result.data["content_complete"]
    assert result.data["provenance"]["unresolved_count"] == 2
    assert private not in result.model_dump_json() and hidden not in result.model_dump_json()


def test_provenance_bounds_and_failed_artifact_status(store):
    key=store.put(Record(kind="Check",corpus_id="papers",content={"result":"failed"}))
    digest=publish(store,b"Negative result",provenance=(key,),status="failed")
    result=read(store,artifact_id=digest)
    assert result.status == "complete" and result.data["artifact_status"] == "failed"
    assert result.data["provenance"]["links"] == [{"kind":"provenance","id":key}]
    bounded=read(store,artifact_id=digest,max_links=0)
    assert bounded.status == "partial" and bounded.data["provenance"]["omitted_count"] == 1


def test_corrupt_bytes_fail_without_content(store,monkeypatch):
    digest=publish(store,b"Actual source")
    monkeypatch.setattr(store,"read_artifact",lambda key:b"corrupted bytes")
    result=read(store,artifact_id=digest)
    assert result.status == "failed" and not result.data


def test_private_region_and_foreign_corpus_denied(store):
    from nima_semantica.source_corpus import SourceRegion, SourceCorpusService
    prepared,_,_=pipeline(store)
    record=store.get(prepared.data["region_ids"][0])
    region=SourceRegion.model_validate(record.content).model_copy(update={"project_id":"private"})
    private=SourceCorpusService(store).register_region(region)
    assert read(store,region_id=private).status == "failed"
    assert read(store,project=None,region_id=private).status == "failed"
    assert read(store,project="private",region_id=private).status == "complete"
    result=read_evidence(store,ReadEvidenceRequest(artifact_id=region.artifact_id),ReadEvidenceContext(corpus_id="other"))
    assert result.status == "failed" and not result.data


def test_unregistered_region_artifact_cannot_bypass_registry(store):
    from conftest import seed_region
    region=seed_region(store,"Unpublished exact source bytes")
    assert read(store,region_id=region.id).status == "failed"
