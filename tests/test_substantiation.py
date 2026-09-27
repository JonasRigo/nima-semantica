"""Offline evidence-seeking OSA acceptance; scripted judgments are not accuracy scores."""
from asyncio import CancelledError
import copy
import pytest
from nima_semantica.substantiation_contracts import SubstantiationRequest,SubstantiationContext
from nima_semantica.substantiation_tool import substantiate_graph_snapshot
from nima_semantica.models import ConflictError
from test_source_pipeline import store
from test_claim_dependencies import seed,ref
from test_deep_extraction import region,Model
from model_fixture import MANIFEST


def context(**kw):return SubstantiationContext(**({"corpus_id":"papers","project_id":"research","allow_audit_writes":True,"allow_model_calls":True,"model_manifest":MANIFEST}|kw))
def request(store,r=None,**kw):return SubstantiationRequest(**({"mode":"substantiate","operation_id":"substantiate-1",
    "graph_revision":store.graph_revision("papers","research"),"targets":[{"ref":ref("a"),"finding":"The claim appears unsupported."}],
    "source_region_ids":(r.id,) if r else ()}|kw))
def assessment(r=None,**kw):return {"name":"assess_target","arguments":{"target_index":0,"extraction_fidelity":"unresolved",
    "source_substantiation":"support_located" if r else "unresolved","recommendation":"withdraw" if r else "unresolved",
    "supporting":[{"source_id":r.id,"quotation":r.content["text"]}] if r else [],
    "justification":"The earlier lemma supplies the omitted premise." if r else "Evidence is not yet available.",
    "justification_steps":[{"explanation":"Apply the stated premise to the selected claim; applicability requires independent checking.","anchors":[{"source_id":r.id,"quotation":r.content["text"]}]}] if r else [],
    "proposed_correction":"Withdraw the missing-support finding." if r else "",
    "verification_obligations":["Independently check applicability and mathematical validity."],
    "coverage_limitations":["Only selected and retrieved passages were examined."],**kw}}
def actions(r=None):return ([{"name":"read_regions","arguments":{"region_ids":[r.id]}}] if r else [])+[assessment(r),
    {"name":"analyze_dependencies","arguments":{}},{"name":"submit_result","arguments":{}}]
def run(store,r,seq=None,**kw):return substantiate_graph_snapshot(store,request(store,r),context(**kw),model=Model(seq or actions(r)))


def test_subtle_support_correction_preserves_original_and_private_state(store):
    seed(store);r=region(store,"By the previous lemma the integrand is bounded, so dominated convergence applies.")
    head=store.graph_revision("papers","research");req=request(store,r);m=Model(actions(r))
    result=substantiate_graph_snapshot(store,req,context(),model=m)
    assert result.status=="partial",result
    out=result.data["result"];a=out["assessments"][0]
    assert a["recommendation"]=="withdraw" and a["source_substantiation"]=="support_located"
    assert a["grounding"][0]["quotation"]==r.content["text"] and a["grounding"][0]["source_revision"]
    assert not out["source_fidelity_verified"] and a["mathematical_validity"]=="not_verified"
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert "assessment_0" in result.data["reasoning_state"]["consequences"]["Open"]
    assert not result.data["reasoning_state"]["publishable"]
    assert store.graph_revision("papers","research")==head
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    assert result.data["project_progress"]["graph_target"]==[t.model_dump(mode="json") for t in req.targets]
    from nima_semantica.artifact_service import ArtifactService
    assert ArtifactService(store).resolve(result.artifacts["assessment"],corpus_id="papers",project_id="research").artifact_kind=="substantiation_assessment"
    assert substantiate_graph_snapshot(store,req,context(),model=m)==result and len(m.calls)==4
    with pytest.raises(ConflictError):substantiate_graph_snapshot(store,req,context(max_actions=21),model=m)


def test_preview_and_permissions(store):
    before=store.revision
    assert not substantiate_graph_snapshot(None,SubstantiationRequest(),context()).data["executed"]
    for key in ("allow_model_calls","allow_audit_writes"):
        m=Model([]);assert substantiate_graph_snapshot(store,request(store),context(**{key:False}),model=m).status=="failed"
        assert not m.calls
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"private"},{"allow_model_calls":True},{"max_actions":999},{"rules":[]},{"commit":True}])
def test_public_authority_is_rejected(extra):
    with pytest.raises(ValueError):SubstantiationRequest.model_validate(extra)


