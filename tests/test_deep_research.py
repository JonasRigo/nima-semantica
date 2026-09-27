import pytest
from pydantic import ValidationError
from nima_semantica.scalar import ScalarCheck, check_boundaries


def example_check(**updates):
    check = {"statement": "strict radical inequality", "variables": [{"name": "q", "lower": 1, "upper": 5}],
        "left": {"op": "sqrt", "left": {"op": "add", "left": {"op": "constant", "value": 1},
            "right": {"op": "div", "left": {"op": "constant", "value": 1}, "right": {"op": "variable", "name": "q"}}}},
        "right": {"op": "sqrt", "left": {"op": "constant", "value": 2}}, "relation": "lt"}
    return ScalarCheck.model_validate({**check, **updates})


def test_exact_radical_boundary_check_finds_equality_endpoint():
    result = check_boundaries(example_check())
    assert result["outcome"] == "counterexample_to_encoded_statement"
    assert result["failures"][0] == {"witness": {"q": 1}, "left": "sqrt(2)", "right": "sqrt(2)"}
    assert result["correspondence_verified"] is False


def test_extra_assumptions_prevent_refutation_claim():
    result = check_boundaries(example_check(assumptions=["q > 1"]))
    assert result["outcome"] == "candidate_witness"


def test_no_sample_failure_is_not_a_proof():
    result = check_boundaries(example_check(relation="le"))
    assert result["outcome"] == "no_counterexample_in_samples"


def test_scalar_rejects_unknown_variable_and_code():
    with pytest.raises(ValidationError):
        example_check(left={"op": "variable", "name": "not_declared"})
    with pytest.raises(ValidationError):
        example_check(left={"op": "eval", "name": "arbitrary code"})


def test_proof_graph_has_a_hard_size_limit():
    from nima_semantica.proof import ProofPlan
    with pytest.raises(ValidationError):
        ProofPlan(target_statement="target", goals={f"g{i}": [] for i in range(25)}, imported_regions=(), unresolved_leaves=())


def test_invalid_domain_point_is_not_a_counterexample():
    result = check_boundaries(example_check(variables=[{"name": "q", "lower": 0, "upper": 1}]))
    assert result["skipped"] == 1
    assert all(point["witness"]["q"] != 0 for point in result["failures"])
