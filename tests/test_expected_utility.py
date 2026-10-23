"""Finite decision semantics, caller authority and durable audit boundaries."""
from fractions import Fraction
import json

import pytest

from nima_semantica.decision_contracts import (ActionModel, DecisionObservation,
    DecisionRequest, EvaluationContext, InformationValueModel, OutcomeEstimate)
from nima_semantica.expected_utility import ExpectedUtilityService, brier_score
from nima_semantica.models import ConflictError, Record, identity
from nima_semantica.okf_contracts import GraphRevision


def model(gain=5, cost=2):
    return {"basis": {"rationale": "Declared fixture estimate, not measured success."},
        "expected_cost": cost, "outcomes": [{"outcome_id": "success", "probability": "1/2", "utility_gain": gain},
            {"outcome_id": "failure", "probability": "1/2", "utility_gain": 0}]}


def request():
    return DecisionRequest.model_validate({"operation_id": "decision-1", "context": {
        "corpus_id": "papers", "project_id": "research", "state_hash": "a" * 64,
        "target": "Resolve the selected obligation", "graph_revision": {
            "corpus_id": "papers", "project_id": "research", "project_revision": 0}},
        "policy": {"policy_id": "progress", "version": "1", "objective": "Assess useful target progress",
            "utility_unit": "declared progress units", "horizon": "one attempted action",
            "cost_definition": "Supplied expected_cost includes all converted resource costs."},
        "actions": [{"action_id": "proof", "description": "Attempt the lemma", "model": model(10, 2)},
            {"action_id": "read", "description": "Read the definition", "model": model(4, 0)},
            {"action_id": "stop", "description": "Return current partial work", "model": model(0, 0)}]})


def context(req, *, mode="advisory", record=False, statuses=None):
    return EvaluationContext.model_validate({"corpus_id": req.context.corpus_id,
        "project_id": req.context.project_id, "state_hash": req.context.state_hash,
        "graph_revision": req.context.graph_revision.model_dump(), "policy_hash": req.policy.content_hash,
        "mode": mode, "allow_recording": record, "eligibility": [
            {"action_id": action.action_id, "status": (statuses or {}).get(action.action_id, "eligible"),
             "reason": "Explicit caller assessment."} for action in req.actions]})


def revise(req, change):
    data = req.model_dump(mode="json")
    change(data)
    return DecisionRequest.model_validate(data)


def test_exact_rankings_shadow_and_no_execution():
    req = request()
    result = ExpectedUtilityService(context(req)).evaluate(req)
    assert result.best_action_ids == ("proof",) and result.recommended_action_id == "proof"
    assert result.actions[0].expected_utility == 3
    assert result.complete and result.ranking_stable and not result.persisted
    assert result.scientific_status == "uncertified" and result.authority == "advisory_only"
    shadow = ExpectedUtilityService(context(req, mode="shadow")).evaluate(req)
    assert shadow.best_action_ids == ("proof",) and shadow.recommended_action_id is None
    assert ExpectedUtilityService(context(req)).evaluate(req) == result
    assert result.model_dump(mode="json")["actions"][0]["expected_utility"] == "3/1"


def test_permissions_are_not_purchasable_with_utility():
    req = request()
    result = ExpectedUtilityService(context(req, statuses={"proof": "needs_approval"})).evaluate(req)
    assert result.actions[0].expected_utility == 3
    assert result.actions[0].eligibility.status == "needs_approval"
    assert result.recommended_action_id == "read"
    none = ExpectedUtilityService(context(req, statuses={a.action_id: "unassessed" for a in req.actions})).evaluate(req)
    assert not none.complete and not none.best_action_ids and none.recommended_action_id is None


def test_scenarios_expose_rank_reversal_and_unknown_outcomes():
    req = revise(request(), lambda d: d["actions"][0].update(scenarios=[{"scenario_id": "less-promising", "model": model(2, 2)}]))
    result = ExpectedUtilityService(context(req)).evaluate(req)
    assert result.best_action_ids == ("proof",) and not result.ranking_stable
    assert result.actions[0].lower_utility == -1 and result.actions[0].upper_utility == 3
    assert result.recommended_action_id is None
    unknown = revise(request(), lambda d: d["actions"][0]["model"]["outcomes"][0].update(probability=None))
    result = ExpectedUtilityService(context(unknown)).evaluate(unknown)
    assert result.actions[0].expected_utility is None and not result.complete
    assert result.recommended_action_id is None