@pytest.mark.parametrize("damage",["foreign_target","missing_target","foreign_revision","missing_artifact","foreign_region","run","target_record"])
def test_scope_failure_before_model_and_retained_attempt(store,damage):
    seed(store);r=region(store);kw={}
    if damage=="foreign_target":kw["targets"]=[{"ref":ref("a","private"),"finding":"Unsupported"}]
    if damage=="missing_target":kw["targets"]=[{"ref":ref("missing"),"finding":"Unsupported"}]
    if damage=="foreign_revision":kw["graph_revision"]=store.graph_revision("papers","private")
    if damage=="missing_artifact":kw["artifact_id"]="0"*64
    if damage=="foreign_region":
        from conftest import seed_region
        kw["source_region_ids"]=(seed_region(store,"secret text",project_id="private").id,)
    if damage=="run":kw["run_id"]="missing"
    if damage=="target_record":kw["target_record_id"]="missing"
    m=Model([]);result=substantiate_graph_snapshot(store,request(store,r,**kw),context(),model=m)
    assert result.status=="failed" and not m.calls
    assert store.records("SubstantiationOutcome") and store.records("SubstantiationProgressProposal")
    assert "secret text" not in result.model_dump_json()


@pytest.mark.parametrize("damage",["quote","unread","foreign_citation","validity","target_index","no_obligations","unsupported_support"])
def test_invalid_assessment_rejected(store,damage):
    seed(store);r=region(store);seq=actions(r);a=seq[1]["arguments"]
    if damage=="quote":a["supporting"][0]["quotation"]="Invented support"
    if damage=="unread":seq=seq[1:]
    if damage=="foreign_citation":a["supporting"][0]["source_id"]="foreign"
    if damage=="validity":a["mathematical_validity"]="verified"
    if damage=="target_index":a["target_index"]=1
    if damage=="no_obligations":a["verification_obligations"]=[]
    if damage=="unsupported_support":a["supporting"]=[]
    result=run(store,r,seq,max_actions=len(seq))
    assert result.status=="failed" and "result" not in result.data
    assert not result.data["assessment_history"]


def test_invalid_quote_repair_and_correction_history(store):
    seed(store);r=region(store);seq=actions(r);bad=copy.deepcopy(seq[1]);bad["arguments"]["supporting"][0]["quotation"]="Invented"
    revised=copy.deepcopy(seq[1]);revised["arguments"].update(correction_reason="Account for assumptions",assumptions=["Lemma hypotheses hold"])
    result=run(store,r,[seq[0],bad,seq[1],revised,*seq[2:]])
    assert result.status=="partial",result
    assert len(result.data["assessment_history"])==2
    assert any(a["status"]=="failed" for a in result.data["attempts"])


@pytest.mark.parametrize("change",["assessment","read","retrieval"])
def test_new_observation_or_assessment_invalidates_analysis(store,change):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store);seq=actions(r)
    extra=copy.deepcopy(seq[1]);extra["arguments"]["correction_reason"]="Reconsider"
    if change=="read":extra=seq[0]
    if change=="retrieval":extra={"name":"retrieve_context","arguments":{"query":"claim obligation","purpose":"Seek counterevidence"}}
    result=run(store,r,[*seq[:3],extra,seq[3]],max_actions=5,retrieval=MathRetrievalPolicy(enabled=True))
    assert result.status=="failed" and "result" not in result.data


def test_real_retrieval_can_supply_previously_unattached_evidence(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store,"The appendix supplies the missing premise for the claim.")
    seq=[{"name":"retrieve_context","arguments":{"query":"appendix premise","purpose":"Seek subtle support or counterevidence"}},*actions(r)[1:]]
    result=substantiate_graph_snapshot(store,request(store),context(retrieval=MathRetrievalPolicy(enabled=True)),model=Model(seq))
    assert result.status=="partial",result
    assert result.data["context_packets"][0]["passages"][0]["region_id"]==r.id
    assert result.data["result"]["assessments"][0]["grounding"][0]["receipt_id"]
    assert result.data["result"]["coverage"]["searches"]==1


