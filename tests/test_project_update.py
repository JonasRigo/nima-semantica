"""Exact approval, progress graph admission, atomicity and projection recovery."""
from asyncio import CancelledError
import pytest
from nima_semantica.project_update import UpdateProjectRequest,UpdateProjectContext,update_project_graph,PROGRESS_PROFILE
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.okf_contracts import OKFDelta,OKFNode
from nima_semantica.models import ConflictError
from test_source_pipeline import store
from test_claim_dependencies import seed,ref,request as tr,context as tc
from nima_semantica.claim_dependencies import trace_claim_dependencies


def context(**kw):return UpdateProjectContext(**({"corpus_id":"papers","project_id":"research","allow_graph_writes":True,"allow_audit_writes":True,"allow_projection_writes":True}|kw))
def delta(store,**kw):return OKFDelta(**({"delta_id":"update-1","corpus_id":"papers","project_id":"research",
    "base_revision":store.graph_revision("papers","research"),"ontology_profile":"claim_obligation","reason":"Reviewed claim",
    "upsert_nodes":(OKFNode(node_id="new",node_type="claim",corpus_id="papers",project_id="research"),)}|kw))
def request(store,**kw):return UpdateProjectRequest(**({"mode":"commit","operation_id":"update-1","graph_revision":store.graph_revision("papers","research"),"delta":delta(store)}|kw))
def approved(d,**kw):return context(approval=OKFCommitApproval.for_delta(d,approval_id="approve-1",approved_by="harness",rationale="Reviewed exact delta"),**kw)
def progress(store):
    seed(store);result=trace_claim_dependencies(store,tr(store),tc());return result.data["project_progress"]["record_id"]
def prepared_progress(store,**kw):
    pid=progress(store);req=request(store,mode="prepare",delta=None,progress_proposal_ids=(pid,),**kw)
    result=update_project_graph(store,req,context());assert result.status=="complete",result
    return req,OKFDelta.model_validate(result.data["delta"]),pid


def test_preview_prepare_no_writes_and_exact_commit_replay(store):
    before=store.revision;req=request(store)
    assert not update_project_graph(None,UpdateProjectRequest(),context()).data["executed"]
    preview=update_project_graph(store,req.model_copy(update={"mode":"prepare"}),context())
    assert preview.status=="complete" and not preview.data["approval_granted"] and store.revision==before
    ctx=approved(req.delta);out=update_project_graph(store,req,ctx)
    assert out.status=="complete" and out.data["committed"],out
    assert out.data["projection"]["manifest"]["graph_revision"]==out.data["commit"]["final_revision"]
    assert store.graph_revision("papers","research").project_revision==1
    assert store.graph_revision("papers").corpus_revision==req.graph_revision.corpus_revision
    revision=store.revision;assert update_project_graph(store,req,ctx)==out and store.revision==revision
    with pytest.raises(ConflictError):update_project_graph(store,req,ctx.model_copy(update={"actor":"other"}))


@pytest.mark.parametrize("flag",["allow_graph_writes","allow_audit_writes","approval"])
def test_denied_writes_have_no_side_effects(store,flag):
    req=request(store);ctx=approved(req.delta).model_copy(update={flag:None if flag=="approval" else False});before=store.revision
    assert update_project_graph(store,req,ctx).status=="failed" and store.revision==before


@pytest.mark.parametrize("payload",[{"approval":{}},{"allow_graph_writes":True},{"corpus_id":"foreign"},{"actor":"admin"},{"allow_projection_writes":True}])
def test_public_authority_rejected(payload):
    with pytest.raises(ValueError):UpdateProjectRequest.model_validate(payload)


@pytest.mark.parametrize("damage",["hash","actor","scope","revision","ontology","evidence"])
def test_invalid_approval_delta_rolls_back_and_records_failure(store,damage):
    req=request(store);ctx=approved(req.delta)
    if damage=="hash":ctx=ctx.model_copy(update={"approval":ctx.approval.model_copy(update={"delta_hash":"0"*64})})
    if damage=="actor":ctx=ctx.model_copy(update={"actor":"other"})
    if damage=="scope":ctx=ctx.model_copy(update={"project_id":"other"})
    if damage=="revision":seed(store)
    if damage=="ontology":req=request(store,delta=delta(store,ontology_profile="missing"));ctx=approved(req.delta)
    if damage=="evidence":
        from nima_semantica.okf_contracts import EvidenceReference
        e=EvidenceReference(corpus_id="papers",project_id="research",region_id="missing",artifact_id="0"*64,content_hash="0"*64,source_revision="missing")
        d=delta(store,upsert_nodes=(delta(store).upsert_nodes[0].model_copy(update={"evidence":(e,)}),));req=request(store,delta=d);ctx=approved(d)
    head=store.graph_revision("papers","research");out=update_project_graph(store,req,ctx)
    assert out.status=="failed" and not out.data["committed"] and store.graph_revision("papers","research")==head
    assert store.records("ProjectUpdateOutcome")


