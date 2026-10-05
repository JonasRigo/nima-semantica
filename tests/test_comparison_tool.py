"""Native comparison acceptance; scripted model, real storage/retrieval/reasoning."""
from asyncio import CancelledError
import copy
import pytest
from nima_semantica.comparison_contracts import CompareObjectsRequest,ComparisonContext
from nima_semantica.comparison_tool import compare_research_objects,ComparisonController
from nima_semantica.models import ConflictError,Record
from test_source_pipeline import store
from test_deep_extraction import region,Model
from model_fixture import MANIFEST
from test_claim_dependencies import seed,ref


def context(**kw):return ComparisonContext(**({"corpus_id":"papers","project_id":"research","allow_model_calls":True,"allow_audit_writes":True,"model_manifest":MANIFEST}|kw))
def objects():return [{"object_id":"left","graph_ref":ref("a")},{"object_id":"right","graph_ref":ref("b")}]
def request(store,r=None,**kw):return CompareObjectsRequest(**({"mode":"mathematical_objects","operation_id":"compare-1","graph_revision":store.graph_revision("papers","research"),
    "objects":objects(),"criteria":[{"criterion_id":"logic","description":"Compare dependencies and unresolved assumptions"}],"source_region_ids":(r.id,) if r else ()}|kw))
def assessment(r=None,**kw):return {"name":"assess_criterion","arguments":{"criterion_id":"logic","pairs":[{"left":"left","right":"right","relation":"unresolved",
    "rationale":"The represented dependencies differ; semantic consequences need checking.","unresolved_obligations":["Check source correspondence and logical compatibility."],
    "supporting":[{"source_id":r.id,"quotation":r.content["text"]}] if r else [],**kw}]}}
def actions(r=None):return ([{"name":"read_regions","arguments":{"region_ids":[r.id]}}] if r else [])+[assessment(r),{"name":"analyze_objects","arguments":{}},{"name":"submit_result","arguments":{}}]
def run(store,r=None,seq=None,**kw):return compare_research_objects(store,request(store,r),context(**kw),model=Model(seq or actions(r)))


def test_graph_grounded_comparison_is_private_advisory_and_replayable(store):
    seed(store);req=request(store);m=Model(actions());result=compare_research_objects(store,req,context(),model=m)
    assert result.status=="partial",result
    out=result.data["result"];assert len(out["analysis"]["dependency_traces"])==2
    assert out["selected_object_id"] is None and not out["equivalence_verified"] and not out["scientific_admission"]
    assert not out["parent_proof_completed"] and not result.data["reasoning_state"]["publishable"]
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert store.graph_revision("papers","research")==req.graph_revision
    assert store.records("ComparisonOutcome") and store.records("ComparisonProgressProposal")
    before=store.revision;assert compare_research_objects(store,req,context(),model=m)==result and store.revision==before
    with pytest.raises(ConflictError):compare_research_objects(store,req.model_copy(update={"objective":"Changed"}),context(),model=m)


def test_exact_evidence_and_counterevidence(store):
    seed(store);r=region(store);seq=actions(r);seq[1]["arguments"]["pairs"][0]["contradicting"]=[{"source_id":r.id,"quotation":r.content["text"]}]
    result=run(store,r,seq);assert result.status=="partial",result
    p=result.data["result"]["assessments"][0]["pairs"][0]
    assert {g["polarity"] for g in p["exact_grounding"]}=={"supporting","contradicting"}
    assert not p["source_entailment_verified"]


def test_preview_and_denial_no_side_effects(store):
    before=store.revision
    assert not compare_research_objects(None,CompareObjectsRequest(),context()).data["executed"]
    for flag in ("allow_model_calls","allow_audit_writes"):
        m=Model([]);assert compare_research_objects(store,request(store),context(**{flag:False}),model=m).status=="failed" and not m.calls
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"foreign"},{"allow_model_calls":True},{"allow_audit_writes":True},{"max_actions":999},{"worker_url":"evil"},{"model":"other"}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):CompareObjectsRequest.model_validate(extra)


@pytest.mark.parametrize("damage",["object","region","revision","record","run","artifact","target"])
def test_invalid_scope_prevents_model(store,damage):
    seed(store);kw={}
    if damage=="object":kw["objects"]=[objects()[0],{"object_id":"right","graph_ref":ref("a","other")}]
    if damage=="region":kw["source_region_ids"]=("missing",)
    if damage=="revision":kw["graph_revision"]=store.graph_revision("papers","other")
    if damage=="record":kw["objects"]=[objects()[0],{"object_id":"right","record_id":"missing"}]
    if damage=="run":kw["run_id"]="missing"
    if damage=="artifact":kw["artifact_id"]="0"*64
    if damage=="target":kw["target"]=ref("a","other")
    m=Model([]);result=compare_research_objects(store,request(store,**kw),context(),model=m)
    assert result.status=="failed" and not m.calls and store.records("ComparisonProgressProposal")


