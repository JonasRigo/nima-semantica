"""Native search acceptance: scope, evidence, revisions and honest limited outcomes."""
import ast
import copy
import json
import os
from asyncio import CancelledError
from itertools import islice, product

import pytest

from nima_semantica.addition_contracts import example_claim
from nima_semantica.counterexample_contracts import CounterexampleRequest, CounterexampleContext, IntegerSearch, PlanSearch
from nima_semantica.counterexample_tool import search_counterexamples, counterexample_tools
from nima_semantica.models import ConflictError, identity, Record
from nima_semantica.providers import Invocation
from nima_semantica.verification import holds
from model_fixture import MANIFEST
from test_source_pipeline import store, pipeline, context as source_context


def encoding(**kw):
    return IntegerSearch(**({"claim":example_claim(),"correspondence":"Proposed interpretation of the stated integer identity."}|kw))


def context(**kw):
    return CounterexampleContext(**({"corpus_id":"papers","project_id":"research","allow_execution":True,
        "allow_audit_writes":True,"allow_model_calls":True,"model_manifest":MANIFEST}|kw))


def request(**kw):
    return CounterexampleRequest(**({"mode":"integer","operation_id":"search-test","encoding":encoding()}|kw))


class Worker:
    """Test double reads typed data from the fixed renderer without executing source."""
    def __init__(self, error=None, mutate=None):
        self.calls=[]; self.error=error; self.mutate=mutate

    def run(self, source, timeout):
        self.calls.append(source)
        if self.error is not None:raise self.error
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.Assign) and n.targets[0].id=="task")
        spec=IntegerSearch.model_validate_json(ast.literal_eval(node.value.args[0]))
        visited=eligible=0; witness=None
        for values in islice(product(range(spec.lower,spec.upper+1),repeat=len(spec.claim.variables)),spec.max_evaluations):
            visited+=1; point=dict(zip(spec.claim.variables,values))
            if not all(holds(a,point) for a in spec.assumptions):continue
            eligible+=1
            if not holds(spec.claim,point):witness=point;break
        out={"encoding_id":identity(spec),"witness":witness,"evaluations":visited,"eligible_evaluations":eligible,
            "box_size":(spec.upper-spec.lower+1)**len(spec.claim.variables)}
        if self.mutate:self.mutate(out)
        return dict(outcome="executed",exit_code=0,stdout=json.dumps(out),stderr="")


def actions(spec=None):
    return [{"name":"plan_search","arguments":{"encoding":(spec or encoding()).model_dump(mode="json")}},
        {"name":"search","arguments":{}},{"name":"submit_result","arguments":{}}]


def agent(store, sequence=None, worker=None, seen=None, **kw):
    it=iter(sequence or actions())
    def model(prompt):
        if seen is not None:seen.append(copy.deepcopy(prompt))
        return Invocation(result=next(it),manifest=MANIFEST,input_tokens=10,output_tokens=10)
    return search_counterexamples(store,request(mode="agent",encoding=None),context(**kw),model=model,worker=worker or Worker())


def test_preview_and_permissions_no_side_effects(store):
    assert not search_counterexamples(None,CounterexampleRequest(),context()).data["executed"]
    for field in ("allow_execution","allow_audit_writes","allow_model_calls"):
        before=store.revision; worker=Worker()
        result=search_counterexamples(store,request(mode="agent"),context(**{field:False}),worker=worker)
        assert result.status=="failed" and not worker.calls and store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"foreign"},{"allow_execution":True},{"source":"print(1)"},{"max_actions":999}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):CounterexampleRequest.model_validate(extra)


def test_exact_witness_persists_private_state_artifact_progress_and_replay(store):
    worker=Worker(); graph=store.graph_revision("papers","research")
    result=search_counterexamples(store,request(),context(),worker=worker)
    assert result.status=="partial",result
    found=result.data["result"]
    assert found["outcome"]=="validated_counterexample_to_encoding" and found["search"]["witness"]=={"x":-3}
    assert found["independent_check"]["validated"] and found["verification"]["outcome"]=="refuted"
    assert not found["source_correspondence_verified"]
    assert result.data["reasoning_state"]["inference"]["backend"].startswith("semantica-")
    assert store.graph_revision("papers","research")==graph and not store.records("OKFNode")
    assert store.records("CounterexampleAttemptRevision") and not store.records("MathAttemptRevision")
    assert result.data["project_progress"]["project_recording"]=={"status":"pending","commit_receipt_id":None}
    from nima_semantica.artifact_service import ArtifactService
    envelope=ArtifactService(store).resolve(result.artifacts["outcome"],corpus_id="papers",project_id="research")
    assert envelope.artifact_kind=="counterexample_outcome"
    assert search_counterexamples(store,request(),context(),worker=worker)==result and len(worker.calls)==1
    with pytest.raises(ConflictError):
        search_counterexamples(store,request(statement="Different target"),context(),worker=worker)


