"""Controller acceptance; scripted models do not measure formalization ability."""
from asyncio import CancelledError
import pytest
from nima_semantica.lean_draft_contracts import *
from nima_semantica.lean_draft_tool import draft_lean
from nima_semantica.lean_verification_service import LeanVerificationService
from nima_semantica.models import identity, ConflictError
from test_source_pipeline import store
from test_claim_dependencies import seed,ref
from proof_fixture import target
from test_deep_extraction import Model,region
from model_fixture import MANIFEST
from test_verify_lean_tool import Verifier,TRUE_TYPE_FINGERPRINT,FALSE_TYPE_FINGERPRINT
from nima_semantica.providers import Invocation

def request(store,v=None,**kw):
    return DraftLeanRequest(**(dict(mode="draft",operation_id="draft-1",graph_revision=store.graph_revision("papers","research"),
        target=target(statement="True",unresolved_obligations=()),
        environment_digest=identity(LeanVerificationService(store,v or Verifier()).environment_manifest()),
        expected_declaration_type_fingerprints={"target":TRUE_TYPE_FINGERPRINT})|kw))
def context(**kw):return DraftLeanContext(**(dict(corpus_id="papers",project_id="research",allow_model_calls=True,allow_audit_writes=True,
    allow_execution=True,model_manifest=MANIFEST)|kw))
def propose(**kw):return {"name":"propose_source","arguments":dict(modules=[{"name":"Main","source":"theorem target : True := True.intro"}],
    root_modules=["Main"],correspondence="Exact trivial target.",**kw)}
def act(name,**kw):return {"name":name,"arguments":kw}
def actions():return [propose(),act("verify_source"),act("submit_result")]
def run(store,seq=None,req=None,v=None,**kw):return draft_lean(store,req or request(store,v),context(**kw),model=Model(seq or actions()),verifier=v or Verifier())

def test_verified_local_draft_replay_and_private_state(store):
    seed(store);v=Verifier();req=request(store,v);m=Model(actions())
    r=draft_lean(store,req,context(),model=m,verifier=v)
    assert r.status=="partial",r
    out=r.data["result"]
    assert out["formal_verification_accepted"] and out["formal_target_fully_pinned"]
    assert not out["correspondence_verified"] and not out["parent_proof_completed"]
    assert r.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert store.graph_revision("papers","research")==req.graph_revision
    assert store.records("LeanDraftProgressProposal")
    assert draft_lean(store,req,context(),model=m,verifier=v)==r
    assert len(m.calls)==3 and len(v.calls)==1

def test_preview_and_operator_permissions(store):
    before=store.revision
    assert not draft_lean(None,DraftLeanRequest(),context()).data["executed"]
    for field in ("allow_model_calls","allow_audit_writes"):
        assert run(store,**{field:False}).status=="failed"
    assert store.revision==before

@pytest.mark.parametrize("field",["allow_execution","allow_model_calls","lean_search","corpus_id","project_id","model"])
def test_harness_cannot_enable_capabilities(field):
    with pytest.raises(ValueError):DraftLeanRequest.model_validate({field:True})

@pytest.mark.parametrize("damage",["environment","revision","target","prior","dag","run"])
def test_invalid_bindings_before_model(store,damage):
    seed(store);kw={}
    if damage=="environment":kw["environment_digest"]="f"*64
    if damage=="revision":kw["graph_revision"]=store.graph_revision("papers","foreign")
    if damage=="target":kw["target"]=target(ref=ref("a","foreign"))
    if damage=="prior":kw["prior_artifact_ids"]=("f"*64,)
    if damage=="dag":kw["proof_dag_record_id"]="missing"
    if damage=="run":kw["run_id"]="missing"
    m=Model([]);r=draft_lean(store,request(store,**kw),context(),model=m,verifier=Verifier())
    assert r.status=="failed" and not m.calls

@pytest.mark.parametrize("error",[ValueError(),CancelledError()])
def test_failed_cancelled_records(store,error):
    seed(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,[error])
    else:assert run(store,[error]).status=="failed"
    assert store.records("LeanDraftOutcome") and store.records("LeanDraftProgressProposal")

def test_expected_type_mismatch_and_explicit_incomplete(store):
    seed(store);req=request(store,expected_declaration_type_fingerprints={"target":FALSE_TYPE_FINGERPRINT})
    r=run(store,[*actions(),act("submit_result",accept_unverified=True,reason="Type mismatch remains.")],req=req)
    assert r.status=="partial",r
    assert not r.data["result"]["formal_verification_accepted"]
    assert any(a["status"]=="failed" for a in r.data["attempts"])