def test_ties_negative_values_and_stop_alternative():
    tied = revise(request(), lambda d: d["actions"][0].update(model=model(4, 0)))
    result = ExpectedUtilityService(context(tied)).evaluate(tied)
    assert result.best_action_ids == ("proof", "read") and result.recommended_action_id is None
    negative = revise(request(), lambda d: [a.update(model=model(-2, 1)) for a in d["actions"][:2]])
    assert ExpectedUtilityService(context(negative)).evaluate(negative).recommended_action_id == "stop"


def information_model():
    return {"baseline": [{"decision_id": "a", "expected_utility": 5}, {"decision_id": "b", "expected_utility": 5}],
        "observations": [{"observation_id": label, "probability": "1/2", "decisions": [
            {"decision_id": "a", "expected_utility": a}, {"decision_id": "b", "expected_utility": b}]}
            for label, a, b in [("first", 8, 2), ("second", 2, 8)]]}


def test_information_value_is_net_improvement_and_not_double_counted():
    req = revise(request(), lambda d: d["actions"][0].update(model={"information": information_model(),
        "expected_cost": 1, "basis": {"rationale": "Supplied coherent conditional expectations."}}))
    result = ExpectedUtilityService(context(req)).evaluate(req)
    assert result.actions[0].expected_utility == 2  # 8 - 5 - 1
    bad = information_model();bad["baseline"][0]["expected_utility"] = 4
    with pytest.raises(ValueError, match="disagree"):
        InformationValueModel.model_validate(bad)
    with pytest.raises(ValueError, match="never add both"):
        ActionModel.model_validate({**model(), "information": information_model()})


@pytest.mark.parametrize("value", [0.5, True, "NaN", "Infinity", "1/0", "1e1000000", "1" * 129])
def test_malformed_or_inexact_probabilities_rejected(value):
    with pytest.raises(ValueError):
        OutcomeEstimate(outcome_id="a", probability=value, utility_gain=1)


@pytest.mark.parametrize("probability", ["-1/2", "3/2", "1/3"])
def test_probability_domain_and_normalization(probability):
    data = model();data["outcomes"][0]["probability"] = probability
    with pytest.raises(ValueError):
        ActionModel.model_validate(data)


@pytest.mark.parametrize("origin", ["observed", "fitted"])
def test_empirical_labels_need_declared_evidence(origin):
    data = model();data["basis"]["origin"] = origin
    with pytest.raises(ValueError, match="evidence and sample size"):
        ActionModel.model_validate(data)


@pytest.mark.parametrize("field,value", [("allow_recording", True), ("mode", "advisory"), ("code", "execute()")])
def test_proposals_cannot_supply_execution_authority(field, value):
    data = request().model_dump(mode="json");data[field] = value
    with pytest.raises(ValueError):
        DecisionRequest.model_validate(data)


@pytest.mark.parametrize("change", [
    lambda d: d["context"].update(state_hash="b" * 64),
    lambda d: d["context"]["graph_revision"].update(project_revision=1),
    lambda d: d["policy"].update(objective="Replace the user's objective"),
    lambda d: d["actions"].pop(),
])
def test_changed_scope_policy_state_and_eligibility_rejected(change):
    req = request();other = revise(req, change)
    with pytest.raises(ConflictError):
        ExpectedUtilityService(context(req)).evaluate(other)


def test_scope_and_foreign_provenance_rejected():
    req = request()
    other = revise(req, lambda d: (d["context"].update(project_id="private"),
        d["context"]["graph_revision"].update(project_id="private")))
    with pytest.raises(ConflictError, match="scope"):
        ExpectedUtilityService(context(req)).evaluate(other)
    def inject(d):
        d["actions"][0]["model"]["basis"]["evidence"] = [{"corpus_id": "other", "artifact_id": "a" * 64,
            "region_id": "foreign", "source_revision": "a" * 64, "content_hash": "a" * 64}]
    other = revise(req, inject)
    with pytest.raises(ConflictError, match="evidence"):
        ExpectedUtilityService(context(other)).evaluate(other)


def test_recording_is_scoped_idempotent_and_preserves_graph(store):
    req = request();service = ExpectedUtilityService(context(req, record=True), store)
    graph = store.read_okf_snapshot(corpus_id="papers", project_id="research")
    result = service.evaluate(req, record=True)
    revision = store.revision
    assert service.evaluate(req, record=True) == result and store.revision == revision
    assert result.persisted and result.receipt_id
    assert store.read_okf_snapshot(corpus_id="papers", project_id="research") == graph
    assert len(list(store.records("DecisionAssessment", corpus_id="papers", project_id="research"))) == 1
    changed = revise(req, lambda d: d["actions"][0].update(description="Changed input"))
    with pytest.raises(ConflictError):
        service.evaluate(changed, record=True)
    with pytest.raises(PermissionError):
        ExpectedUtilityService(context(req), store).evaluate(req, record=True)


