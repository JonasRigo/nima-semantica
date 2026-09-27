"""Deterministic report boundary: no paid providers."""
import json
from asyncio import CancelledError
import pytest
from nima_semantica.research_analysis import *
from nima_semantica.storage import GraphStore
from nima_semantica.evidence_reader import read_evidence,ReadEvidenceRequest,ReadEvidenceContext
from test_source_pipeline import store

def context(**kw):
    return SaveAnalysisContext(**({"corpus_id":"papers","project_id":"research","allow_artifact_writes":True}|kw))

def request(store,**kw):
    return SaveAnalysisRequest(**({"mode":"save","operation_id":"save-1",
        "graph_revision":store.graph_revision("papers","research"),
        "bundle":ResearchAnalysisBundle(title="Research <untrusted>",sections=(AnalysisSection(section_id="summary",category="summary",text="A claim, not a verified theorem."),),limitations=("No scientific validation.",))}|kw))

def test_preview_denied_and_public_authority(store):
    before=store.revision
    assert not save_research_analysis(None,SaveAnalysisRequest(),context()).data["executed"]
    assert save_research_analysis(store,request(store),context(allow_artifact_writes=False)).status=="failed"
    assert before==store.revision
    for key in ("project_id","corpus_id","allow_artifact_writes","output_path"):
        with pytest.raises(ValueError):SaveAnalysisRequest.model_validate({key:True})

def test_save_read_replay_and_no_scientific_mutation(store):
    req=request(store);revision=req.graph_revision
    result=save_research_analysis(store,req,context())
    assert result.status=="complete",result
    assert set(result.artifacts)=={"bundle","markdown","outcome"}
    env,raw=ArtifactService(store).read(result.artifacts["bundle"],corpus_id="papers",project_id="research")
    assert json.loads(raw)["bundle"]==req.bundle.model_dump(mode="json")
    for aid in result.artifacts.values():
        read=read_evidence(store,ReadEvidenceRequest(artifact_id=aid),ReadEvidenceContext(corpus_id="papers",project_id="research"))
        assert read.status=="complete",read
    assert store.graph_revision("papers","research")==revision
    assert not store.records("OKFNode")
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    before=store.revision
    assert save_research_analysis(store,req,context())==result
    assert store.revision==before
    with pytest.raises(ConflictError):
        save_research_analysis(store,req.model_copy(update={"bundle":req.bundle.model_copy(update={"title":"changed"})}),context())

@pytest.mark.parametrize("field,value",[("artifact_ids",("a"*64,)),("receipt_ids",("missing",))])
def test_unknown_references_fail_with_recorded_attempt(store,field,value):
    req=request(store);req=req.model_copy(update={"bundle":req.bundle.model_copy(update={field:value})})
    result=save_research_analysis(store,req,context())
    assert result.status=="failed"
    assert set(result.artifacts)=={"outcome"}
    assert not store.records("ResearchAnalysisBundle")
    assert store.records("ResearchAnalysisProgressProposal")

def test_foreign_artifact_and_receipt_rejected(store):
    first=save_research_analysis(store,request(store),context())
    for field,value in (("artifact_ids",(first.artifacts["bundle"],)),("receipt_ids",first.receipt_ids)):
        req=request(store,operation_id=field,graph_revision=store.graph_revision("papers","foreign"))
        req=req.model_copy(update={"bundle":req.bundle.model_copy(update={field:value})})
        result=save_research_analysis(store,req,context(project_id="foreign"))
        assert result.status=="failed"

@pytest.mark.parametrize("cancel",[False,True])
def test_atomic_publication_failure_and_cancellation(store,monkeypatch,cancel):
    original=ArtifactService.publish
    def fail(self,blob,envelope,**kwargs):
        if envelope.artifact_kind=="research_analysis_markdown":
            if cancel:raise CancelledError()
            raise RuntimeError("publication failed")
        return original(self,blob,envelope,**kwargs)
    monkeypatch.setattr(ArtifactService,"publish",fail)
    if cancel:
        with pytest.raises(CancelledError):save_research_analysis(store,request(store),context())
    else:assert save_research_analysis(store,request(store),context()).status=="failed"
    assert not store.records("ResearchAnalysisBundle")
    assert store.records("ResearchAnalysisOutcome")
    receipts=[r for _,r in store.records("ExecutionReceipt") if r.content.get("stage")=="save_research_analysis"]
    assert len(receipts)==1
    assert receipts[0].content["status"]==("interrupted" if cancel else "failed")

def test_real_review_and_failed_history_can_be_saved(store):
    from test_review_tool import run
    from test_claim_dependencies import seed
    seed(store)
    for op,seq in (("submitted",None),("failed",[{"name":"invalid","arguments":{}}])):
        from test_review_tool import request as review_request
        review=run(store,seq=seq,req=review_request(store,operation_id=op))
        req=request(store,operation_id="save-"+op)
        req=req.model_copy(update={"bundle":req.bundle.model_copy(update={"artifact_ids":tuple(review.artifacts.values()),"receipt_ids":review.receipt_ids})})
        result=save_research_analysis(store,req,context())
        assert result.status=="complete",result
        assert result.data["review_publication_checks"][0]["publishable_assessments"]==(op=="submitted")