def test_progress_commits_real_history_without_promoting_science(store):
    req,d,pid=prepared_progress(store);original=store.get(pid);head=store.graph_revision("papers","research")
    out=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d))
    assert out.status=="complete",out
    assert store.get(pid)==original and original.content["project_recording"]["status"]=="pending"
    mappings=store.records("ProjectProgressCommit");assert len(mappings)==1
    assert mappings[0][1].content["commit_receipt_id"]==out.data["commit"]["receipt_id"]
    from nima_semantica.graph_service import GraphService
    from nima_semantica.ontology_services import OntologyService
    graph=GraphService(store,ontology=OntologyService.from_store(store,corpus_id="papers",project_id="research")).read_snapshot(corpus_id="papers",project_id="research")
    node=next(n for n in graph.nodes if n.node_type=="attempt")
    assert node.status.value=="observed" and not node.properties["scientific_acceptance"]
    assert len(node.provenance)==3 and node.properties["progress"]["expected_graph_revision"]==head.model_dump(mode="json")
    assert node.parents==(ref("a"),) and not node.properties["parent_proof_completed"]
    repeat=req.model_copy(update={"operation_id":"again","graph_revision":store.graph_revision("papers","research"),"record_historical_progress":True})
    assert update_project_graph(store,repeat,context()).status=="failed"


def test_historical_progress_needs_explicit_selection_and_fresh_approval(store):
    pid=progress(store);seed(store,(("a","b"),))
    req=request(store,mode="prepare",delta=None,progress_proposal_ids=(pid,))
    assert update_project_graph(store,req,context()).status=="failed"
    req=req.model_copy(update={"record_historical_progress":True})
    result=update_project_graph(store,req,context());assert result.status=="complete",result
    d=OKFDelta.model_validate(result.data["delta"])
    assert update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d)).status=="complete"


def test_projection_failure_does_not_misreport_committed_graph_and_can_rebuild(store,monkeypatch):
    from nima_semantica.graph_projection import GraphProjectionService
    original=GraphProjectionService.rebuild
    def fail(*a,**kw):raise ValueError("secret")
    monkeypatch.setattr(GraphProjectionService,"rebuild",fail)
    req=request(store);out=update_project_graph(store,req,approved(req.delta))
    assert out.status=="partial" and out.data["committed"] and "secret" not in out.model_dump_json()
    monkeypatch.setattr(GraphProjectionService,"rebuild",original)
    req=UpdateProjectRequest(mode="rebuild_projection",operation_id="repair",graph_revision=store.graph_revision("papers","research"))
    head=store.graph_revision("papers","research");out=update_project_graph(store,req,context(allow_graph_writes=False))
    assert out.status=="complete" and not out.data["committed"] and store.graph_revision("papers","research")==head


@pytest.mark.parametrize("where",["commit","projection"])
def test_cancelled_attempt_records_exact_commit_state(store,monkeypatch,where):
    from nima_semantica.graph_service import GraphService
    from nima_semantica.graph_projection import GraphProjectionService
    def cancel(*a,**kw):raise CancelledError()
    monkeypatch.setattr(GraphService if where=="commit" else GraphProjectionService,"commit_delta" if where=="commit" else "rebuild",cancel)
    req=request(store)
    with pytest.raises(CancelledError):update_project_graph(store,req,approved(req.delta))
    rows=store.records("ProjectUpdateOutcome");assert len(rows)==1
    assert rows[0][1].content["result"]["data"]["committed"]==(where=="projection")


