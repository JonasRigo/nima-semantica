import os
import json
from pathlib import Path

import pytest

from nima_semantica.acquisition import acquire
from nima_semantica.models import AcquisitionPolicy
from nima_semantica.verification import Expression, LeanVerifier, PolynomialClaim


@pytest.mark.parametrize("url", ["http://127.0.0.1/secret", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"])
def test_acquisition_rejects_private_and_non_http_targets(url):
    with pytest.raises(Exception):
        acquire(url, AcquisitionPolicy(enabled=True, domains=("127.0.0.1", "169.254.169.254")))


@pytest.mark.integration
def test_isolated_lean_identity(store):
    image = os.environ.get("NIMA_LEAN_IMAGE")
    runtime = os.environ.get("NIMA_VERIFIER_RUNTIME", "docker")
    toolchain = os.environ.get("NIMA_LEAN_TOOLCHAIN")
    if not image and not (runtime == "bwrap" and toolchain):
        pytest.skip("requires an explicitly configured isolated Lean image")
    variable = Expression(op="variable", name="n")
    claim = PolynomialClaim(variables=("n",), left=variable, right=variable)
    verifier = LeanVerifier(image, Path(toolchain) if toolchain else None, runtime=runtime)
    result = verifier.verify(store, claim, "rfl")
    assert result.outcome == "verified", result.model_dump()
    assert not result.correspondence_verified
    assert result.evidence_artifact
    evidence = json.loads(store.read_artifact(result.evidence_artifact))
    assert evidence["lean_limits"] == ["-j1", "-s8192", "-M512"]
    assert evidence["exit_code"] == 0
    assert evidence["axioms"] == []
    false_claim = claim.model_copy(update={"right": Expression(op="add", left=variable, right=Expression(op="constant", value=1))})
    rejected = verifier.verify(store, false_claim, "rfl")
    assert rejected.outcome == "failed"
    assert rejected.evidence_artifact
