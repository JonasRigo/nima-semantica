"""Review boundaries and connected native checks, not model-quality scores."""
from asyncio import CancelledError
import copy
import json
import pytest
from nima_semantica.review_contracts import ReviewRequest,ReviewContext,ReviewTarget
from nima_semantica.review_tool import review_research,ReviewController
from nima_semantica.models import identity,ConflictError
from test_source_pipeline import store
from test_claim_dependencies import seed,ref
from test_deep_extraction import region,Model
from model_fixture import MANIFEST
from test_counterexample_tool import Worker,encoding


def target(**kw):
    content={"target_id":"step","ref":ref("a").model_dump(mode="json"),"statement":"For every integer x, x squared equals x.",
        "argument":"Proposed local argument: x squared equals x by cancellation.","domain":"integers","quantifier":"forall",
        "assumptions":[],"dependencies":[],"unresolved_obligations":["Check the cancellation step."],"source_region_ids":[],**kw}
    return ReviewTarget(**content,content_revision=identity(content))
def request(store,r=None,**kw):return ReviewRequest(**({"mode":"proof_assessment","operation_id":"review-1","graph_revision":store.graph_revision("papers","research"),"targets":(target(),),"source_region_ids":(r.id,) if r else ()}|kw))
def context(**kw):return ReviewContext(**({"corpus_id":"papers","project_id":"research","allow_model_calls":True,"allow_audit_writes":True,"model_manifest":MANIFEST}|kw))
def assessment(r=None,**kw):return {"name":"assess_target","arguments":{"target_id":"step","assessment":"unresolved","rationale":"The cancellation requires an additional justification.",
    "target_correspondence":"Exact selected local argument, not the parent proof.","assumption_analysis":"No assumptions supplied.","inference_analysis":"Cancellation needs checking.",
    "dependency_analysis":"Inspect represented dependency cycles separately.","source_applicability":"No automatic entailment check.","testability":"not_tested","testability_reason":"No test yet.",
    "supporting":[{"source_id":r.id,"quotation":r.content["text"]}] if r else [],"unresolved_obligations":["Check source correspondence."],"limitations":["Only selected argument reviewed."],**kw}}
def actions(r=None):return ([{"name":"read_regions","arguments":{"region_ids":[r.id]}}] if r else [])+[assessment(r),{"name":"analyze_review","arguments":{}},{"name":"submit_result","arguments":{}}]
def search(spec=None):return {"name":"search_counterexamples","arguments":{"target_id":"step","encoding":(spec or encoding()).model_dump(mode="json")}}
def run(store,r=None,seq=None,worker=None,req=None,**kw):return review_research(store,req or request(store,r),context(**kw),model=Model(seq or actions(r)),worker=worker)


def test_review_private_progress_and_replay(store):
    seed(store);r=region(store);req=request(store,r);m=Model(actions(r));result=review_research(store,req,context(),model=m)
    assert result.status=="partial",result
    out=result.data["result"]
    assert out["assessments"][0]["exact_grounding"] and out["analysis"]["traces"]
    assert not out["parent_proof_completed"] and not out["scientific_admission"]
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert store.graph_revision("papers","research")==req.graph_revision
    assert store.records("ReviewOutcome") and store.records("ReviewProgressProposal")
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    assert review_research(store,req,context(),model=m)==result
    with pytest.raises(ConflictError):review_research(store,req,context(max_actions=25),model=m)


def test_preview_and_denied_permissions_no_writes(store):
    before=store.revision
    assert not review_research(None,ReviewRequest(),context()).data["executed"]
    for flag in ("allow_model_calls","allow_audit_writes"):
        m=Model([]);assert run(store,seq=[ValueError()],**{flag:False}).status=="failed"
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"foreign"},{"allow_counterexamples":True},{"allow_substantiation":True},{"worker_url":"evil"},{"model":"other"},{"commit":True}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):ReviewRequest.model_validate(extra)


