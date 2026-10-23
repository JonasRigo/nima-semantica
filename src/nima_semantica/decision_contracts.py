"""Exact, bounded contracts for advisory expected-utility evaluation."""
from __future__ import annotations

from fractions import Fraction
import re
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, PlainSerializer, model_validator

from .models import StrictModel, identity
from .okf_contracts import Digest, EvidenceReference, GraphIdentifier, GraphRevision


def _exact(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Fraction)):
        raise ValueError("exact values require integers or decimal/rational strings, never floats")
    if isinstance(value, str):
        if len(value) > 128 or not re.fullmatch(r"[+-]?\d+(?:/\d+|\.\d+)?", value):
            raise ValueError("invalid or oversized exact value")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise ValueError("invalid exact value") from None
    if max(abs(result.numerator).bit_length(), result.denominator.bit_length()) > 512:
        raise ValueError("exact value exceeds arithmetic bound")
    return result


Exact = Annotated[Fraction, BeforeValidator(_exact), PlainSerializer(
    lambda value: f"{value.numerator}/{value.denominator}", return_type=str)]
ComputedExact = Annotated[Fraction, PlainSerializer(
    lambda value: f"{value.numerator}/{value.denominator}", return_type=str)]


def _unique(items, field):
    values = [getattr(item, field) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field}")


class EstimateBasis(StrictModel):
    origin: Literal["judgment", "observed", "fitted"] = "judgment"
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=16)
    sample_size: int | None = Field(default=None, strict=True, ge=1)
    calibration_scope: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def empirical_basis(self):
        if self.origin != "judgment" and (not self.evidence or self.sample_size is None):
            raise ValueError("empirical estimates require evidence and sample size")
        if self.origin == "fitted" and not self.calibration_scope:
            raise ValueError("fitted estimates require a calibration scope")
        return self


class UtilityPolicy(StrictModel):
    policy_id: GraphIdentifier
    version: GraphIdentifier
    objective: str = Field(min_length=1, max_length=4000)
    utility_unit: str = Field(min_length=1, max_length=128)
    horizon: str = Field(min_length=1, max_length=2000)
    cost_definition: str = Field(min_length=1, max_length=2000,
        description="How all resource costs were converted into the utility unit.")

    @property
    def content_hash(self):
        return identity(self)


class OutcomeEstimate(StrictModel):
    outcome_id: GraphIdentifier
    probability: Exact | None
    utility_gain: Exact | None

    @model_validator(mode="after")
    def probability_domain(self):
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise ValueError("probability must be between zero and one")
        return self


class DecisionValue(StrictModel):
    decision_id: GraphIdentifier
    expected_utility: Exact


