"""Offline OSA extraction acceptance with real source, ontology, graph and artifact services."""
from asyncio import CancelledError
import copy
import json
import pytest
from nima_semantica.extraction_contracts import DeepExtractionRequest,DeepExtractionContext
from nima_semantica.deep_extraction_tool import deep_extraction
from nima_semantica.models import Record,ConflictError
from nima_semantica.providers import Invocation
from model_fixture import MANIFEST
from test_source_pipeline import store,pipeline,request as source_request
from conftest import seed_region


def context(**kw):return DeepExtractionContext(**({"corpus_id":"papers","project_id":"research","allow_audit_writes":True,"allow_model_calls":True,"model_manifest":MANIFEST}|kw))
def region(store,text="A claim requires an obligation."):
    prepared,_,_=pipeline(store,source_request(operation_id="prepare-"+str(store.revision),sources=[{"name":"claim.txt","text":text}]))
    return store.get(prepared.data["region_ids"][0])
def request(r,**kw):return DeepExtractionRequest(**({"mode":"regional","operation_id":"extract-test","source_region_ids":(r.id,)}|kw))
def proposal(r):
    quote={"source_id":r.id,"quotation":r.content["text"]}
    return {"name":"propose_graph","arguments":{"nodes":[
        {"node_id":"claim","node_type":"claim","text":"A claim","anchors":[quote]},
        {"node_id":"obligation","node_type":"obligation","text":"An obligation","anchors":[quote]}],
        "edges":[{"edge_id":"requires","relation":"requires","source_id":"claim","target_id":"obligation","anchors":[quote]}],
        "coverage":[{"region_id":r.id,"status":"extracted","reason":"Both statements and their relation are proposed."}]}}
def actions(r):return [{"name":"read_regions","arguments":{"region_ids":[r.id]}},proposal(r),
    {"name":"analyze_graph","arguments":{}},{"name":"submit_result","arguments":{}}]
class Model:
    def __init__(self,sequence):self.sequence=iter(sequence);self.calls=[]
    def __call__(self,prompt):
        self.calls.append(copy.deepcopy(prompt));value=next(self.sequence)
        if isinstance(value,BaseException):raise value
        return Invocation(result=value,manifest=MANIFEST,input_tokens=10,output_tokens=10)


def test_preview_denial_no_reads_model_or_writes(store):
    before=store.revision
    assert not deep_extraction(None,DeepExtractionRequest(),context()).data["executed"]
    req=DeepExtractionRequest(mode="regional",operation_id="denied",source_region_ids=("missing",))
    model=Model([])
    for permission in ("allow_audit_writes","allow_model_calls"):
        assert deep_extraction(store,req,context(**{permission:False}),model=model).status=="failed"
    assert not model.calls and store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"other"},{"model":"evil"},{"allow_audit_writes":True},{"commit":True},{"max_actions":999}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):DeepExtractionRequest.model_validate(extra)


def test_real_native_proposal_private_osa_and_replay(store):
    r=region(store);model=Model(actions(r));revision=store.graph_revision("papers","research")
    result=deep_extraction(store,request(r),context(),model=model)
    assert result.status=="partial",result
    graph=result.data["result"]["graph_proposal"]["artifact"]["delta"]
    assert {n["status"] for n in graph["upsert_nodes"]}=={"proposed"}
    assert graph["upsert_nodes"][0]["properties"]["extraction_grounding"][0]["quotation"]==r.content["text"]
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert not result.data["reasoning_state"]["publishable"] and store.records("ExtractionAttemptRevision")
    assert store.graph_revision("papers","research")==revision and not store.records("OKFNode")
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    assert not result.data["result"]["source_fidelity_verified"]
    from nima_semantica.artifact_service import ArtifactService
    assert ArtifactService(store).resolve(result.artifacts["graph_proposal"],corpus_id="papers",project_id="research").artifact_kind=="graph_candidate"
    assert deep_extraction(store,request(r),context(),model=model)==result and len(model.calls)==4
    with pytest.raises(ConflictError):deep_extraction(store,request(r,question="Changed scope"),context(),model=model)