def test_bundle_bounds():
    with pytest.raises(ValueError):ResearchAnalysisBundle(title="x",limitations=("y",))
    with pytest.raises(ValueError):ResearchAnalysisBundle(title="x",limitations=("y",),sections=(AnalysisSection(section_id="x",category="claim",text="x",evidence_indices=(0,)),))

def test_separately_approved_progress(store):
    from test_project_update import request as ur,context as uc,approved
    from nima_semantica.project_update import update_project_graph
    from nima_semantica.okf_contracts import OKFDelta
    result=save_research_analysis(store,request(store),context())
    req=ur(store,mode="prepare",delta=None,progress_proposal_ids=(result.data["project_progress"]["record_id"],))
    prep=update_project_graph(store,req,uc())
    assert prep.status=="complete",prep
    assert not store.records("ProjectProgressCommit")
    commit=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(OKFDelta.model_validate(prep.data["delta"])))
    assert commit.status=="complete" and store.records("ProjectProgressCommit")

@pytest.mark.parametrize("problem",["revision","run","target","evidence","size"])
def test_invalid_bindings_and_bounds(store,problem):
    req=request(store)
    if problem=="revision":req=req.model_copy(update={"graph_revision":store.graph_revision("papers","foreign")})
    elif problem=="run":req=req.model_copy(update={"run_id":"unknown"})
    elif problem=="target":req=req.model_copy(update={"target_record_id":"unknown"})
    elif problem=="evidence":
        evidence=EvidenceReference(corpus_id="papers",project_id="research",artifact_id="b"*64,content_hash="b"*64,source_revision="missing",region_id="missing")
        req=req.model_copy(update={"bundle":req.bundle.model_copy(update={"evidence":(evidence,)})})
    result=save_research_analysis(store,req,context(max_bytes=1000) if problem=="size" else context())
    assert result.status=="failed",result
    assert not store.records("ResearchAnalysisBundle")

def test_exact_evidence_is_preserved(store):
    from test_deep_extraction import region
    r=region(store)
    evidence=EvidenceReference(corpus_id="papers",artifact_id=r.content["artifact_id"],content_hash=r.content["artifact_id"],
        region_id=r.id,source_revision=r.content["source_revision"],quotation=r.content["text"])
    req=request(store)
    req=req.model_copy(update={"bundle":req.bundle.model_copy(update={"evidence":(evidence,)})})
    result=save_research_analysis(store,req,context())
    assert result.status=="complete",result

@pytest.mark.parametrize("defect",[None,"missing","finding","target","revision","content","support","receipt","status"])
def test_adverse_publication_gate(store,monkeypatch,defect):
    # Synthetic stored-receipt double isolates publication rechecks, not model quality.
    from types import SimpleNamespace
    from test_review_tool import request as rr
    req=rr(store);target=req.targets[0];scope=dict(corpus_id="papers",project_id="research")
    selected={"ref":target.ref.model_dump(mode="json"),"finding":"Support not located"}
    nested_result={"receipt_ids":["nested"],"data":{"result":{"assessments":[{"target":selected.copy(),"snapshot_hash":"snapshot","source_substantiation":"support_not_located"}]}}}
    nested={"operation_id":"nested","target_id":target.target_id,"content_revision":target.content_revision,"status":"partial",
        "request":{"graph_revision":req.graph_revision.model_dump(mode="json"),"targets":[selected.copy()]},"result":nested_result}
    assessment={"target_id":target.target_id,"content_revision":target.content_revision,"assessment":"concerns",
        "missing_support_finding":"Support not located","substantiation_operation_id":"nested"}
    result={"finalized":True,"targets":[target.model_dump(mode="json")],"assessments":[assessment],"substantiations":[nested],"snapshot_hash":"snapshot"}
    if defect=="missing":result["substantiations"]=[]
    if defect=="finding":nested["request"]["targets"][0]["finding"]="different"
    if defect=="target":nested["target_id"]="different"
    if defect=="revision":nested["request"]["graph_revision"]={}
    if defect=="content":nested["content_revision"]="different"
    if defect=="support":nested_result["data"]["result"]["assessments"][0]["source_substantiation"]="support_located"
    if defect=="status":nested["status"]="failed"
    captured={"request":req.model_dump(mode="json"),"data":{"result":result},"status":"partial",**scope}
    main=SimpleNamespace(output_ids=("a"*64,),graph_revision=req.graph_revision,status="partial",metadata={"result":{"data":{"result":result}}})
    child=SimpleNamespace(graph_revision=req.graph_revision,status="partial",metadata={"result":nested_result})
    mid=identity({"stage":"review_research","operation_id":req.operation_id,**scope})
    monkeypatch.setattr(ExecutionReceiptService,"get",lambda self,rid,**kw:main if rid==mid else None if defect=="receipt" else child)
    call=lambda:review_publication_check(store,"a"*64,SimpleNamespace(artifact_kind="review_assessment"),captured,req.graph_revision,scope)
    if defect:
        with pytest.raises(ConflictError):call()
    else:assert call()["publishable_assessments"]