@pytest.mark.parametrize("damage",["target","region","revision","run","artifact","record","dependency"])
def test_invalid_scope_prevents_model(store,damage):
    seed(store);kw={}
    if damage=="target":kw["targets"]=(target(ref=ref("a","other").model_dump(mode="json")),)
    if damage=="dependency":kw["targets"]=(target(dependencies=[ref("a","other").model_dump(mode="json")]),)
    if damage=="region":kw["source_region_ids"]=("missing",)
    if damage=="revision":kw["graph_revision"]=store.graph_revision("papers","other")
    if damage=="run":kw["run_id"]="missing"
    if damage=="artifact":kw["artifact_id"]="0"*64
    if damage=="record":kw["parent_record_id"]="missing"
    m=Model([]);result=review_research(store,request(store,**kw),context(),model=m)
    assert result.status=="failed" and not m.calls and store.records("ReviewOutcome")


def test_exact_target_content_revision():
    t=target().model_dump(mode="json");t["argument"]="Changed argument"
    with pytest.raises(ValueError):ReviewTarget.model_validate(t)


@pytest.mark.parametrize("damage",["quote","unread","target","certification","obligations","fake_search","missing_support"])
def test_invalid_assessment_rejected(store,damage):
    seed(store);r=region(store);seq=actions(r);a=seq[1]["arguments"]
    if damage=="quote":a["supporting"][0]["quotation"]="Invented"
    if damage=="unread":seq=seq[1:]
    if damage=="target":a["target_id"]="other"
    if damage=="certification":a["mathematical_validity"]="verified"
    if damage=="obligations":a["unresolved_obligations"]=[]
    if damage=="fake_search":a["testability"]="searched"
    if damage=="missing_support":a.update(assessment="concerns",missing_support_finding="No support")
    result=run(store,r,seq,max_actions=len(seq));assert result.status=="failed" and "result" not in result.data


def test_native_search_binds_scope_and_independent_witness(store):
    seed(store);worker=Worker();seq=[search(),assessment(testability="searched",assessment="concerns"),*actions()[1:]]
    result=run(store,seq=seq,worker=worker,allow_counterexamples=True,counterexample_target_ids=("step",))
    assert result.status=="partial",result
    test=result.data["result"]["tests"][0];found=test["result"]["data"]["result"]
    assert found["outcome"]=="validated_counterexample_to_encoding" and found["independent_check"]["validated"]
    assert test["content_revision"]==target().content_revision and test["request"]["statement"]==target().statement
    assert test["request"]["graph_revision"]==request(store).graph_revision.model_dump(mode="json")
    assert not found["source_correspondence_verified"] and len(worker.calls)==1
    assert store.records("CounterexampleProgressProposal")


@pytest.mark.parametrize("domain",["integers","all integers","every integer","the integers"])
def test_nested_search_canonical_integer_wording(store,domain):
    seed(store)
    selected=target(domain=domain)
    seq=[search(),assessment(testability="searched",assessment="concerns"),*actions()[1:]]
    result=run(store,seq=seq,worker=Worker(),req=request(store,targets=(selected,)),
        allow_counterexamples=True,counterexample_target_ids=("step",))
    assert result.status=="partial",result
    assert result.data["result"]["tests"][0]["request"]["domain"]=="integers"
    assert result.data["result"]["targets"][0]["domain"]==domain


@pytest.mark.parametrize("damage",["permission","target","limit","evaluations"])
def test_unauthorized_search_never_runs_worker(store,damage):
    seed(store);worker=Worker();kw=dict(allow_counterexamples=True,counterexample_target_ids=("step",),max_actions=1)
    if damage=="permission":kw["allow_counterexamples"]=False
    if damage=="target":kw["counterexample_target_ids"]=()
    if damage=="limit":kw["max_counterexamples"]=0
    if damage=="evaluations":kw["max_evaluations"]=1
    result=run(store,seq=[search()],worker=worker,**kw)
    assert result.status=="failed" and not worker.calls


def test_no_witness_never_proves_and_old_witness_cannot_be_hidden(store):
    seed(store);seq=[search(encoding(lower=0,upper=1)),assessment(assessment="plausible",testability="searched"),*actions()[1:]]
    result=run(store,seq=seq,worker=Worker(),allow_counterexamples=True,counterexample_target_ids=("step",))
    assert result.status=="partial" and result.data["result"]["mathematical_validity"]=="not_verified"
    seq.insert(0,search())
    result=run(store,seq=seq,worker=Worker(),req=request(store,operation_id="second"),allow_counterexamples=True,counterexample_target_ids=("step",),max_actions=len(seq))
    assert result.status=="failed" and len(result.data["tests"])==2


