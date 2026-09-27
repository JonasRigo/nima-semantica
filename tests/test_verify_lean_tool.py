"""Native exact-source Lean tool boundary; verifier doubles are explicitly offline."""
from asyncio import CancelledError
from types import SimpleNamespace
from dataclasses import replace,asdict
import copy
import hashlib
import json
import os
import pytest

from nima_semantica.verify_lean_tool import VerifyLeanRequest,VerifyLeanContext,verify_lean
from nima_semantica.lean_project import LeanProjectResult
from nima_semantica.lean_verification_service import LeanVerificationService
from nima_semantica.models import Record,ConflictError,canonical
from test_source_pipeline import store

TRUE_TYPE_FINGERPRINT="f5d8a8840f30c2d0e5b651a20789cb4c8d616d4ba312057d224114a13a157b90"
FALSE_TYPE_FINGERPRINT="60e335d985a24e16439acf4497bf438125a4c9401d179635e0486267b2199628"


class Verifier:
    toolchain=SimpleNamespace(sha256="a"*64)
    libraries=();trusted_imports=("Init",);timeout=30;memory_mb=1024;output_bytes=1048576;artifact_bytes=16777216
    def __init__(self,error=None,changes=None):self.calls=[];self.error=error;self.changes=changes or {}
    def verify(self,store,request):
        self.calls.append(copy.deepcopy(request))
        if self.error:raise self.error
        sources={n:store.artifact(s.encode()) for n,s in request.sources.items()}
        result=LeanProjectResult(True,True,True,"",source_artifacts=sources,
            certification_verified=True,kernel_replay_succeeded=True,
            axioms={n:() for n in request.targets},declaration_types={n:"Lean.Expr.const `True []" for n in request.targets},
            declaration_type_fingerprints={n:TRUE_TYPE_FINGERPRINT for n in request.targets})
        result=replace(result,**self.changes)
        evidence={**asdict(result),"sources":sources,"targets":list(request.targets),"imports":list(request.imports),
            "toolchain":self.toolchain.sha256,"libraries":[],"trusted_imports":["Init"],
            "allowed_axioms":["propext","Classical.choice","Quot.sound"]}
        return replace(result,evidence_artifact=store.artifact(canonical(evidence)))


def request(**kw):return VerifyLeanRequest(**({"mode":"verify","operation_id":"lean-test"}|kw))
def context(**kw):return VerifyLeanContext(**({"corpus_id":"papers","project_id":"research","allow_execution":True,"allow_audit_writes":True}|kw))


def test_preview_and_denied_execution_are_side_effect_free(store,monkeypatch):
    def forbidden():pytest.fail("preview or denied execution must not configure worker")
    monkeypatch.setattr("nima_semantica.lean_transport.configured_lean_verifier",forbidden)
    before=store.revision
    assert not verify_lean(None,VerifyLeanRequest(),context()).data["executed"]
    for field in ("allow_execution","allow_audit_writes"):
        assert verify_lean(store,request(),context(**{field:False})).status=="failed"
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"corpus_id":"foreign"},{"allow_execution":True},{"worker_url":"http://evil"},
    {"toolchain":"/host"},{"trusted_imports":["Evil"]},{"allowed_axioms":["sorryAx"]},{"model":"LLM"},{"retrieval":True}])
def test_public_authority_fields_rejected(extra):
    with pytest.raises(ValueError):VerifyLeanRequest.model_validate(extra)


@pytest.mark.parametrize("extra",[{"sources":{"../bad":"x"}},{"targets":["target;evil"]},
    {"module_order":["Unknown"]},{"imports":["Missing"]},{"proof_target_id":"x"}])
def test_invalid_targets_modules_and_unversioned_refs_rejected(extra):
    with pytest.raises(ValueError):request(**extra)


def test_exact_submission_receipts_artifact_replay_and_pending_progress(store):
    verifier=Verifier();original=request();revision=store.graph_revision("papers","research")
    result=verify_lean(store,original,context(),verifier=verifier)
    assert result.status=="complete",result
    assert result.data["formal_verification_accepted"] and not result.data["correspondence_verified"]
    assert verifier.calls[0].sources==original.sources
    assert len(store.records("LeanProofAttempt"))==1
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    assert store.graph_revision("papers","research")==revision and not store.records("OKFNode")
    from nima_semantica.artifact_service import ArtifactService
    assert ArtifactService(store).resolve(result.artifacts["outcome"],corpus_id="papers",project_id="research").artifact_kind=="lean_verification_outcome"
    assert verify_lean(store,original,context(),verifier=verifier)==result and len(verifier.calls)==1
    with pytest.raises(ConflictError):verify_lean(store,request(sources={"Submission":"theorem target : True := by trivial"}),context(),verifier=verifier)