@pytest.mark.parametrize("damage",["quote","unread","pair","duplicate","criterion","certification","obligation"])
def test_invalid_assessment_cannot_finalize(store,damage):
    seed(store);r=region(store);seq=actions(r);a=seq[1]["arguments"];p=a["pairs"][0]
    if damage=="quote":p["supporting"][0]["quotation"]="Invented"
    if damage=="unread":seq=seq[1:]
    if damage=="pair":p["right"]="unselected"
    if damage=="duplicate":a["pairs"].append(copy.deepcopy(p))
    if damage=="criterion":a["criterion_id"]="invented"
    if damage=="certification":p["equivalence_verified"]=True
    if damage=="obligation":p["unresolved_obligations"]=[]
    result=run(store,r,seq,max_actions=len(seq));assert result.status=="failed" and "result" not in result.data


def test_all_pairs_and_all_criteria_required(store):
    seed(store);req=request(store,objects=[*objects(),{"object_id":"third","graph_ref":ref("c")}])
    result=compare_research_objects(store,req,context(max_actions=3),model=Model(actions()))
    assert result.status=="failed"
    req=request(store,operation_id="second",criteria=[*request(store).criteria,{"criterion_id":"empirical","description":"Evidence"}])
    assert compare_research_objects(store,req,context(max_actions=3),model=Model(actions())).status=="failed"


def test_revision_preserves_obligations_and_requires_reanalysis(store):
    seed(store);first=assessment();revised=assessment(unresolved_obligations=["Check another premise."])
    revised["arguments"]["correction_reason"]="Additional gap found"
    result=run(store,seq=[first,revised,*actions()[1:]])
    assert result.status=="partial",result
    assert len(result.data["result"]["assessments"][0]["pairs"][0]["unresolved_obligations"])==2
    assert len(result.data["assessment_history"])==2


@pytest.mark.parametrize("change",["revision","retrieval"])
def test_new_evidence_or_revision_invalidates_analysis(store,change):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store);seq=actions(r);extra=assessment(r)
    extra["arguments"]["correction_reason"]="Reconsider"
    if change=="retrieval":extra={"name":"retrieve_context","arguments":{"query":"claim obligation","purpose":"Find counterevidence"}}
    result=run(store,r,[*seq[:3],extra,seq[3]],max_actions=5,retrieval=MathRetrievalPolicy(mode="lexical",enabled=True))
    assert result.status=="failed" and "result" not in result.data


def test_native_retrieval_adds_unattached_exact_passages(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store,"Integer identities require assumptions.")
    seq=[{"name":"retrieve_context","arguments":{"query":"integer identities","purpose":"Compare evidence"}},*actions(r)[1:]]
    result=run(store,seq=seq,retrieval=MathRetrievalPolicy(mode="lexical",enabled=True));assert result.status=="partial",result
    assert result.data["result"]["coverage"]["read_region_ids"]==[r.id]


@pytest.mark.parametrize("error",[ValueError("secret"),CancelledError()])
def test_failed_cancelled_attempts_retained(store,error):
    seed(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,seq=[error])
    else:assert run(store,seq=[error]).status=="failed"
    assert store.records("ComparisonOutcome") and store.records("ComparisonProgressProposal")


@pytest.mark.parametrize("damage",["source","graph"])
def test_changed_sources_or_graph_prevent_publication(store,monkeypatch,damage):
    seed(store);r=region(store);original=ComparisonController.submit
    def change(controller):
        if damage=="graph":seed(store,(("a","b"),))
        else:
            read=store.read_artifact
            monkeypatch.setattr(store,"read_artifact",lambda key:b"tampered" if key==r.content["artifact_id"] else read(key))
        return original(controller)
    monkeypatch.setattr(ComparisonController,"submit",change)
    result=run(store,r);assert result.status=="failed" and "result" not in result.data


def typed_objects(kind="mathematical_object",**kw):
    return [{"object_id":name,"inline":{"kind":kind,"statement":"Local conjecture "+name,"domain":"integers","assumptions":["x is integer"],
        "unresolved_obligations":["Prove the step"],"dependencies":[ref("b")],**kw}} for name in ("left","right")]