@pytest.mark.parametrize("mode",["document","document_to_proposal"])
def test_prepared_document_modes_select_exact_regions(store,mode):
    r=region(store);result=deep_extraction(store,request(r,mode=mode,source_id=r.content["source_id"],source_region_ids=()),context(),model=Model(actions(r)))
    assert result.status=="partial",result
    assert result.data["result"]["coverage"][0]["region_id"]==r.id


@pytest.mark.parametrize("change",["region","project","revision","run","ontology","document"])
def test_invalid_scope_and_unprepared_sources_prevent_model_calls(store,change):
    r=region(store);kw={}
    if change=="region":kw["source_region_ids"]=("missing",)
    if change=="project":
        foreign=seed_region(store,"private",project_id="foreign");kw["source_region_ids"]=(foreign.id,)
    if change=="revision":kw["graph_revision"]=store.graph_revision("papers","foreign")
    if change=="run":kw["run_id"]="missing"
    if change=="ontology":kw["ontology_profile"]="missing"
    if change=="document":kw.update(mode="document",source_id="missing",source_region_ids=())
    model=Model([]);result=deep_extraction(store,request(r,**kw),context(),model=model)
    assert result.status=="failed" and not model.calls
    assert store.records("ExtractionOutcome") and "private" not in result.model_dump_json()


@pytest.mark.parametrize("damage",["quote","endpoint","ontology","coverage","unread","duplicates"])
def test_invalid_proposals_cannot_finalize(store,damage):
    r=region(store);seq=actions(r);p=seq[1]["arguments"]
    if damage=="quote":p["nodes"][0]["anchors"][0]["quotation"]="invented text"
    if damage=="endpoint":p["edges"][0]["target_id"]="invented"
    if damage=="ontology":p["nodes"][0]["node_type"]="invented"
    if damage=="coverage":p["coverage"][0]["region_id"]="invented"
    if damage=="duplicates":p["nodes"][1]["node_id"]="claim"
    if damage=="unread":seq=seq[1:]
    result=deep_extraction(store,request(r),context(max_actions=len(seq)),model=Model(seq))
    assert result.status=="failed" and "graph_proposal" not in result.artifacts,result
    assert not store.records("GraphArtifactProposal")


def test_agent_can_repair_rejection_with_actionable_diagnostics(store):
    r=region(store);seq=actions(r);bad=copy.deepcopy(seq[1]);bad["arguments"]["nodes"][0]["anchors"][0]["quotation"]="invented"
    model=Model([seq[0],bad,*seq[1:]])
    result=deep_extraction(store,request(r),context(),model=model)
    assert result.status=="partial",result
    assert "Quotation" in json.dumps(model.calls[2]["messages"])
    assert any(a["status"]=="failed" for a in result.data["attempts"])


def test_new_candidate_invalidates_old_analysis(store):
    r=region(store);seq=actions(r);revised=copy.deepcopy(seq[1]);revised["arguments"]["correction_reason"]="Reconcile wording"
    revised["arguments"]["nodes"][0]["text"]="An attributed claim"
    result=deep_extraction(store,request(r),context(max_actions=5),model=Model([*seq[:3],revised,seq[3]]))
    assert result.status=="failed" and "result" not in result.data
    assert len(result.data["candidate_history"])==2


@pytest.mark.parametrize("error",[ValueError("secret"),CancelledError()])
def test_failed_cancelled_model_retains_attempt_and_progress(store,error):
    r=region(store)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):deep_extraction(store,request(r),context(),model=Model([error]))
    else:
        result=deep_extraction(store,request(r),context(),model=Model([error]));assert result.status=="failed"
        assert "secret" not in result.model_dump_json()
    assert store.records("ExtractionProgressProposal")
    rows=[r for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="deep_extraction"]
    assert rows[0].content["status"]==("interrupted" if isinstance(error,CancelledError) else "failed")


def test_explicit_unread_deferral_is_partial_not_complete_coverage(store):
    r=region(store);p={"name":"propose_graph","arguments":{"coverage":[{"region_id":r.id,"status":"deferred","reason":"Insufficient remaining scope"}]}}
    result=deep_extraction(store,request(r),context(),model=Model([p,*actions(r)[2:]]))
    assert result.status=="partial",result
    assert "coverage" in result.data["reasoning_state"]["consequences"]["Open"]
    assert result.data["result"]["coverage"][0]["status"]=="deferred"