@pytest.mark.parametrize("lower,upper,limit,expected",[(0,1,10,True),(0,3,1,False)])
def test_no_witness_reports_exact_coverage_not_truth(store,lower,upper,limit,expected):
    result=search_counterexamples(store,request(encoding=encoding(lower=lower,upper=upper,max_evaluations=limit)),context(),worker=Worker())
    found=result.data["result"]
    assert found["outcome"]=="no_witness_in_examined_scope" and not found["mathematically_verified"]
    assert found["search"]["box_exhausted"] is expected


def test_encoded_assumptions_filter_domain(store):
    from nima_semantica.verification import PolynomialClaim, Expression
    a=PolynomialClaim(variables=("x",),left=example_claim().right,right=Expression(op="constant",value=0),relation="eq")
    result=search_counterexamples(store,request(assumptions=("x equals zero",),encoding=encoding(assumptions=(a,))),context(),worker=Worker())
    assert result.data["result"]["search"]["eligible_evaluations"]==1
    assert result.data["result"]["outcome"]=="no_witness_in_examined_scope"


@pytest.mark.parametrize("mutation",[
    lambda o:o.update(witness={"x":0}),lambda o:o.update(witness={"x":True}),
    lambda o:o.update(witness={"x":99}),lambda o:o.update(encoding_id="invented"),
    lambda o:o.update(evaluations=0),lambda o:o.update(witness={"other":1})])
def test_corrupt_or_invalid_witness_never_finalizes(store,mutation):
    result=search_counterexamples(store,request(),context(),worker=Worker(mutate=mutation))
    assert result.status=="failed" and "result" not in result.data
    assert result.data["project_progress"]["attempt_status"]=="failed"


@pytest.mark.parametrize("error",[ValueError("failure"),CancelledError()])
def test_failed_cancelled_search_has_durable_outcome(store,error):
    worker=Worker(error=error)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):search_counterexamples(store,request(),context(),worker=worker)
    else:assert search_counterexamples(store,request(),context(),worker=worker).status=="failed"
    rows=[r.content for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="search_counterexamples"]
    assert len(rows)==1 and rows[0]["status"]==("interrupted" if isinstance(error,CancelledError) else "failed")
    assert store.records("CounterexampleProgressProposal") and store.records("CounterexampleOutcome")


@pytest.mark.parametrize("change",["revision","target","region","run"])
def test_invalid_scope_or_provenance_prevents_callbacks(store,change):
    kw={"graph_revision":store.graph_revision("papers","foreign")} if change=="revision" else {}
    if change=="target":
        ref=store.put(Record(kind="Claim",corpus_id="papers",project_id="foreign",content={"text":"secret"}))
        kw.update(target_record_id=ref,graph_revision=store.graph_revision("papers","research"))
    if change=="region":kw["context_region_ids"]=("missing",)
    if change=="run":kw["run_id"]="missing"
    worker=Worker()
    result=search_counterexamples(store,request(**kw),context(),worker=worker)
    assert result.status=="failed" and not worker.calls
    assert "secret" not in result.model_dump_json()


def test_agent_loop_uses_current_encoding_and_cannot_submit_old_search(store):
    seq=actions(); changed={"name":"plan_search","arguments":{"encoding":encoding(lower=0,upper=1).model_dump(mode="json"),"revision_reason":"Test a different finite region"}}
    result=agent(store,[seq[0],seq[1],changed,seq[2]],max_actions=4)
    assert result.status=="failed" and "result" not in result.data


def test_agent_can_revise_search_and_preserves_old_witness(store):
    seq=actions(); changed={"name":"plan_search","arguments":{"encoding":encoding(lower=0,upper=1).model_dump(mode="json"),"revision_reason":"Inspect a region with no witness"}}
    result=agent(store,[seq[0],seq[1],changed,seq[1],seq[2]],max_actions=5)
    assert result.status=="partial",result
    assert result.data["result"]["outcome"]=="no_witness_in_examined_scope"
    assert [r["outcome"] for r in result.data["searches"]]==["validated_counterexample_to_encoding","no_witness_in_examined_scope"]


def test_unsupported_is_explicit_and_needs_no_execution(store):
    worker=Worker()
    result=agent(store,[{"name":"plan_search","arguments":{"unsupported_reason":"Requires a non-polynomial operator domain"}},actions()[2]],worker=worker,max_actions=2)
    assert result.status=="partial",result
    assert result.data["result"]["outcome"]=="unsupported" and not worker.calls


def test_optional_retrieval_is_exact_scoped_and_receipted(store):
    _,_,prepared=pipeline(store)
    from nima_semantica.math_retrieval import MathRetrievalPolicy
    seq=actions(); seq[0]["arguments"]["context_needs"]=[{"query":"Claim evidence","purpose":"Locate method context"}]
    result=agent(store,seq,retrieval=MathRetrievalPolicy(enabled=True,projection_id=prepared.data["projection"]["projection_id"]))
    assert result.status=="partial",result
    assert result.data["context_packets"][0]["passages"]
    assert any(a["kind"]=="retrieval" for a in result.data["attempts"])


