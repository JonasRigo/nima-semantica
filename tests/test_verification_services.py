from types import SimpleNamespace

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.lean_project import LeanProjectResult
from nima_semantica.lean_verification_service import LeanVerificationRequest, LeanVerificationService
from nima_semantica.models import Record
from nima_semantica.verification_service import VerificationRequest, VerificationService
from nima_semantica.verification import Expression, PolynomialClaim


def claim():
    return PolynomialClaim(
        variables=("n",),
        left=Expression(op="variable", name="n"),
        right=Expression(op="constant", value=0),
    )


def test_verification_service_preserves_counterexample_scope_and_replays(store):
    request = VerificationRequest(
        operation="exact_counterexample",
        corpus_id="papers",
        project_id="project-a",
        claim=claim(),
        witness={"n": 1},
        idempotency_key="verification-1",
    )
    service = VerificationService(store)
    result = service.execute(request)
    replay = service.execute(request)

    assert result.status == "completed"
    assert result.verification.outcome == "refuted"
    assert result.verification.correspondence_verified is False
    assert replay == result


def test_lean_verification_service_records_verified_kernel_result(store):
    class FakeVerifier:
        toolchain = SimpleNamespace(sha256="a" * 64)
        libraries = ()
        trusted_imports = ("Init",)
        timeout = 30
        memory_mb = 1024

        def verify(self, supplied_store, request):
            return LeanProjectResult(
                compilation_succeeded=True,
                inspection_succeeded=True,
                axioms_accepted=True,
                evidence_artifact=supplied_store.artifact(b"lean evidence"),
                certification_verified=True,
                kernel_replay_succeeded=True,
            )

    from conftest import seed_region
    source_region_id = seed_region(store, "theorem target : True := True.intro").id
    request = LeanVerificationRequest(
        sources={"Submission": "theorem target : True := True.intro"},
        targets=("target",),
        imports=("Submission",),
        corpus_id="papers",
        project_id="project-a",
        graph_revision=store.graph_revision("papers", "project-a"),
        proof_target_id="target-node",
        source_region_ids=(source_region_id,),
        idempotency_key="lean-verification-1",
    )
    result = LeanVerificationService(store, FakeVerifier()).execute(request)

    assert result.status == "verified"
    assert result.outcome == "verified"
    assert result.correspondence_verified is False
    attempts = store.records("LeanProofAttempt", corpus_id="papers")
    assert len(attempts) == 1
    assert attempts[0][1].content["authority"] == "verification_result"