def test_real_ingestion_retrieval_read_extraction_ensemble(store):
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    r=region(store);seq=actions(r)
    model=Model([seq[0],{"name":"retrieve_context","arguments":{"query":"claim obligation","purpose":"Clarify the dependency vocabulary"}},*seq[1:]])
    result=deep_extraction(store,request(r),context(retrieval=MathRetrievalPolicy(enabled=True)),model=model)
    assert result.status=="partial",result
    packet=result.data["context_packets"][0]
    assert packet["passages"] and packet["projection_revision"]
    assert packet["passages"][0]["text"]==r.content["text"]
    assert "context" in result.data["reasoning_state"]["items"]["candidate"]["depends_on"]


def test_retrieval_permission_cannot_be_invented_by_agent(store):
    r=region(store);model=Model([{"name":"retrieve_context","arguments":{"query":"claim","purpose":"background"}}])
    result=deep_extraction(store,request(r),context(max_actions=1),model=model)
    assert result.status=="failed" and not result.data["context_packets"]
    assert "retrieve_context" not in [t["function"]["name"] for t in model.calls[0]["tools"]]


def test_retrieved_regions_cannot_replace_selected_source_quotations(store):
    r=region(store);other=region(store,"Outside the selected document.");seq=actions(r)
    seq[1]["arguments"]["nodes"][0]["anchors"]=[{"source_id":other.id,"quotation":other.content["text"]}]
    result=deep_extraction(store,request(r),context(max_actions=4),model=Model(seq))
    assert result.status=="failed" and not store.records("GraphArtifactProposal")


def test_issue_history_cannot_disappear_or_be_self_certified(store):
    r=region(store);seq=actions(r);initial=copy.deepcopy(seq[1])
    initial["arguments"]["issues"]=[{"issue_id":"ambiguity","description":"Necessity is ambiguous"}]
    missing=copy.deepcopy(seq[1]);missing["arguments"]["correction_reason"]="Reworded claim"
    resolved=copy.deepcopy(initial);resolved["arguments"]["correction_reason"]="Proposed reading"
    resolved["arguments"]["issues"][0].update(status="resolution_proposed",resolution_reason="Explicit dependency wording")
    result=deep_extraction(store,request(r),context(),model=Model([seq[0],initial,missing,resolved,*seq[2:]]))
    assert result.status=="partial" and len(result.data["candidate_history"])==2,result
    assert result.data["result"]["issues"][0]["status"]=="resolution_proposed"
    assert any(k.startswith("issue_") for k in result.data["reasoning_state"]["consequences"]["Open"])


def test_publication_failure_rolls_back_candidate_but_records_failure(store,monkeypatch):
    from nima_semantica.proposal_service import ProposalService
    r=region(store)
    def fail(*a,**kw):raise ValueError("simulated write failure")
    monkeypatch.setattr(ProposalService,"persist_graph_candidate",fail)
    result=deep_extraction(store,request(r),context(),model=Model(actions(r)))
    assert result.status=="failed" and not store.records("GraphArtifactProposal")
    receipts={row.content["receipt_id"]:row for _,row in store.records("ExecutionReceipt")}
    assert all(ref in receipts for ref in result.receipt_ids)
    assert any(row.content["stage"]=="graph_extraction" and row.content["status"]=="failed" for row in receipts.values())
    assert not any(row.content.get("artifact_kind")=="graph_candidate" for _,row in store.records("ArtifactEnvelope"))


def test_registry_failure_does_not_advertise_unstarted_native_receipt(store,monkeypatch):
    from nima_semantica import deep_extraction_tool as module
    r=region(store);original=module._revision;calls=[]
    def fail_once(*a,**kw):
        calls.append(True)
        if len(calls)==1:raise ValueError("simulated registry conflict")
        return original(*a,**kw)
    monkeypatch.setattr(module,"_revision",fail_once)
    result=deep_extraction(store,request(r),context(),model=Model(actions(r)))
    assert result.status=="failed" and not store.records("GraphArtifactProposal")
    ids={row.content["receipt_id"] for _,row in store.records("ExecutionReceipt")}
    assert set(result.receipt_ids)<=ids