def test_progress_mapping_failure_rolls_back_graph_and_ontology(store,monkeypatch):
    req,d,pid=prepared_progress(store);head=store.graph_revision("papers","research");put=store.put
    def fail(record):
        if record.kind=="ProjectProgressCommit":raise ValueError("mapping failed")
        return put(record)
    monkeypatch.setattr(store,"put",fail)
    out=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d))
    assert out.status=="failed" and store.graph_revision("papers","research")==head
    assert not store.records("ProjectProgressCommit") and not store.records("ProjectUpdateCommit")
    from nima_semantica.ontology_services import OntologyService
    assert PROGRESS_PROFILE.digest not in [p.digest for p in OntologyService.from_store(store,corpus_id="papers",project_id="research").list_profiles()]


def test_crash_after_commit_can_resume_without_duplicate_graph_change(store,monkeypatch):
    req=request(store);ctx=approved(req.delta);put=store.put
    def fail(record):
        if record.kind=="ProjectUpdateOutcome":raise OSError("lost final publication")
        return put(record)
    monkeypatch.setattr(store,"put",fail)
    with pytest.raises(OSError):update_project_graph(store,req,ctx)
    head=store.graph_revision("papers","research")
    monkeypatch.setattr(store,"put",put)
    result=update_project_graph(store,req,ctx)
    assert result.status=="complete" and result.data["committed"] and store.graph_revision("papers","research")==head


@pytest.mark.parametrize("tool",["counterexample","counterexample_failed","counterexample_cancelled","lean","extraction","analysis","substantiation"])
def test_real_tool_progress_outcomes_are_recordable(store,tool):
    seed(store)
    if tool.startswith("counterexample"):
        from test_counterexample_tool import request as cr,context as cc,Worker
        from nima_semantica.counterexample_tool import search_counterexamples
        error=CancelledError() if tool.endswith("cancelled") else ValueError("failure") if tool.endswith("failed") else None
        try:out=search_counterexamples(store,cr(graph_revision=store.graph_revision("papers","research")),cc(),worker=Worker(error=error))
        except CancelledError:
            pid=store.records("CounterexampleProgressProposal")[0][0];out=None
    elif tool=="lean":
        from test_verify_lean_tool import request as lr,context as lc,Verifier
        from nima_semantica.verify_lean_tool import verify_lean
        out=verify_lean(store,lr(),lc(),verifier=Verifier())
    elif tool=="extraction":
        from test_deep_extraction import region,request as er,context as ec,Model,actions
        from nima_semantica.deep_extraction_tool import deep_extraction
        r=region(store);out=deep_extraction(store,er(r),ec(),model=Model(actions(r)))
    elif tool=="analysis":
        from nima_semantica.graph_analysis import analyze_graph,AnalyzeGraphRequest,AnalyzeGraphContext
        out=analyze_graph(store,AnalyzeGraphRequest(mode="strict",operation_id="analysis",graph_revision=store.graph_revision("papers","research")),AnalyzeGraphContext(corpus_id="papers",project_id="research",allow_audit_writes=True))
    else:
        from test_substantiation import run
        out=run(store,None)
    if out:pid=out.data["project_progress"]["record_id"]
    selected=[pid]
    if tool=="counterexample":
        from test_counterexample_tool import encoding
        no_witness=search_counterexamples(store,cr(operation_id="no-witness",graph_revision=store.graph_revision("papers","research"),
            encoding=encoding(lower=0,upper=0)),cc(),worker=Worker())
        assert no_witness.data["result"]["outcome"]=="no_witness_in_examined_scope"
        selected.append(no_witness.data["project_progress"]["record_id"])
    req=request(store,mode="prepare",delta=None,progress_proposal_ids=tuple(selected))
    prep=update_project_graph(store,req,context());assert prep.status=="complete",prep
    d=OKFDelta.model_validate(prep.data["delta"])
    result=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d))
    assert result.status=="complete",result
    assert len(store.records("ProjectProgressCommit"))==len(selected)


def test_committed_history_cannot_be_deleted_by_raw_delta(store):
    req,d,pid=prepared_progress(store)
    result=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d));assert result.status=="complete"
    bad=delta(store,delta_id="delete",upsert_nodes=(),remove_node_ids=(d.upsert_nodes[0].ref,))
    result=update_project_graph(store,request(store,operation_id="delete",delta=bad),approved(bad))
    assert result.status=="failed"