@pytest.mark.skipif(os.environ.get("NIMA_LIVE_SYMBOLIC")!="1",reason="requires configured isolated worker")
@pytest.mark.parametrize("bounds,outcome",[({},"validated_counterexample_to_encoding"),
    ({"lower":0,"upper":1},"no_witness_in_examined_scope"),
    ({"lower":0,"upper":3,"max_evaluations":1},"no_witness_in_examined_scope")])
def test_actual_isolated_search_and_independent_check(store,bounds,outcome):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    result=search_counterexamples(store,request(encoding=encoding(**bounds)),context(),worker=configured_symbolic_worker())
    assert result.status=="partial",result
    assert result.data["result"]["outcome"]==outcome


@pytest.mark.parametrize("relation,left,right,variables,expected",[
    ("eq",{"op":"constant","value":1},{"op":"constant","value":0},(),"validated_counterexample_to_encoding"),
    ("le",{"op":"constant","value":0},example_claim().left.model_dump(),("x",),"no_witness_in_examined_scope"),
    ("lt",example_claim().left.model_dump(),{"op":"constant","value":0},("x",),"validated_counterexample_to_encoding"),
    ("eq",{"op":"variable","name":"a"},{"op":"variable","name":"b"},("a","b"),"validated_counterexample_to_encoding")])
def test_relations_multiple_variables_and_constant_claim(store,relation,left,right,variables,expected):
    from nima_semantica.verification import PolynomialClaim
    claim=PolynomialClaim(relation=relation,left=left,right=right,variables=variables)
    result=search_counterexamples(store,request(statement="Evaluate the explicitly supplied polynomial encoding.",
        encoding=encoding(claim=claim)),context(),worker=Worker())
    assert result.status=="partial",result
    assert result.data["result"]["outcome"]==expected


@pytest.mark.parametrize("change",[{"domain":"reals"},{"quantifier":"exists"},{"assumptions":("x is positive",)}])
def test_target_domain_quantifier_and_assumptions_cannot_be_silently_narrowed(store,change):
    worker=Worker()
    result=search_counterexamples(store,request(**change),context(),worker=worker)
    assert result.status=="failed" and not worker.calls


def test_operator_evaluation_limit_prevents_worker_calls(store):
    worker=Worker()
    result=search_counterexamples(store,request(),context(max_evaluations=1),worker=worker)
    assert result.status=="failed" and not worker.calls


def test_independent_check_rejects_refutation_outside_assumptions(store):
    from nima_semantica.verification import PolynomialClaim, Expression
    a=PolynomialClaim(variables=("x",),left=example_claim().right,right=Expression(op="constant",value=0),relation="eq")
    worker=Worker(mutate=lambda o:o.update(witness={"x":-3}))
    result=search_counterexamples(store,request(encoding=encoding(assumptions=(a,))),context(),worker=worker)
    assert result.status=="failed" and "result" not in result.data


def test_state_inspection_failure_still_records_terminal_attempt(store,monkeypatch):
    from nima_semantica.counterexample_tool import SearchController
    def fail(self):
        def broken():raise ValueError("inference unavailable")
        monkeypatch.setattr(self.state,"view",broken)
        raise ValueError("inference unavailable")
    monkeypatch.setattr(SearchController,"search",fail)
    result=search_counterexamples(store,request(),context(),worker=Worker())
    assert result.status=="failed" and store.records("CounterexampleOutcome")
    assert result.data["project_progress"]["attempt_status"]=="failed"


def test_initial_sources_and_project_links_are_preserved(store):
    from nima_semantica.evidence_contracts import require_source_region
    _,_,prepared=pipeline(store)
    regions=store.records("SourceRegion",corpus_id="papers",project_id="research")
    region_id=regions[0][0]
    target=store.put(Record(kind="Claim",corpus_id="papers",project_id="research",content={"text":"x squared equals x"}))
    rev=store.graph_revision("papers","research");seen=[]
    sequence=iter(actions())
    def model(prompt):
        seen.append(prompt)
        return Invocation(result=next(sequence),manifest=MANIFEST,input_tokens=1,output_tokens=1)
    result=search_counterexamples(store,request(mode="agent",encoding=None,target_record_id=target,parent_record_id=target,
        graph_revision=rev,context_region_ids=(region_id,)),context(),model=model,worker=Worker())
    assert result.status=="partial",result
    source=json.loads(seen[0]["messages"][1]["content"])["exact_sources"]
    assert source[region_id]==require_source_region(store,region_id,corpus_id="papers",project_id="research").content["text"]
    assert result.data["project_progress"]["target_record_id"]==target
    assert store.graph_revision("papers","research")==rev
