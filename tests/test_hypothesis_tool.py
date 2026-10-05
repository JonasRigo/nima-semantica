"""Hypothesis OSA acceptance with real native storage/evidence and scripted judgments."""
from asyncio import CancelledError
import copy
import pytest
from nima_semantica.hypothesis_contracts import GenerateHypothesesRequest,HypothesisContext
from nima_semantica.hypothesis_tool import generate_hypotheses
from nima_semantica.models import ConflictError
from test_source_pipeline import store
from test_deep_extraction import region,Model
from model_fixture import MANIFEST
from test_claim_dependencies import seed,ref


def context(**kw):return HypothesisContext(**({"corpus_id":"papers","project_id":"research","allow_model_calls":True,"allow_audit_writes":True,"model_manifest":MANIFEST}|kw))
def request(store,r=None,**kw):return GenerateHypothesesRequest(**({"mode":"generate","operation_id":"hypotheses-1","graph_revision":store.graph_revision("papers","research"),
    "objective":"Find a useful, testable local claim.","source_region_ids":(r.id,) if r else ()}|kw))
def proposal(r=None,**kw):return {"name":"propose_hypothesis","arguments":{"candidate_id":"h1","statement":"For every integer x, x squared equals x.",
    "domain":"integers","quantifier":"forall","grounding":"source_grounded" if r else "speculative",
    "rationale":"Investigate the stated pattern, with source applicability unresolved.","supporting":[{"source_id":r.id,"quotation":r.content["text"]}] if r else [],
    "expected_consequences":["The polynomial difference vanishes."],"unresolved_obligations":["Check all integer cases and source correspondence."],
    "verification_plans":["Search for an integer counterexample, then consider proof."],**kw}}
def actions(r=None):return ([{"name":"read_regions","arguments":{"region_ids":[r.id]}}] if r else [])+[proposal(r),
    {"name":"analyze_candidates","arguments":{}},{"name":"submit_result","arguments":{}}]
def run(store,r=None,seq=None,**kw):return generate_hypotheses(store,request(store,r),context(**kw),model=Model(seq or actions(r)))


def test_speculative_alternatives_are_proposals_not_selection_or_graph_admission(store):
    seed(store);req=request(store);head=req.graph_revision;m=Model([proposal(),proposal(candidate_id="h2",statement="There exists an exceptional integer.",quantifier="exists"),*actions()[1:]])
    result=generate_hypotheses(store,req,context(),model=m)
    assert result.status=="partial",result
    out=result.data["result"];assert len(out["proposals"])==2 and out["selected_hypothesis_id"] is None
    assert all(p["status"]=="proposed" and p["authority"]=="proposal_only" for p in out["proposals"])
    assert len(store.records("HypothesisProposal"))==2 and store.graph_revision("papers","research")==head
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert not result.data["reasoning_state"]["publishable"]
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    before=store.revision;assert generate_hypotheses(store,req,context(),model=m)==result and store.revision==before
    with pytest.raises(ConflictError):generate_hypotheses(store,req.model_copy(update={"objective":"Changed"}),context(),model=m)


def test_exact_source_grounding_and_contradicting_passages(store):
    seed(store);r=region(store,"The examples x equals zero and one satisfy the identity.")
    result=run(store,r);assert result.status=="partial",result
    p=result.data["result"]["proposals"][0]
    assert p["evidence"][0]["region_id"]==r.id and p["supporting_references"][0]["content_hash"]==r.content["artifact_id"]
    assert p["model_metadata"]["candidate"]["exact_grounding"][0]["quotation"]==r.content["text"]


def test_preview_and_denial_no_writes(store):
    before=store.revision
    assert generate_hypotheses(None,GenerateHypothesesRequest(),context()).data["executed"] is False
    for flag in ("allow_model_calls","allow_audit_writes"):
        m=Model([]);assert generate_hypotheses(store,request(store),context(**{flag:False}),model=m).status=="failed" and not m.calls
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"private"},{"allow_counterexample_checks":True},{"allow_audit_writes":True},{"max_actions":999},{"worker_url":"evil"},{"model":"other"}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):GenerateHypothesesRequest.model_validate(extra)


@pytest.mark.parametrize("damage",["target","region","revision","prior","run","ontology"])
def test_bad_scope_prevents_model_calls(store,damage):
    seed(store);kw={};ctx=context()
    if damage=="target":kw["graph_targets"]=[{"ref":ref("a","other")}]
    if damage=="region":kw["source_region_ids"]=("missing",)
    if damage=="revision":kw["graph_revision"]=store.graph_revision("papers","other")
    if damage=="prior":kw.update(mode="restate",prior_hypothesis_ids=("missing",))
    if damage=="run":kw["run_id"]="missing"
    if damage=="ontology":kw["artifact_id"]="0"*64
    m=Model([]);result=generate_hypotheses(store,request(store,**kw),ctx,model=m)
    assert result.status=="failed" and not m.calls and store.records("HypothesisProgressProposal")