def test_extraction_artifact_commit_and_explicit_substantiation_correction(store):
    from test_deep_extraction import region,request as er,context as ec,Model,actions
    from nima_semantica.deep_extraction_tool import deep_extraction
    r=region(store);ex=deep_extraction(store,er(r),ec(),model=Model(actions(r)))
    req=request(store,mode="prepare",delta=None,artifact_id=ex.artifacts["graph_proposal"])
    prep=update_project_graph(store,req,context());assert prep.status=="complete",prep
    d=OKFDelta.model_validate(prep.data["delta"])
    assert update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(d)).status=="complete"
    # A correction is authored by the harness, never inferred from an assessment label.
    from test_substantiation import request as sr,context as sc,actions as sa
    from nima_semantica.substantiation_tool import substantiate_graph_snapshot
    assessment=substantiate_graph_snapshot(store,sr(store,r,targets=[{"ref":ref("claim"),"finding":"Missing support"}]),sc(),model=Model(sa(r)))
    binding={"assessment_artifact_id":assessment.artifacts["assessment"],"target_indices":[0]}
    corrected=d.upsert_nodes[0].model_copy(update={"properties":{**d.upsert_nodes[0].properties,"review_note":"Missing-support assessment withdrawn; validity unresolved."}})
    change=delta(store,delta_id="correction",ontology_profile=d.ontology_profile,upsert_nodes=(corrected,),metadata={"substantiation_corrections":[binding]})
    req=request(store,operation_id="correction",delta=change,correction_bindings=(binding,))
    result=update_project_graph(store,req,approved(change));assert result.status=="complete",result
    assert result.data["delta_hash"]==approved(change).approval.delta_hash
    stale=change.model_copy(update={"delta_id":"stale-correction","base_revision":store.graph_revision("papers","research")})
    stale_req=request(store,operation_id="stale-correction",delta=stale,correction_bindings=(binding,))
    assert update_project_graph(store,stale_req,approved(stale)).status=="failed"


def test_exact_approval_cannot_authorize_cross_project_or_corpus_delta(store):
    for project in (None,"foreign"):
        d=OKFDelta(delta_id="foreign",corpus_id="papers",project_id=project,base_revision=store.graph_revision("papers",project),ontology_profile="claim_obligation",reason="outside scope")
        req=UpdateProjectRequest(mode="commit",operation_id="foreign-"+str(project),graph_revision=d.base_revision,delta=d)
        assert update_project_graph(store,req,approved(d)).status=="failed"


@pytest.mark.parametrize("damage",["receipts","target","artifact","status","revision"])
def test_tampered_progress_binding_rejected(store,damage):
    from nima_semantica.models import Record
    pid=progress(store);p=store.get(pid);content=dict(p.content)
    if damage=="receipts":content["receipt_ids"]=["missing"]
    if damage=="target":content["graph_target"]=ref("b").model_dump(mode="json")
    if damage=="artifact":content["artifact_id"]="0"*64
    if damage=="status":content["attempt_status"]="failed"
    if damage=="revision":content["expected_graph_revision"]=None
    forged=store.put(Record(kind=p.kind,corpus_id=p.corpus_id,project_id=p.project_id,content=content,parents=p.parents))
    req=request(store,mode="prepare",delta=None,progress_proposal_ids=(forged,))
    assert update_project_graph(store,req,context()).status=="failed"


def test_batch_failure_is_atomic_and_preparation_is_read_only(store):
    pid=progress(store);before=store.revision
    req=request(store,mode="prepare",delta=None,progress_proposal_ids=(pid,"missing"))
    assert update_project_graph(store,req,context()).status=="failed" and store.revision==before
    assert not store.records("ProjectProgressCommit")


def test_projection_requires_separate_permission(store):
    req=request(store);result=update_project_graph(store,req,approved(req.delta,allow_projection_writes=False))
    assert result.status=="partial" and result.data["committed"] and result.data["projection"]["status"]=="pending"


def test_approved_commit_joins_outer_transaction_rollback(store):
    from nima_semantica.graph_service import GraphService
    d=delta(store);before=store.graph_revision("papers","research")
    with pytest.raises(RuntimeError):
        with store.joined_transaction():
            GraphService(store).commit_delta(d,approved(d).approval)
            raise RuntimeError("outer failure")
    assert store.graph_revision("papers","research")==before and not store.records("GraphCommitReceipt")