def test_ambiguous_quote_requires_exact_offsets(store):
    r=region(store,"A claim. A claim.");seq=actions(r)
    for node in seq[1]["arguments"]["nodes"]:node["anchors"]=[{"source_id":r.id,"quotation":"A claim."}]
    corrected=copy.deepcopy(seq[1])
    for node in corrected["arguments"]["nodes"]:node["anchors"][0].update(start=0,end=8)
    result=deep_extraction(store,request(r),context(),model=Model([seq[0],seq[1],corrected,*seq[2:]]))
    assert result.status=="partial" and len(result.data["candidate_history"])==1,result


def test_stale_graph_during_model_call_prevents_candidate_publication(store):
    from unittest.mock import Mock
    r=region(store);model=Model(actions(r));original=store.graph_revision
    def invoke(prompt):
        value=model(prompt)
        store.graph_revision=Mock(return_value=original("papers","foreign"))
        return value
    result=deep_extraction(store,request(r),context(),model=invoke)
    assert result.status=="failed" and not store.records("GraphArtifactProposal")


@pytest.mark.parametrize("owner",["research","foreign"])
def test_saved_ontology_identity_and_scope_drive_vocabulary(store,owner):
    from nima_semantica.ontology_tools import save_ontology,SaveOntologyRequest,OntologyContext
    saved=save_ontology(store,SaveOntologyRequest(mode="save",operation_id="custom-profile",profile={
        "name":"experimental","version":"1.0.0","node_types":[{"name":"Observation","description":"An attributed observation"}],
        "relation_types":[{"name":"Supports","source_types":["Observation"],"target_types":["Observation"],"description":"Proposed support"}]}),
        OntologyContext(corpus_id="papers",project_id=owner,allow_writes=True))
    assert saved.status=="complete"
    r=region(store);seq=actions(r)
    for node in seq[1]["arguments"]["nodes"]:node["node_type"]="observation"
    seq[1]["arguments"]["edges"][0]["relation"]="supports"
    model=Model(seq);result=deep_extraction(store,request(r,ontology_profile=saved.data["digest"]),context(),model=model)
    if owner=="foreign":assert result.status=="failed" and not model.calls
    else:
        assert result.status=="partial",result
        assert result.data["ontology_digest"]==saved.data["digest"]
        schema=next(t["function"]["parameters"] for t in model.calls[0]["tools"] if t["function"]["name"]=="propose_graph")
        assert schema["$defs"]["ExtractionNode"]["properties"]["node_type"]["enum"]==["observation"]


def test_dependency_cycles_remain_diagnostics_not_logical_certification(store):
    r=region(store);seq=actions(r)
    edge=copy.deepcopy(seq[1]["arguments"]["edges"][0]);edge.update(edge_id="reverse",source_id="obligation",target_id="claim")
    seq[1]["arguments"]["edges"].append(edge)
    result=deep_extraction(store,request(r),context(),model=Model(seq))
    assert result.status=="partial",result
    assert result.data["result"]["analysis"]["dependency_cycle_witnesses"]
    assert not result.data["result"]["analysis"]["semantic_conflicts_resolved"]


def test_document_reconciliation_preserves_all_region_coverage(store):
    text="\n\n".join("# Section "+str(i)+"\n"+"A claim requires an obligation. "*35 for i in range(3))
    prepared,_,_=pipeline(store,source_request(sources=[{"name":"multi.md","text":text}]))
    ids=prepared.data["region_ids"];assert 1<len(ids)<=4
    r=store.get(ids[0]);seq=actions(r)
    seq[0]["arguments"]["region_ids"]=ids
    seq[1]["arguments"]["coverage"].extend({"region_id":ref,"status":"no_relevant_content","reason":"Repeated statement; no additional proposed entities"} for ref in ids[1:])
    result=deep_extraction(store,request(r,mode="document",source_region_ids=(),source_id=r.content["source_id"]),context(),model=Model(seq))
    assert result.status=="partial",result
    assert {c["region_id"] for c in result.data["result"]["coverage"]}==set(ids)
    assert len([v for v in result.data["reasoning_state"]["items"].values() if v["kind"]=="region"])==len(ids)
