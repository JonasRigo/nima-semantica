import pytest

from nima_semantica.audit import assess
from nima_semantica.models import Assessment, NimaError, VerificationResult
from nima_semantica.proof import ProofPlan
from nima_semantica.verification import Expression, PolynomialClaim, exact_counterexample, lean_source


def test_exact_false_universal_claim(store):
    claim = PolynomialClaim(variables=("n",), left=Expression(op="variable", name="n"), right=Expression(op="constant", value=0))
    result = exact_counterexample(store, claim, {"n": 1})
    assert result.outcome == "refuted"
    assert result.evidence_artifact
    assert not result.correspondence_verified
    assert exact_counterexample(store, claim, {"n": 0}).outcome == "inconclusive"
    with pytest.raises(NimaError):
        exact_counterexample(store, claim, {"n": True})


def test_proof_route_failure_does_not_refute_claim():
    assert assess("claim", {"route": "refuted"}, []) == Assessment.NOT_ESTABLISHED
    result = VerificationResult(target_id="claim", protocol="test", outcome="verified", scope="formal", evidence_artifact="abc")
    assert assess("claim", {}, [result]) == Assessment.NOT_ESTABLISHED
    assert assess("claim", {}, [result.model_copy(update={"correspondence_verified": True})]) == Assessment.VERIFIED


def test_lean_renderer_cannot_admit_or_execute():
    claim = PolynomialClaim(variables=("n",), left=Expression(op="variable", name="n"), right=Expression(op="variable", name="n"))
    source = lean_source(claim, "rfl")
    assert "#print axioms nima_target" in source
    for tactic in ("sorry", "admit", "native_decide", "rfl\n#eval IO.println \"success\""):
        with pytest.raises(NimaError):
            lean_source(claim, tactic)


def test_proof_plan_cycles_and_missing_dependencies():
    with pytest.raises(ValueError, match="cyclic"):
        ProofPlan(target_statement="test", goals={"a": ("b",), "b": ("a",)}, imported_regions=(), unresolved_leaves=()).validate_dependencies()
    with pytest.raises(ValueError, match="unsupported"):
        ProofPlan(target_statement="test", goals={"a": ("missing",)}, imported_regions=(), unresolved_leaves=()).validate_dependencies()