@pytest.mark.parametrize("error",[ValueError("private failure"),CancelledError()])
def test_nested_failures_and_cancellation_preserve_both_attempts(store,error):
    seed(store);kw=dict(seq=[search()],worker=Worker(error=error),allow_counterexamples=True,counterexample_target_ids=("step",),max_actions=1)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,**kw)
    else:assert run(store,**kw).status=="failed"
    assert store.records("ReviewProgressProposal") and store.records("CounterexampleProgressProposal")
    receipt=next(r.content for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="review_research")
    assert receipt["metadata"]["result"]["data"]["tests"][0]["receipt_id"]


def test_new_read_requires_reassessment_and_retains_obligations(store):
    seed(store);r=region(store);seq=actions(r);seq=[assessment(),seq[0],*seq[2:]]
    assert run(store,r,seq,max_actions=4).status=="failed"
    seq.insert(2,assessment(r,correction_reason="Read additional evidence",unresolved_obligations=["Another obligation"]))
    result=run(store,r,seq,req=request(store,r,operation_id="repair"))
    assert result.status=="partial",result
    assert len(result.data["result"]["assessments"][0]["unresolved_obligations"])==3


def test_all_selected_parts_required(store):
    seed(store);req=request(store,targets=(target(),target(target_id="second",ref=ref("b").model_dump(mode="json"))))
    assert run(store,req=req,max_actions=3).status=="failed"


def test_missing_support_without_permission_stays_unresolved(store):
    seed(store);seq=[assessment(missing_support_finding="Support not located"),*actions()[1:]]
    result=run(store,seq=seq);assert result.status=="partial",result
    assert result.data["result"]["assessments"][0]["substantiation"]["status"]=="unresolved"


def test_nested_substantiation_exact_finding_and_retained_support(store):
    from test_substantiation import actions as substantiation_actions
    from nima_semantica.providers import Invocation
    seed(store);r=region(store);pending=iter(substantiation_actions(r));step=[0]
    def model(prompt):
        if "Substantiate Graph Snapshot" in prompt["messages"][0]["content"]:a=next(pending)
        else:
            i=step[0];step[0]+=1;view=json.loads(prompt["messages"][-1]["content"])
            if i==0:a={"name":"substantiate_target","arguments":{"target_id":"step","finding":"No support"}}
            elif i==1:a=assessment(missing_support_finding="No support",substantiation_operation_id=view["substantiations"][0]["operation_id"])
            else:a=actions()[i-1]
        return Invocation(result=a,manifest=MANIFEST,input_tokens=1,output_tokens=1)
    result=review_research(store,request(store,r),context(allow_substantiation=True),model=model)
    assert result.status=="partial",result
    a=result.data["result"]["assessments"][0]
    assert a["substantiation"]["assessment"]["source_substantiation"]=="support_located" and a["assessment"]=="unresolved"
    assert store.records("SubstantiationProgressProposal")


def test_progress_commits_only_with_explicit_approval(store):
    from test_project_update import request as ur,context as uc,approved
    from nima_semantica.project_update import update_project_graph
    from nima_semantica.okf_contracts import OKFDelta
    seed(store);result=run(store)
    req=ur(store,mode="prepare",delta=None,progress_proposal_ids=(result.data["project_progress"]["record_id"],))
    prep=update_project_graph(store,req,uc());assert prep.status=="complete",prep
    commit=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(OKFDelta.model_validate(prep.data["delta"])))
    assert commit.status=="complete" and store.records("ProjectProgressCommit")


@pytest.mark.parametrize("domain,quantifier",[("reals","forall"),("integers","exists")])
def test_unsupported_domains_are_retained_without_execution(store,domain,quantifier):
    seed(store);worker=Worker();req=request(store,targets=(target(domain=domain,quantifier=quantifier),))
    result=run(store,req=req,seq=[search(),assessment(testability="unsupported"),*actions()[1:]],worker=worker,
        allow_counterexamples=True,counterexample_target_ids=("step",))
    assert result.status=="partial" and not worker.calls
    assert result.data["tests"][0]["result"]["status"]=="failed"