@pytest.mark.parametrize("mode,kind",[("mathematical_objects","mathematical_object"),("proof_strategies","proof_strategy"),("proof_attempts","proof_attempt")])
def test_typed_object_modes_preserve_target_assumptions_and_obligations(store,mode,kind):
    seed(store);req=request(store,mode=mode,objects=typed_objects(kind,target=ref("a")),target=ref("a"))
    result=compare_research_objects(store,req,context(),model=Model(actions()))
    assert result.status=="partial",result
    assert all(o["assumptions"]==["x is integer"] and o["unresolved_obligations"]==["Prove the step"] for o in result.data["result"]["objects"])
    assert result.data["project_progress"]["graph_target"]==ref("a").model_dump(mode="json")


@pytest.mark.parametrize("damage",["assumptions","domain","target","represented_conflict","loss"])
def test_equivalence_cannot_override_represented_differences(store,damage):
    seed(store);obj=typed_objects();a=assessment(relation="proposed_equivalent")
    if damage=="assumptions":obj[1]["inline"]["assumptions"]=["x is positive"]
    if damage=="domain":obj[1]["inline"]["domain"]="reals"
    if damage=="target":obj[1]["inline"]["target"]=ref("a")
    if damage=="represented_conflict":a["arguments"]["pairs"][0]["conflicting_premises"]=["Incompatible premises"]
    if damage=="loss":a["arguments"]["pairs"][0]["alignment_losses"]=["Lost direction"]
    result=compare_research_objects(store,request(store,objects=obj),context(max_actions=3),model=Model([a,*actions()[1:]]))
    assert result.status=="failed"


def test_proof_target_mismatch_prevents_model(store):
    seed(store);obj=typed_objects("proof_strategy",target=ref("a"));obj[1]["inline"]["target"]=ref("b")
    m=Model([]);result=compare_research_objects(store,request(store,mode="proof_strategies",target=ref("a"),objects=obj),context(),model=m)
    assert result.status=="failed" and not m.calls


def test_hypothesis_generation_outputs_are_comparable(store):
    from test_hypothesis_tool import run as generate,proposal,actions as ha
    seed(store);r=region(store);generated=generate(store,r,seq=[ha(r)[0],proposal(r),proposal(r,candidate_id="h2",statement="Another conjecture."),*ha(r)[2:]])
    assert generated.status=="partial",generated
    pp=generated.data["result"]["proposals"]
    req=request(store,r,mode="hypotheses",objects=[{"object_id":"left","hypothesis_id":pp[0]["proposal_id"]},{"object_id":"right","hypothesis":pp[1]}])
    result=compare_research_objects(store,req,context(),model=Model(actions(r)));assert result.status=="partial",result
    assert result.data["result"]["objects"][0]["record_id"] and result.data["result"]["objects"][1]["original_proposal"]==pp[1]


def test_typed_record_and_progress_integration(store):
    from nima_semantica.comparison_contracts import ResearchObject
    from test_project_update import request as ur,context as uc,approved
    from nima_semantica.project_update import update_project_graph
    from nima_semantica.okf_contracts import OKFDelta
    seed(store);obj=typed_objects("proof_attempt",target=ref("a"))
    record=Record(kind="ResearchObject",corpus_id="papers",project_id="research",content=ResearchObject.model_validate(obj[0]["inline"]).model_dump(mode="json"))
    obj[0]={"object_id":"left","record_id":store.put(record)}
    result=compare_research_objects(store,request(store,mode="proof_attempts",target=ref("a"),objects=obj),context(),model=Model(actions()))
    assert result.status=="partial",result
    req=ur(store,mode="prepare",delta=None,progress_proposal_ids=(result.data["project_progress"]["record_id"],))
    prep=update_project_graph(store,req,uc());assert prep.status=="complete",prep
    commit=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(OKFDelta.model_validate(prep.data["delta"])))
    assert commit.status=="complete" and store.records("ProjectProgressCommit")


def test_three_objects_two_criteria_cover_every_pair_without_selecting(store):
    from itertools import combinations
    seed(store);obj=[*objects(),{"object_id":"third","graph_ref":ref("c")}]
    criteria=[{"criterion_id":k,"description":k} for k in ("logic","empirical")]
    seq=[]
    for c in criteria:
        a=assessment();a["arguments"]["criterion_id"]=c["criterion_id"]
        pair=a["arguments"]["pairs"][0]
        a["arguments"]["pairs"]=[{**pair,"left":l,"right":r} for l,r in combinations(("left","right","third"),2)]
        seq.append(a)
    result=compare_research_objects(store,request(store,objects=obj,criteria=criteria),context(),model=Model([*seq,*actions()[1:]]))
    assert result.status=="partial",result
    assert len(result.data["result"]["assessments"])==2
    assert all(len(a["pairs"])==3 for a in result.data["result"]["assessments"])


