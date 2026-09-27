"""Reuse the native verification services; no new verifier or proof authority."""
import pytest

from nima_semantica.calculation import CalculationTask, SymbolicCheckInput
from nima_semantica.lean_project import LeanProjectResult
from nima_semantica.lean_verification_service import LeanVerificationRequest, LeanVerificationService
from nima_semantica.models import identity
from nima_semantica.reasoning_checks import attach_native_check
from nima_semantica.verification_service import VerificationRequest, VerificationService
from test_reasoning_state import state, patch, anchored


def test_existing_symbolic_service_remains_inconclusive_for_source_claim(store):
    class Worker:
        def check(self, request):return {"outcome":"check_passed","scope":"encoded expression only"}
    request=VerificationRequest(operation="symbolic_check",corpus_id="papers",project_id="project-a",
        symbolic=SymbolicCheckInput(task=CalculationTask(corpus_id="papers",project_id="project-a"),candidate="x**3/3"))
    result=VerificationService(store,symbolic_worker=Worker()).execute(request)
    s=state(store);v=patch(s,[anchored(facets={"verification_request_hash":identity(request)})])
    v=attach_native_check(s,base_revision=v["revision"],target="task",receipt_id=result.receipt_id)
    assert v["checks"][0]["result"]["outcome"]=="inconclusive"
    assert v["unresolved_obligations"]==["task"] and not v["correspondence_verified"]


class Verifier:
    def verify(self,store,request):
        return LeanProjectResult(compilation_succeeded=True,inspection_succeeded=True,axioms_accepted=True,
            evidence_artifact=store.artifact(b"kernel replay fixture"),certification_verified=True,kernel_replay_succeeded=True)


@pytest.mark.parametrize("mismatch",[None,"request","environment"])
def test_native_lean_binding_requires_exact_request_and_environment(store,mismatch):
    service=LeanVerificationService(store,Verifier())
    request=LeanVerificationRequest(corpus_id="papers",project_id="project-a",sources={"Main":"theorem target : True := by trivial"},
        imports=("Main",),targets=("target",))
    result=service.execute(request)
    facets=dict(verification_request_hash=service.request_hash(request),
                environment_manifest_hash=identity(service.environment_manifest()))
    if mismatch:facets["verification_request_hash" if mismatch=="request" else "environment_manifest_hash"]="wrong"
    s=state(store);v=patch(s,[anchored(facets=facets)])
    before=store.revision
    if mismatch:
        with pytest.raises(ValueError):attach_native_check(s,base_revision=v["revision"],target="task",receipt_id=result.receipt_id)
        assert store.revision==before
    else:
        v=attach_native_check(s,base_revision=v["revision"],target="task",receipt_id=result.receipt_id)
        assert v["checks"][0]["result"]["outcome"]=="verified"
        assert v["unresolved_obligations"]==["task"], "kernel success must not establish prose correspondence"
        facets["environment_manifest_hash"]="new environment"
        assert patch(s,[anchored(facets=facets)])["checks"][0]["stale"]