def test_execution_disabled_returns_only_explicit_incomplete(store):
    seed(store);r=run(store,[*actions(),act("submit_result",accept_unverified=True,reason="No execution permission.")],allow_execution=False)
    assert r.status=="partial" and not r.data["result"]["formal_verification_accepted"]

def test_exact_read_handles(store):
    seed(store);src=region(store)
    seq=[act("read_regions",region_ids=[src.id]),propose(evidence_ids=[identity({"proof_evidence_region":src.id})]),*actions()[1:]]
    r=run(store,seq,req=request(store,source_region_ids=(src.id,)))
    assert r.status=="partial",r
    assert r.data["result"]["source"]["exact_grounding"]

def test_resolution_and_unauthorized_search(store):
    seed(store);r=run(store,[act("search_lean",query="True"),act("resolve_declaration",declaration="True.intro"),*actions()])
    assert r.status=="partial",r
    assert next(iter(r.data["resolutions"].values()))["resolved"]
    assert not r.data["discoveries"]

@pytest.mark.parametrize("name",["Nat.add;evil","../Init","Nat\n#eval", "Nat.add\"", "Nat add"])
def test_no_lookup_source_injection(name):
    with pytest.raises(ValueError):ResolveLean(declaration=name)

def test_large_source_packet(store):
    seed(store);a=propose();a["arguments"]["modules"][0]["source"] += "\n--"+"x"*40000
    r=run(store,[a,*actions()[1:]])
    assert r.status=="partial",r

def test_real_lean_resolution_and_verification(store,verifier):
    seed(store);r=run(store,[act("resolve_declaration",declaration="Nat.add_comm"),*actions()],req=request(store,verifier),v=verifier)
    assert r.status=="partial",r
    assert r.data["result"]["formal_verification_accepted"]
    assert next(iter(r.data["resolutions"].values()))["resolved"]

from test_lean_project import verifier

def feedback(prompt):
    import json
    return json.loads(prompt["messages"][-1]["content"])

def test_repair_invalidates_receipt_and_requires_reverify(store):
    from nima_semantica.providers import Invocation
    seed(store);calls=[]
    def model(prompt):
        f=feedback(prompt);i=len(calls);calls.append(f)
        if i==0:a=propose()
        elif i in (1,4):a=act("verify_source")
        elif i==2:a=propose(base_source_revision=f["source"]["source_revision"],correction_reason="Use tactic proof.")
        else:a=act("submit_result")
        return Invocation(result=a,manifest=MANIFEST,input_tokens=10,output_tokens=10)
    r=draft_lean(store,request(store),context(),model=model,verifier=Verifier())
    assert r.status=="partial",r
    assert len(calls)==6 and len(r.data["verifications"])==2
    assert not calls[3]["readiness"]["formal_verification_accepted"]
    assert "current_verification" in calls[3]["consequences"]["Blocked"]
    assert r.data["verifications"][0]["source_revision"]!=r.data["verifications"][1]["source_revision"]

def test_real_compiler_diagnostics_drive_repair(store,verifier):
    from nima_semantica.providers import Invocation
    seed(store);observed=[]
    def model(prompt):
        f=feedback(prompt);i=len(observed);observed.append(f)
        if i==0:
            a=propose();a["arguments"]["modules"]=[{"name":"Main","source":"theorem target : True := by rfl"}]
        elif i in (1,3):a=act("verify_source")
        elif i==2:a=propose(base_source_revision=f["source"]["source_revision"],correction_reason="Replace invalid tactic with True.intro.")
        else:a=act("submit_result")
        return Invocation(result=a,manifest=MANIFEST,input_tokens=10,output_tokens=10)
    r=draft_lean(store,request(store,verifier),context(),model=model,verifier=verifier)
    assert r.status=="partial",r
    assert not observed[2]["readiness"]["formal_verification_accepted"]
    assert r.data["result"]["formal_verification_accepted"]
    assert len(r.data["verifications"])==2

def test_environment_drift_fails_closed(store):
    from nima_semantica.providers import Invocation
    seed(store);v=Verifier()
    def model(prompt):
        v.timeout+=1
        return Invocation(result=propose(),manifest=MANIFEST,input_tokens=10,output_tokens=10)
    r=draft_lean(store,request(store,v),context(),model=model,verifier=v)
    assert r.status=="failed" and r.data["error"]=="ConflictError"

def test_configuration_failure_has_outcome(store):
    seed(store)
    def fail():raise ValueError("not configured")
    r=draft_lean(store,request(store),context(),model=Model(actions()),verifier_factory=fail)
    assert r.status=="failed" and store.records("LeanDraftOutcome")