def test_retrieval_is_native_and_exact(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store,"Integer cancellation requires assumptions.")
    seq=[{"name":"retrieve_context","arguments":{"query":"integer cancellation","purpose":"Assess evidence"}},*actions(r)[1:]]
    result=run(store,seq=seq,retrieval=MathRetrievalPolicy(mode="lexical",enabled=True))
    assert result.status=="partial",result
    assert result.data["result"]["coverage"]["read_region_ids"]==[r.id]


@pytest.mark.parametrize("error",[ValueError("private"),CancelledError()])
def test_model_failure_or_cancellation_retains_progress(store,error):
    seed(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,seq=[error])
    else:assert run(store,seq=[error]).status=="failed"
    assert store.records("ReviewOutcome") and store.records("ReviewProgressProposal")


def test_publication_rechecks_graph_after_model_submit(store,monkeypatch):
    seed(store);original=ReviewController.submit;calls=[0]
    def altered(self):
        calls[0]+=1
        if calls[0]==2:seed(store,(("a","b"),))
        return original(self)
    monkeypatch.setattr(ReviewController,"submit",altered)
    result=run(store);assert result.status=="failed" and "result" not in result.data


def test_substantiation_permission_denial(store):
    seed(store);result=run(store,seq=[{"name":"substantiate_target","arguments":{"target_id":"step","finding":"Missing support"}}],max_actions=1)
    assert result.status=="failed" and not store.records("SubstantiationOutcome")


@pytest.mark.parametrize("outcome,expected",[("support_not_located","partial"),("support_located","failed"),("unresolved","failed"),("failure","failed"),("mismatch","failed")])
def test_missing_support_publication_gate_with_native_substantiation(store,outcome,expected):
    from test_substantiation import assessment as sa
    from nima_semantica.providers import Invocation
    seed(store);r=region(store)
    native=([ValueError("unavailable")] if outcome=="failure" else
        [{"name":"read_regions","arguments":{"region_ids":[r.id]}},
            sa(r) if outcome=="support_located" else sa(source_substantiation="support_not_located" if outcome in ("support_not_located","mismatch") else "unresolved"),
            {"name":"analyze_dependencies","arguments":{}},{"name":"submit_result","arguments":{}}])
    pending=iter(native);step=[0]
    def model(prompt):
        if "Substantiate Graph Snapshot" in prompt["messages"][0]["content"]:
            a=next(pending)
            if isinstance(a,Exception):raise a
        else:
            i=step[0];step[0]+=1;view=json.loads(prompt["messages"][-1]["content"])
            if i==0:a={"name":"substantiate_target","arguments":{"target_id":"step","finding":"No support"}}
            elif i==1:a=assessment(assessment="concerns",missing_support_finding="Other finding" if outcome=="mismatch" else "No support",substantiation_operation_id=view["substantiations"][0]["operation_id"])
            else:a=actions()[i-1]
        return Invocation(result=a,manifest=MANIFEST,input_tokens=1,output_tokens=1)
    result=review_research(store,request(store,r),context(allow_substantiation=True,max_actions=4),model=model)
    assert result.status==expected,result
    if expected=="partial":
        a=result.data["result"]["assessments"][0]
        assert not a["unqualified_absence_supported"] and a["substantiation"]["assessment"]["coverage_limitations"]


def test_corrupted_worker_output_is_not_successful_search(store):
    seed(store);seq=[search(),assessment(testability="searched"),*actions()[1:]]
    result=run(store,seq=seq,worker=Worker(mutate=lambda o:o.update(witness={"x":0})),allow_counterexamples=True,counterexample_target_ids=("step",),max_actions=4)
    assert result.status=="failed" and result.data["tests"][0]["result"]["status"]=="failed"


@pytest.mark.skipif(__import__("os").environ.get("NIMA_LIVE_SYMBOLIC")!="1",reason="requires isolated symbolic worker")
def test_review_uses_actual_isolated_worker(store):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    seed(store);seq=[search(),assessment(assessment="concerns",testability="searched"),*actions()[1:]]
    result=run(store,seq=seq,worker=configured_symbolic_worker(),allow_counterexamples=True,counterexample_target_ids=("step",))
    assert result.status=="partial",result
    out=result.data["result"]["tests"][0]["result"]["data"]["result"]
    assert out["independent_check"]["validated"] and out["outcome"]=="validated_counterexample_to_encoding"