@pytest.mark.parametrize("damage",["quote","unread","graph","novelty","missing_obligations","review_target"])
def test_invalid_candidate_cannot_publish(store,damage):
    seed(store);r=region(store);seq=actions(r);p=seq[1]["arguments"]
    if damage=="quote":p["supporting"][0]["quotation"]="Invented"
    if damage=="unread":seq=seq[1:]
    if damage=="graph":p["graph_references"]=[{"ref":ref("a").model_dump(mode="json")}]
    if damage=="novelty":p["novelty_verified"]=True
    if damage=="missing_obligations":p["unresolved_obligations"]=[]
    if damage=="review_target":p.update(kind="missing_support",supporting=[],grounding="speculative")
    result=run(store,r,seq,max_actions=len(seq))
    assert result.status=="failed" and not store.records("HypothesisProposal")


def test_repair_preserves_required_assumptions_constraints_and_open_obligations(store):
    seed(store);req=request(store,required_assumptions=("x is an integer",),constraints=[{"constraint_id":"local","text":"Restrict to the stated local problem"}])
    c=[{"constraint_id":"local","assessment":"unresolved","explanation":"Scope correspondence needs review"}]
    first=proposal(constraints=c);revised=proposal(constraints=c,correction_reason="Narrow the conjecture",statement="For x in {0,1}, x squared equals x.",unresolved_obligations=["Check the finite domain."])
    result=generate_hypotheses(store,req,context(),model=Model([proposal(),first,revised,*actions()[1:]]))
    assert result.status=="partial",result
    p=result.data["result"]["proposals"][0]
    assert p["assumptions"]==["x is an integer"] and len(p["unresolved_obligations"])==2
    assert len(result.data["candidate_history"])==2 and any(a["status"]=="failed" for a in result.data["attempts"])


def test_restate_records_prior_provenance_and_reports_duplicate(store):
    seed(store);first=run(store);prior=first.data["result"]["proposals"][0]
    result=generate_hypotheses(store,request(store,mode="restate",operation_id="restate",prior_hypothesis_ids=(prior["proposal_id"],)),context(),model=Model(actions()))
    assert result.status=="partial",result
    assert result.data["result"]["analysis"]["duplicate_checks"][0]["existing_proposal_ids"]==[prior["proposal_id"]]
    assert first.data["result"]["proposal_record_ids"][0] in result.data["result"]["proposals"][0]["provenance"]
    assert not result.data["result"]["novelty_verified"]


@pytest.mark.parametrize("change",["revision","read","retrieval"])
def test_new_evidence_or_revision_invalidates_analysis(store,change):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store);seq=actions(r)
    extra=proposal(r,correction_reason="Reconsider")
    if change=="read":extra=seq[0]
    if change=="retrieval":extra={"name":"retrieve_context","arguments":{"query":"claim obligation","purpose":"Seek counterevidence"}}
    result=run(store,r,[*seq[:3],extra,seq[3]],max_actions=5,retrieval=MathRetrievalPolicy(mode="lexical",enabled=True))
    assert result.status=="failed" and not store.records("HypothesisProposal")