def test_native_retrieval(store):
    from test_source_pipeline import pipeline,request as source_request
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);_,_,prepared=pipeline(store,source_request())
    r=run(store,[act("retrieve_context",query="Claim",purpose="Check target definitions."),*actions()],
        retrieval=MathRetrievalPolicy(enabled=True,projection_id=prepared.data["projection"]["projection_id"]))
    assert r.status=="partial",r
    assert r.data["context_packets"][0]["passages"]

def test_approved_progress(store):
    from test_project_update import request as ur,context as uc,approved
    from nima_semantica.project_update import update_project_graph
    from nima_semantica.okf_contracts import OKFDelta
    seed(store);r=run(store)
    req=ur(store,mode="prepare",delta=None,progress_proposal_ids=(r.data["project_progress"]["record_id"],))
    prep=update_project_graph(store,req,uc());assert prep.status=="complete",prep
    assert update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(OKFDelta.model_validate(prep.data["delta"]))).status=="complete"

def test_authorized_discovery_and_unavailable_service(store):
    seed(store);seen=[]
    def discovery(policy,action):
        seen.append(action.query)
        raise TimeoutError()
    r=draft_lean(store,request(store),context(lean_search=LeanSearchPolicy(enabled=True,allow_query_disclosure=True)),
        model=Model([act("search_lean",query="True.intro"),*actions()]),verifier=Verifier(),discovery=discovery)
    assert r.status=="partial" and seen==["True.intro"]
    assert r.data["discoveries"][0]["status"]=="unavailable"

def test_leansearch_wire_protocol_and_disclosure(monkeypatch):
    import json
    from nima_semantica.lean_search import search_lean
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps([[{"result":{"name":["Nat","add_comm"],"type":"forall a b, a + b = b + a"}},None]]).encode()
    class Opener:
        def open(self,req,timeout):calls.append(json.loads(req.data));return Response()
    monkeypatch.setattr("urllib.request.build_opener",lambda *a:Opener())
    with pytest.raises(ValueError):search_lean(LeanSearchPolicy(enabled=True),SearchLean(query="sum"))
    assert not calls
    p=search_lean(LeanSearchPolicy(enabled=True,allow_query_disclosure=True),SearchLean(query="sum"))
    assert calls==[{"query":["sum"],"num_results":5}]
    assert p["candidates"][0]["name"]=="Nat.add_comm" and not p["candidates"][0]["locally_resolved"]

def test_initial_repair_bundle(store):
    seed(store);req=request(store,mode="repair",initial_modules=({"name":"Main","source":"theorem target : True := True.intro"},),
        initial_root_modules=("Main",))
    r=run(store,actions()[1:],req=req)
    assert r.status=="partial" and r.data["result"]["formal_verification_accepted"]

@pytest.mark.parametrize("failure",[ValueError(),CancelledError()])
def test_nested_verifier_failures_remain_recorded(store,failure):
    seed(store);v=Verifier(error=failure)
    if isinstance(failure,CancelledError):
        with pytest.raises(CancelledError):run(store,v=v)
    else:
        r=run(store,[*actions(),act("submit_result",accept_unverified=True,reason="Verification failed.")],v=v)
        assert not r.data["result"]["formal_verification_accepted"]
    assert store.records("LeanVerificationOutcome") and store.records("LeanDraftOutcome")

def test_forged_handles_rejected_without_overwriting_source(store):
    seed(store);r=run(store,[propose(evidence_ids=["unknown"]),propose(resolution_ids=["a"*64]),*actions()])
    assert r.status=="partial"
    assert sum(a["status"]=="failed" for a in r.data["attempts"])==2


def test_invalid_module_and_handle_namespaces_return_precise_feedback(store):
    seed(store);bad=propose();bad["arguments"]["modules"][0]["name"]="Main.lean"
    model=Model([bad,propose(evidence_ids=["not-evidence"]),*actions()])
    result=draft_lean(store,request(store),context(),model=model,verifier=Verifier())
    assert result.status=="partial",result
    first_tool=next(message for message in model.calls[1]["messages"] if message["role"]=="tool")
    second_tool=[message for message in model.calls[2]["messages"] if message["role"]=="tool"][-1]
    assert "modules.0" in first_tool["content"] and "extensionless" in first_tool["content"]
    assert "selectable_handles.evidence_ids" in second_tool["content"]
    assert sum(item["status"]=="failed" for item in result.data["attempts"])==2


class SelectiveVerifier(Verifier):
    def verify(self,store,request):
        from dataclasses import replace
        result=super().verify(store,request)
        if any("Missing.declaration" in source for source in request.sources.values()):
            return replace(result,compilation_succeeded=False,inspection_succeeded=False,axioms_accepted=False,
                certification_verified=False,kernel_replay_succeeded=False,declaration_types={},declaration_type_fingerprints={},
                diagnostics=("missing declaration",))
        return result