def test_stale_recording_and_atomic_rollback(store, monkeypatch):
    req = request();service = ExpectedUtilityService(context(req, record=True), store)
    monkeypatch.setattr(store, "graph_revision", lambda **kw: GraphRevision(corpus_id="papers", project_id="research", project_revision=1))
    with pytest.raises(ConflictError, match="stale"):
        service.evaluate(req, record=True)
    assert not list(store.records("DecisionAssessment", corpus_id="papers", project_id="research"))
    monkeypatch.setattr(store, "graph_revision", lambda **kw: req.context.graph_revision)
    from nima_semantica.execution_receipts import ExecutionReceiptService
    def fail(*a, **k):raise RuntimeError("receipt write unavailable")
    monkeypatch.setattr(ExecutionReceiptService, "record", fail)
    before = store.revision
    with pytest.raises(RuntimeError):service.evaluate(req, record=True)
    assert store.revision == before
    assert not list(store.records("DecisionAssessment", corpus_id="papers", project_id="research"))


def test_observations_preserve_predictions_and_censoring(store):
    req = request();service = ExpectedUtilityService(context(req, record=True), store)
    result = service.evaluate(req, record=True)
    observation = DecisionObservation(observation_id="observed-1", assessment_id=result.assessment_id,
        action_id="proof", outcome_id="failure", actual_cost="7/2", selection_rationale="Harness selected this route.")
    key = service.observe(observation);before = store.revision
    assert service.observe(observation) == key and store.revision == before
    assert service.evaluate(req, record=True) == result
    assert store.get(key, corpus_id="papers", project_id="research").content["scientific_status"] == "uncertified"
    with pytest.raises(ConflictError):
        service.observe(observation.model_copy(update={"actual_cost": Fraction(4)}))
    with pytest.raises(ConflictError):
        service.observe(observation.model_copy(update={"observation_id": "bad", "action_id": "unknown"}))
    with pytest.raises(ConflictError):
        service.observe(observation.model_copy(update={"observation_id": "bad", "receipt_ids": ("foreign",)}))
    censored = observation.model_copy(update={"observation_id": "censored", "censored": True, "outcome_id": None})
    assert service.observe(censored)
    foreign = req.model_copy(update={"context": req.context.model_copy(update={"project_id": "private",
        "graph_revision": GraphRevision(corpus_id="papers", project_id="private", project_revision=0)})})
    with pytest.raises(ConflictError):
        ExpectedUtilityService(context(foreign, record=True), store).observe(observation)


def test_exact_calibration_and_offline_cli(tmp_path, capsys, monkeypatch):
    assert brier_score({"a": "3/4", "b": "1/4"}, "a") == Fraction(1, 8)
    with pytest.raises(ValueError):brier_score({"a": "1/4"}, "a")
    from nima_semantica.cli import main
    req = request();proposal = tmp_path / "request.json";trusted = tmp_path / "context.json"
    proposal.write_text(req.model_dump_json());trusted.write_text(context(req, mode="shadow").model_dump_json())
    monkeypatch.setenv("NIMA_CONFIG", str(tmp_path / "missing-installation.json"))
    assert main(["decision", "evaluate", str(proposal), "--context", str(trusted)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["best_action_ids"] == ["proof"] and result["recommended_action_id"] is None
    assert not result["persisted"]
    trusted.write_text(context(req, record=True).model_dump_json())
    assert main(["decision", "evaluate", str(proposal), "--context", str(trusted)]) == 2
    assert "offline CLI" in capsys.readouterr().err


def test_complete_input_bound_and_computed_fraction_precision():
    # Sums can need more bits than any individual input; preserve exact output.
    data = request().model_dump(mode="json")
    denominators = [2 ** k - 1 for k in (101, 103, 107, 109, 113, 127)]
    data["actions"][0]["model"]["outcomes"] = [{"outcome_id": str(i), "probability": "1/6",
        "utility_gain": f"1/{denominator}"} for i, denominator in enumerate(denominators)]
    req = DecisionRequest.model_validate(data)
    result = ExpectedUtilityService(context(req)).evaluate(req)
    assert result.actions[0].expected_utility == sum(Fraction(1, d) / 6 for d in denominators) - 2