@pytest.mark.parametrize("field",["compilation_succeeded","inspection_succeeded","axioms_accepted","kernel_replay_succeeded","certification_verified"])
def test_all_kernel_acceptance_conditions_required(store,field):
    result=verify_lean(store,request(),context(),verifier=Verifier(changes={field:False}))
    assert result.status!="complete" and not result.data["formal_verification_accepted"]


def test_expected_target_type_mismatch_is_not_accepted(store):
    result=verify_lean(store,request(expected_declaration_type_fingerprints={"target":FALSE_TYPE_FINGERPRINT}),context(),verifier=Verifier())
    assert result.status=="partial" and result.data["expected_type_mismatches"]==["target"]
    assert not result.data["formal_verification_accepted"]


@pytest.mark.parametrize("change",[{"source_artifacts":{}},{"declaration_types":{"another":"type"}}])
def test_incorrect_worker_binding_fails_closed(store,change):
    result=verify_lean(store,request(),context(),verifier=Verifier(changes=change))
    assert result.status=="failed" and not result.data["formal_verification_accepted"]


@pytest.mark.parametrize("failure",[ValueError("do not expose secret"),CancelledError()])
def test_failed_and_cancelled_outcomes_persist(store,failure):
    if isinstance(failure,CancelledError):
        with pytest.raises(CancelledError):verify_lean(store,request(),context(),verifier=Verifier(error=failure))
    else:
        result=verify_lean(store,request(),context(),verifier=Verifier(error=failure))
        assert result.status=="failed" and "do not expose secret" not in result.model_dump_json()
    assert store.records("LeanProofAttempt") and store.records("LeanVerificationProgressProposal")
    rows=[r for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="verify_lean"]
    assert rows[0].content["status"]==("interrupted" if isinstance(failure,CancelledError) else "failed")


@pytest.mark.parametrize("ref",["proof_target_id","parent_node_ids","definition_node_ids","source_region_ids","run_id","graph_revision"])
def test_foreign_invalid_scope_prevents_verifier(store,ref):
    foreign=store.put(Record(kind="Claim",corpus_id="papers",project_id="elsewhere",content={"text":"private"}))
    kwargs={"graph_revision":store.graph_revision("papers","research")}
    kwargs[ref]=(foreign,) if ref.endswith("ids") else store.graph_revision("papers","elsewhere") if ref=="graph_revision" else foreign
    worker=Verifier();result=verify_lean(store,request(**kwargs),context(),verifier=worker)
    assert result.status=="failed" and not worker.calls
    assert "private" not in result.model_dump_json()


def test_module_order_and_environment_are_replay_identity(store):
    worker=Verifier();sources={"A":"theorem helper : True := True.intro","B":"import A\ntheorem target : True := helper"}
    req=request(sources=sources,imports=("B",))
    assert verify_lean(store,req,context(),verifier=worker).status=="complete"
    with pytest.raises(ConflictError):verify_lean(store,request(sources=sources,imports=("B",),module_order=("B","A")),context(),verifier=worker)
    worker.output_bytes=2048
    with pytest.raises(ConflictError):verify_lean(store,req,context(),verifier=worker)
    assert len(worker.calls)==1


def test_native_service_also_binds_order_and_resource_limits(store):
    from nima_semantica.lean_verification_service import LeanVerificationRequest
    sources={"A":"theorem helper : True := True.intro","B":"import A\ntheorem target : True := helper"}
    req=LeanVerificationRequest(sources=sources,targets=("target",),imports=("B",),corpus_id="papers",project_id="research",operation_id="native-order")
    worker=Verifier();service=LeanVerificationService(store,worker)
    assert service.execute(req).status=="verified"
    reverse=req.model_copy(update={"sources":dict(reversed(list(sources.items())))})
    with pytest.raises(ConflictError):service.execute(reverse)
    worker.artifact_bytes=2048
    with pytest.raises(ConflictError):service.execute(req)


def test_cancelled_worker_configuration_has_durable_outer_outcome(store,monkeypatch):
    def cancelled():raise CancelledError()
    monkeypatch.setattr("nima_semantica.lean_transport.configured_lean_verifier",cancelled)
    with pytest.raises(CancelledError):verify_lean(store,request(),context())
    rows=[r for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="verify_lean"]
    assert rows[0].content["status"]=="interrupted"
    assert store.records("LeanVerificationProgressProposal")


@pytest.mark.skipif(os.environ.get("NIMA_LIVE_LEAN")!="1",reason="requires pinned isolated Lean worker")
@pytest.mark.parametrize("source,accepted",[("theorem target : True := True.intro",True),
    ("theorem target : False := by sorry",False),("axiom cheat : False\ntheorem target : False := cheat",False),
    ("theorem target : False := by rfl",False)])
def test_real_pinned_lean_tool(store,source,accepted):
    result=verify_lean(store,request(sources={"Submission":source}),context())
    assert result.data["formal_verification_accepted"] is accepted,result
    assert result.data["verification"]["project_result"]["source_artifacts"]==result.data["source_sha256"]
    assert not result.data["correspondence_verified"]