def test_proposed_equivalence_stays_unverified(store):
    seed(store);result=compare_research_objects(store,request(store,objects=typed_objects()),context(),model=Model([assessment(relation="proposed_equivalent"),*actions()[1:]]))
    assert result.status=="partial",result
    p=result.data["result"]["assessments"][0]["pairs"][0]
    assert p["relation"]=="proposed_equivalent" and not p["equivalence_verified"]


def test_unauthorized_retrieval_is_rejected_and_never_advertised(store):
    seed(store);m=Model([{"name":"retrieve_context","arguments":{"query":"claim","purpose":"Find evidence"}},*actions()])
    result=compare_research_objects(store,request(store),context(),model=m)
    assert result.status=="partial",result
    assert not result.data["context_packets"] and "retrieve_context" not in [t["function"]["name"] for t in m.calls[0]["tools"]]
    assert any(a["status"]=="failed" for a in result.data["attempts"])


@pytest.mark.parametrize("damage",["scope","evidence","source_reference","graph_reference"])
def test_invalid_typed_hypothesis_references_prevent_model(store,damage):
    from test_hypothesis_tool import run as generate
    seed(store);r=region(store);p=copy.deepcopy(generate(store,r).data["result"]["proposals"][0])
    if damage=="scope":p["project_id"]="foreign"
    if damage=="evidence":p["evidence"][0]["region_id"]="missing"
    if damage=="source_reference":p["supporting_references"][0]["revision"]="wrong"
    if damage=="graph_reference":p["model_metadata"]["candidate"]["graph_references"]=[{"kind":"node","ref":ref("a","foreign").model_dump(mode="json")}]
    obj=[{"object_id":"left","hypothesis":p},{"object_id":"right","hypothesis":p}];m=Model([])
    result=compare_research_objects(store,request(store,mode="hypotheses",objects=obj),context(),model=m)
    assert result.status=="failed" and not m.calls


def test_hypothesis_graph_context_is_traced_without_invented_entailment(store):
    from test_hypothesis_tool import context as hc,request as hr,proposal,actions as ha
    from nima_semantica.hypothesis_tool import generate_hypotheses
    seed(store);target={"kind":"node","ref":ref("a").model_dump(mode="json")}
    gen=generate_hypotheses(store,hr(store,graph_targets=[target]),hc(),model=Model([proposal(graph_references=[target]),*ha()[1:]]))
    assert gen.status=="partial",gen
    p=gen.data["result"]["proposals"][0]
    req=request(store,mode="hypotheses",objects=[{"object_id":"left","hypothesis":p},{"object_id":"right","hypothesis":p}])
    result=compare_research_objects(store,req,context(),model=Model(actions()))
    assert result.status=="partial",result
    assert len(result.data["result"]["analysis"]["dependency_traces"])==2


def test_publication_revalidates_inside_transaction(store,monkeypatch):
    seed(store);original=ComparisonController.submit;calls=[]
    def change(controller):
        calls.append(True)
        if len(calls)==2:seed(store,(("a","b"),))
        return original(controller)
    monkeypatch.setattr(ComparisonController,"submit",change)
    result=run(store)
    assert result.status=="failed" and "result" not in result.data and len(calls)==2
    assert store.records("ComparisonProgressProposal")


def test_record_tampering_invalidates_comparison(store,monkeypatch):
    from nima_semantica.comparison_contracts import ResearchObject
    seed(store);obj=typed_objects();record=Record(kind="ResearchObject",corpus_id="papers",project_id="research",content=ResearchObject.model_validate(obj[0]["inline"]).model_dump(mode="json"))
    rid=store.put(record);obj[0]={"object_id":"left","record_id":rid};original=ComparisonController.submit
    def change(controller):
        get=store.get
        monkeypatch.setattr(store,"get",lambda key,**kw: record.model_copy(update={"content":{**record.content,"statement":"tampered"}}) if key==rid else get(key,**kw))
        return original(controller)
    monkeypatch.setattr(ComparisonController,"submit",change)
    result=compare_research_objects(store,request(store,objects=obj),context(),model=Model(actions()))
    assert result.status=="failed" and "result" not in result.data