def test_genuine_gap_retains_scoped_criticism_not_absence_claim(store):
    seed(store);r=region(store,"The conclusion is asserted without derivation in this passage.")
    a=assessment(None,source_substantiation="support_not_located",recommendation="retain",justification="The examined passage only asserts the conclusion.")
    result=run(store,r,[actions(r)[0],a,*actions(r)[2:]])
    assert result.status=="partial" and result.data["result"]["assessments"][0]["recommendation"]=="retain"
    assert not result.data["result"]["unqualified_absence_supported"] and not result.data["result"]["coverage"]["exhaustive"]


def test_counterevidence_and_circularity_are_retained(store):
    seed(store,(("a","b"),("b","a")));r=region(store,"The cited theorem assumes the conclusion under review.")
    a=assessment(None,source_substantiation="conflicting",recommendation="retain",contradicting=[{"source_id":r.id,"quotation":r.content["text"]}])
    result=run(store,r,[actions(r)[0],a,*actions(r)[2:]])
    assert result.status=="partial",result
    assert result.data["result"]["analysis"]["traces"][0]["trace"]["cycle_witnesses"]
    assert result.data["result"]["assessments"][0]["grounding"][0]["polarity"]=="contradicting"


def test_explicit_unresolved_without_sources_is_not_support_absence(store):
    seed(store);result=run(store,None)
    assert result.status=="partial" and result.data["result"]["assessments"][0]["source_substantiation"]=="unresolved",result
    bad=assessment(None,source_substantiation="support_not_located")
    result=substantiate_graph_snapshot(store,request(store,operation_id="other"),context(max_actions=3),model=Model([bad,*actions()[1:]]))
    assert result.status=="failed"


@pytest.mark.parametrize("error",[ValueError("secret"),CancelledError()])
def test_failed_cancelled_attempts_are_durable(store,error):
    seed(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,None,[error])
    else:
        result=run(store,None,[error]);assert result.status=="failed" and "secret" not in result.model_dump_json()
    assert store.records("SubstantiationProgressProposal")
    receipts=[r.content for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="substantiate_graph_snapshot"]
    assert receipts[0]["status"]==("interrupted" if isinstance(error,CancelledError) else "failed")


def test_multiple_targets_require_complete_assessment_and_edge_identity(store):
    seed(store);req=request(store,targets=[{"ref":ref("a"),"finding":"Missing support"},{"kind":"edge","ref":ref("e0"),"finding":"Dependency may be misrepresented"}])
    incomplete=substantiate_graph_snapshot(store,req,context(max_actions=3),model=Model(actions()))
    assert incomplete.status=="failed"
    req=req.model_copy(update={"operation_id":"complete-targets"})
    result=substantiate_graph_snapshot(store,req,context(),model=Model([assessment(),assessment(target_index=1),*actions()[1:]]))
    assert result.status=="partial",result
    assert len(result.data["result"]["assessments"])==2 and len(result.data["result"]["analysis"]["traces"])==2


def test_deep_extraction_candidate_to_substantiation(store):
    from nima_semantica.deep_extraction_tool import deep_extraction
    from test_deep_extraction import request as er,context as ec,actions as ea
    r=region(store);ex=deep_extraction(store,er(r),ec(),model=Model(ea(r)))
    req=request(store,r,artifact_id=ex.artifacts["graph_proposal"],targets=[{"ref":ref("claim"),"finding":"Support may be missing"}])
    result=substantiate_graph_snapshot(store,req,context(),model=Model(actions(r)))
    assert result.status=="partial",result
    assert result.data["result"]["original_artifact_id"]==ex.artifacts["graph_proposal"]
    assert not store.records("OKFNode")


def test_revision_cannot_silently_drop_open_obligations(store):
    seed(store);initial=assessment(verification_obligations=["Check lemma hypothesis"])
    revised=assessment(verification_obligations=["Check conclusion"],correction_reason="New issue")
    result=run(store,None,[initial,revised,*actions()[1:]])
    assert result.status=="partial",result
    assert result.data["result"]["assessments"][0]["verification_obligations"]==["Check lemma hypothesis","Check conclusion"]