def obligation_request(store,declaration,status,*,citation=False):
    return request(store,declaration_check_obligations=({"obligation_id":"check-"+status,"declaration":declaration,
        "require_discovery":True,"expected_local_status":status,"require_source_citation":citation},))


def discovery_for(declaration):
    return lambda policy,action:{"status":"available","candidates":[{"name":declaration,"locally_resolved":False}],"query":action.query}


def obligation_model(declaration,*,cite):
    calls=[]
    def model(prompt):
        f=feedback(prompt);index=len(calls);calls.append(prompt)
        if index==0:value=act("resolve_declaration",declaration=declaration)
        elif index==1:value=act("search_lean",query=declaration)
        elif index==2:value=act("resolve_declaration",declaration=declaration)
        elif index==3:
            ids=[item["resolution_id"] for item in f["selectable_handles"]["resolution_ids"]]
            value=propose(resolution_ids=ids if cite else [])
        elif index==4:value=act("verify_source")
        else:value=act("submit_result")
        return Invocation(result=value,manifest=MANIFEST,input_tokens=10,output_tokens=10)
    return model,calls


def test_required_available_declaration_discovery_resolution_and_citation(store):
    seed(store);declaration="True.intro";model,calls=obligation_model(declaration,cite=True)
    result=draft_lean(store,obligation_request(store,declaration,"available",citation=True),
        context(lean_search=LeanSearchPolicy(enabled=True,allow_query_disclosure=True)),model=model,verifier=Verifier(),discovery=discovery_for(declaration))
    assert result.status=="partial",result
    assert result.data["result"]["workflow_obligations_satisfied"]
    assert result.data["result"]["source"]["resolution_ids"]
    assert "requires discovery" in next(message for message in calls[1]["messages"] if message["role"]=="tool")["content"]
    assert any(item["status"]=="failed" for item in result.data["attempts"])


def test_required_failed_declaration_resolution_is_retained_and_enforced(store):
    seed(store);declaration="Missing.declaration";model,calls=obligation_model(declaration,cite=False)
    result=draft_lean(store,obligation_request(store,declaration,"unavailable"),
        context(lean_search=LeanSearchPolicy(enabled=True,allow_query_disclosure=True)),model=model,verifier=SelectiveVerifier(),discovery=discovery_for(declaration))
    assert result.status=="partial",result
    obligation=result.data["result"]["declaration_check_obligations"][0]
    assert obligation["satisfied"] and obligation["matching_resolution_ids"]
    resolution=next(iter(result.data["resolutions"].values()))
    assert not resolution["resolved"] and resolution["receipt_id"]


def test_unsatisfied_harness_obligation_blocks_otherwise_verified_submission(store):
    seed(store);req=obligation_request(store,"True.intro","available",citation=True)
    result=run(store,[*actions(),act("submit_result",accept_unverified=True,reason="Skip it.")],req=req,max_actions=4)
    assert result.status=="failed" and result.data["diagnostic"]=="Action limit reached without submission"
    assert any(item["status"]=="failed" for item in result.data["attempts"])

def test_model_cannot_change_targets_or_environment(store):
    seed(store);bad=propose();bad["arguments"]["targets"]=["easier"]
    r=run(store,[bad,*actions()])
    assert r.status=="partial" and r.data["result"]["targets"]==["target"]

def test_no_expected_type_does_not_claim_pinned_correspondence(store):
    seed(store);r=run(store,req=request(store,expected_declaration_type_fingerprints={}))
    assert r.status=="partial" and not r.data["result"]["formal_target_fully_pinned"]
    assert not r.data["result"]["correspondence_verified"]

def test_real_missing_declaration_is_not_resolved(store,verifier):
    seed(store);r=run(store,[act("resolve_declaration",declaration="NimaMissing.theorem"),*actions()],req=request(store,verifier),v=verifier)
    assert r.status=="partial" and not next(iter(r.data["resolutions"].values()))["resolved"]

def test_verification_limit_preserves_incomplete_draft(store):
    seed(store);r=run(store,[*actions(),act("submit_result",accept_unverified=True,reason="No further verifier calls allowed.")],max_verifications=0)
    assert r.status=="partial" and not r.data["result"]["formal_verification_accepted"]

def test_draft_artifact_read_evidence_roundtrip(store):
    from nima_semantica.evidence_reader import ReadEvidenceRequest,ReadEvidenceContext,read_evidence
    seed(store);r=run(store)
    out=read_evidence(store,ReadEvidenceRequest(artifact_id=r.artifacts["draft"],max_bytes=1000000),ReadEvidenceContext(corpus_id="papers",project_id="research"))
    assert out.status=="complete",out
    assert "True.intro" in out.data["content"]