class InformationObservation(StrictModel):
    observation_id: GraphIdentifier
    probability: Exact
    decisions: tuple[DecisionValue, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def valid(self):
        _unique(self.decisions, "decision_id")
        if not 0 <= self.probability <= 1:
            raise ValueError("probability must be between zero and one")
        return self


class InformationValueModel(StrictModel):
    """One information step with a fixed finite downstream decision set."""
    baseline: tuple[DecisionValue, ...] = Field(min_length=1, max_length=16)
    observations: tuple[InformationObservation, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def coherent(self):
        _unique(self.baseline, "decision_id")
        _unique(self.observations, "observation_id")
        if sum(o.probability for o in self.observations) != 1:
            raise ValueError("information probabilities must sum exactly to one")
        baseline = {d.decision_id: d.expected_utility for d in self.baseline}
        for observation in self.observations:
            if {d.decision_id for d in observation.decisions} != set(baseline):
                raise ValueError("information model requires the same downstream decisions")
        for key, value in baseline.items():
            expected = sum(o.probability * next(d.expected_utility for d in o.decisions
                if d.decision_id == key) for o in self.observations)
            if expected != value:
                raise ValueError("conditional utilities disagree with baseline expectation")
        return self


class ActionModel(StrictModel):
    outcomes: tuple[OutcomeEstimate, ...] = Field(default=(), max_length=32)
    information: InformationValueModel | None = None
    expected_cost: Exact = Fraction(0)
    basis: EstimateBasis

    @model_validator(mode="after")
    def valid(self):
        if self.expected_cost < 0:
            raise ValueError("resource cost must be nonnegative")
        if self.information is not None and self.outcomes:
            raise ValueError("choose expected utility or information value, never add both")
        _unique(self.outcomes, "outcome_id")
        known = [o.probability for o in self.outcomes if o.probability is not None]
        total = sum(known)
        if total > 1 or self.outcomes and len(known) == len(self.outcomes) and total != 1:
            raise ValueError("outcome probabilities must sum exactly to one when known")
        return self


class ActionScenario(StrictModel):
    scenario_id: GraphIdentifier
    model: ActionModel


class ActionCandidate(StrictModel):
    action_id: GraphIdentifier
    description: str = Field(min_length=1, max_length=4000)
    model: ActionModel
    scenarios: tuple[ActionScenario, ...] = Field(default=(), max_length=8)
    prerequisites: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def scenario_ids(self):
        _unique(self.scenarios, "scenario_id")
        return self


class DecisionContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    state_hash: Digest
    target: str = Field(min_length=1, max_length=4000)
    run_id: GraphIdentifier | None = None
    evidence_cutoff: str | None = Field(default=None, max_length=128)
    vocabulary_hash: Digest | None = None

    @model_validator(mode="after")
    def scoped(self):
        if (self.graph_revision.corpus_id, self.graph_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("decision graph revision scope differs")
        return self


class DecisionRequest(StrictModel):
    operation_id: GraphIdentifier
    context: DecisionContext
    policy: UtilityPolicy
    actions: tuple[ActionCandidate, ...] = Field(min_length=1, max_length=64)
    candidate_limitations: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def actions_unique(self):
        _unique(self.actions, "action_id")
        return self


class ActionEligibility(StrictModel):
    action_id: GraphIdentifier
    status: Literal["eligible", "needs_approval", "unavailable", "unassessed"]
    reason: str = Field(min_length=1, max_length=2000)


class EvaluationContext(StrictModel):
    """Caller-owned authority; never merged from an action proposal."""
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    state_hash: Digest
    policy_hash: Digest
    eligibility: tuple[ActionEligibility, ...] = Field(min_length=1, max_length=64)
    mode: Literal["shadow", "advisory"] = "shadow"
    allow_recording: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def scoped(self):
        _unique(self.eligibility, "action_id")
        if (self.graph_revision.corpus_id, self.graph_revision.project_id) != (self.corpus_id, self.project_id):
            raise ValueError("evaluation graph revision scope differs")
        return self


class ActionAssessment(StrictModel):
    action_id: GraphIdentifier
    eligibility: ActionEligibility
    expected_utility: ComputedExact | None
    lower_utility: ComputedExact | None
    upper_utility: ComputedExact | None
    scenario_values: dict[str, ComputedExact | None]
    assessed: bool


class DecisionAssessment(StrictModel):
    schema_version: Literal[1] = 1
    assessment_id: Digest
    input_hash: Digest
    policy_hash: Digest
    evaluator_version: str
    mode: Literal["shadow", "advisory"]
    actions: tuple[ActionAssessment, ...]
    best_action_ids: tuple[GraphIdentifier, ...]
    recommended_action_id: GraphIdentifier | None
    ranking_stable: bool
    complete: bool
    persisted: bool = False
    receipt_id: Digest | None = None
    limitations: tuple[str, ...]
    authority: Literal["advisory_only"] = "advisory_only"
    scientific_status: Literal["uncertified"] = "uncertified"


class DecisionObservation(StrictModel):
    observation_id: GraphIdentifier
    assessment_id: Digest
    action_id: GraphIdentifier
    selection_rationale: str = Field(min_length=1, max_length=4000)
    outcome_id: GraphIdentifier | None = None
    actual_cost: Exact | None = None
    censored: bool = Field(default=False, strict=True)
    receipt_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def observed_domain(self):
        if self.actual_cost is not None and self.actual_cost < 0:
            raise ValueError("actual cost must be nonnegative")
        if not self.censored and self.outcome_id is None:
            raise ValueError("an uncensored observation requires an outcome")
        return self