@pytest.mark.parametrize("damage",["source","graph"])
def test_changed_sources_or_graph_prevent_finalization(store,monkeypatch,damage):
    from nima_semantica.substantiation_tool import SubstantiationController
    seed(store);r=region(store);original=SubstantiationController.submit
    def change(controller):
        if damage=="graph":seed(store,(("a","b"),))
        else:
            # Simulate corrupted immutable source storage without changing authorized IDs.
            read=store.read_artifact
            monkeypatch.setattr(store,"read_artifact",lambda key: b"tampered" if key==r.content["artifact_id"] else read(key))
        return original(controller)
    monkeypatch.setattr(SubstantiationController,"submit",change)
    result=run(store,r)
    assert result.status=="failed" and "result" not in result.data
    assert result.data["assessment_history"] and store.records("SubstantiationProgressProposal")


def test_retrieval_not_available_without_operator_permission(store):
    seed(store);m=Model([{"name":"retrieve_context","arguments":{"query":"lemma","purpose":"support"}}])
    result=substantiate_graph_snapshot(store,request(store),context(max_actions=1),model=m)
    assert result.status=="failed" and not result.data["context_packets"]
    assert "retrieve_context" not in [t["function"]["name"] for t in m.calls[0]["tools"]]


def test_unread_regions_and_resource_limits_remain_explicit(store):
    seed(store);r=region(store)
    result=run(store,r,actions())
    assert result.status=="partial" and result.data["result"]["coverage"]["unread_inventory"]==[r.id]
    limited=substantiate_graph_snapshot(store,request(store,r,operation_id="limited"),context(max_actions=1),model=Model([actions(r)[0]]))
    assert limited.status=="failed" and "Action limit" in limited.data["diagnostic"]


def test_revised_assessment_requires_reason(store):
    seed(store);result=run(store,None,[assessment(),assessment(),*actions()[1:]])
    assert result.status=="partial",result
    assert len(result.data["assessment_history"])==1 and any(a["status"]=="failed" for a in result.data["attempts"])


def test_distributed_argument_requires_exact_grounding_for_every_step(store):
    seed(store);r=region(store,"Lemma: the family is uniformly bounded.");other=region(store,"Appendix: the measure is finite.")
    a=assessment(r);a["arguments"]["justification_steps"].append({"explanation":"Finite measure makes the uniform bound integrable.",
        "anchors":[{"source_id":other.id,"quotation":other.content["text"]}]})
    req=request(store,r,source_region_ids=(r.id,other.id))
    seq=[{"name":"read_regions","arguments":{"region_ids":[r.id,other.id]}},a,*actions(r)[2:]]
    result=substantiate_graph_snapshot(store,req,context(),model=Model(seq))
    assert result.status=="partial",result
    chain=[g for g in result.data["result"]["assessments"][0]["grounding"] if g["polarity"]=="justification"]
    assert [g["step_index"] for g in chain]==[0,1] and {g["region_id"] for g in chain}=={r.id,other.id}
    seq[1]["arguments"]["justification_steps"][1]["anchors"][0]["quotation"]="Not in appendix"
    bad=substantiate_graph_snapshot(store,req.model_copy(update={"operation_id":"bad-chain"}),context(max_actions=4),model=Model(seq))
    assert bad.status=="failed" and not bad.data["assessment_history"]


def test_unavailable_retrieval_can_end_with_honest_unresolved_assessment(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store)
    seq=[{"name":"retrieve_context","arguments":{"query":"missing lemma","purpose":"Seek support"}},*actions()]
    result=run(store,None,seq,retrieval=MathRetrievalPolicy(enabled=True,projection_id="unavailable"))
    assert result.status=="partial",result
    assert result.data["result"]["assessments"][0]["source_substantiation"]=="unresolved"
    assert result.data["result"]["coverage"]["searches"]==0
    assert any(a["kind"]=="retrieve_context" and a["status"]=="failed" for a in result.data["attempts"])