def test_real_retrieval_supplies_unattached_evidence(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seed(store);r=region(store,"Integer examples motivate a conjecture.")
    seq=[{"name":"retrieve_context","arguments":{"query":"integer examples","purpose":"Ground a conjecture"}},*actions(r)[1:]]
    result=run(store,None,seq,retrieval=MathRetrievalPolicy(mode="lexical",enabled=True))
    assert result.status=="partial",result
    assert result.data["result"]["proposals"][0]["evidence"][0]["region_id"]==r.id


def test_probe_is_revision_bound_and_no_witness_is_not_proof(store):
    from test_counterexample_tool import encoding,Worker
    seed(store);probe={"name":"probe_hypothesis","arguments":{"candidate_id":"h1","encoding":encoding(lower=0,upper=1).model_dump(mode="json")}}
    revised=proposal(correction_reason="Reconsider the domain",statement="A restricted identity may hold.")
    result=generate_hypotheses(store,request(store),context(allow_counterexample_checks=True),model=Model([proposal(),probe,revised,*actions()[1:]]),worker=Worker())
    assert result.status=="partial",result
    analysis=result.data["result"]["analysis"]
    assert not analysis["current_probes"] and len(analysis["stale_probe_receipt_ids"])==1
    assert result.data["probes"][0]["result"]["data"]["result"]["outcome"]=="no_witness_in_examined_scope"
    assert not result.data["probes"][0]["encoding_correspondence_verified"]


def test_unauthorized_probe_never_calls_worker(store):
    from test_counterexample_tool import encoding,Worker
    seed(store);worker=Worker();probe={"name":"probe_hypothesis","arguments":{"candidate_id":"h1","encoding":encoding().model_dump(mode="json")}}
    m=Model([proposal(),probe,*actions()[1:]])
    result=generate_hypotheses(store,request(store),context(),model=m,worker=worker)
    assert result.status=="partial" and not worker.calls and not result.data["probes"]
    assert "probe_hypothesis" not in [t["function"]["name"] for t in m.calls[0]["tools"]]


@pytest.mark.parametrize("error",[ValueError("secret"),CancelledError()])
def test_failed_cancelled_outcomes_persist(store,error):
    seed(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store,seq=[error])
    else:
        result=run(store,seq=[error]);assert result.status=="failed" and "secret" not in result.model_dump_json()
    assert store.records("HypothesisOutcome") and store.records("HypothesisProgressProposal") and not store.records("HypothesisProposal")


def test_atomic_proposal_publication_failure_retains_failed_attempt(store,monkeypatch):
    from nima_semantica.proposal_service import ProposalService
    seed(store);original=ProposalService.persist_hypothesis;calls=[]
    def fail(self,proposal,**kw):
        calls.append(proposal)
        if len(calls)==2:raise ValueError("publication failure")
        return original(self,proposal,**kw)
    monkeypatch.setattr(ProposalService,"persist_hypothesis",fail)
    result=run(store,seq=[proposal(),proposal(candidate_id="h2",statement="Another conjecture"),*actions()[1:]])
    assert result.status=="failed" and not store.records("HypothesisProposal")
    assert store.records("HypothesisProgressProposal") and "result" not in result.data


def test_all_withdrawn_retains_history_without_empty_success_claim(store):
    seed(store);result=run(store,seq=[proposal(),proposal(disposition="withdrawn",correction_reason="Not useful"),*actions()[1:]])
    assert result.status=="partial" and not result.data["result"]["proposals"] and result.data["result"]["withdrawn_candidate_ids"]==["h1"]
    assert len(result.data["candidate_history"])==2


def test_hypothesis_progress_can_be_approved_and_recorded(store):
    from test_project_update import request as ur,context as uc,approved
    from nima_semantica.project_update import update_project_graph
    from nima_semantica.okf_contracts import OKFDelta
    seed(store);result=run(store);pid=result.data["project_progress"]["record_id"]
    req=ur(store,mode="prepare",delta=None,progress_proposal_ids=(pid,))
    prep=update_project_graph(store,req,uc());assert prep.status=="complete",prep
    commit=update_project_graph(store,req.model_copy(update={"mode":"commit"}),approved(OKFDelta.model_validate(prep.data["delta"])))
    assert commit.status=="complete" and len(store.records("ProjectProgressCommit"))==1


def test_restatement_cannot_silently_drop_prior_assumptions_or_obligations(store):
    seed(store);first=run(store,seq=[proposal(assumptions=["The selected domain is fixed."]),*actions()[1:]])
    prior=first.data["result"]["proposals"][0]
    result=generate_hypotheses(store,request(store,mode="restate",operation_id="restate-preserve",prior_hypothesis_ids=(prior["proposal_id"],)),context(),
        model=Model([proposal(statement="A narrower alternative.",unresolved_obligations=["Check the reformulation."]),*actions()[1:]]))
    assert result.status=="partial",result
    p=result.data["result"]["proposals"][0]
    assert set(prior["assumptions"])<=set(p["assumptions"])
    assert set(prior["unresolved_obligations"])<set(p["unresolved_obligations"])


@pytest.mark.parametrize("damage",["source","graph","inventory"])
def test_concurrent_changes_prevent_publication(store,monkeypatch,damage):
    from nima_semantica.hypothesis_tool import HypothesisController
    seed(store);r=region(store);original=HypothesisController.submit
    def change(controller):
        if damage=="graph":seed(store,(("a","b"),))
        elif damage=="inventory":controller.existing.append({"record_id":"missing","proposal_id":"missing","statement":"Other"})
        else:
            read=store.read_artifact
            monkeypatch.setattr(store,"read_artifact",lambda key: b"tampered" if key==r.content["artifact_id"] else read(key))
        return original(controller)
    monkeypatch.setattr(HypothesisController,"submit",change)
    result=run(store,r)
    assert result.status=="failed" and not store.records("HypothesisProposal")
    assert store.records("HypothesisProgressProposal")


def test_validated_encoding_witness_retained_without_claim_certification(store):
    from test_counterexample_tool import encoding,Worker
    seed(store);probe={"name":"probe_hypothesis","arguments":{"candidate_id":"h1","encoding":encoding().model_dump(mode="json")}}
    result=generate_hypotheses(store,request(store),context(allow_counterexample_checks=True),model=Model([proposal(),probe,*actions()[1:]]),worker=Worker())
    assert result.status=="partial",result
    found=result.data["result"]["analysis"]["current_probes"][0]
    assert found["result"]["data"]["result"]["outcome"]=="validated_counterexample_to_encoding"
    assert not found["encoding_correspondence_verified"] and not result.data["result"]["mathematically_verified"]


def test_review_concern_has_exact_target_and_provisional_severity(store):
    seed(store);r=region(store);seq=actions(r)
    seq[1]=proposal(r,kind="missing_support",concern_severity="medium",contradicting=[{"source_id":r.id,"quotation":r.content["text"]}])
    result=run(store,r,seq)
    assert result.status=="partial",result
    p=result.data["result"]["proposals"][0]
    assert p["kind"]=="missing_support" and p["contradicting_references"]
    assert p["model_metadata"]["candidate"]["concern_severity"]=="medium"
