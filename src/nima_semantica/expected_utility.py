"""Deterministic finite EUM; recommendations never execute research actions."""
from __future__ import annotations

from fractions import Fraction

from .decision_contracts import (ActionAssessment, DecisionAssessment, DecisionObservation,
    DecisionRequest, EvaluationContext)
from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, Record, canonical, identity
from .receipts import ExecutionReceipt

VERSION = "expected-utility-v1"
LIMITATIONS = (
    "Scores are conditional on supplied candidates, estimates, objective and horizon.",
    "Scenario bounds cover only supplied scenarios; they are not statistical confidence intervals.",
    "Recorded outcomes concern selected actions; unchosen alternatives remain unobserved.",
    "Eligibility and utility do not approve execution or establish scientific validity.",
)


def model_utility(model):
    """Exact finite expected gain or coherent one-step information value."""
    if model.information is not None:
        info = model.information
        return sum(o.probability * max(d.expected_utility for d in o.decisions)
            for o in info.observations) - max(d.expected_utility for d in info.baseline) - model.expected_cost
    if not model.outcomes or any(o.probability is None or o.utility_gain is None for o in model.outcomes):
        return None
    return sum(o.probability * o.utility_gain for o in model.outcomes) - model.expected_cost


class ExpectedUtilityService:
    """Evaluate locally; optionally record through the caller's existing owner."""

    def __init__(self, context: EvaluationContext, store=None):
        self.context = EvaluationContext.model_validate(context.model_dump(mode="json"))
        self.store = store

    @property
    def scope(self):
        return {"corpus_id": self.context.corpus_id, "project_id": self.context.project_id}

    def _validate(self, request):
        request = DecisionRequest.model_validate(request.model_dump(mode="json"))
        if len(canonical(request)) > 1_000_000:
            raise ValueError("decision request exceeds complete-read bound")
        ctx = self.context
        if (request.context.corpus_id, request.context.project_id) != (ctx.corpus_id, ctx.project_id):
            raise ConflictError("decision scope differs from authorized evaluation context")
        if request.context.graph_revision != ctx.graph_revision or request.context.state_hash != ctx.state_hash:
            raise ConflictError("decision revision or state differs from pinned context")
        if request.policy.content_hash != ctx.policy_hash:
            raise ConflictError("decision utility policy differs from authorized policy")
        if {a.action_id for a in request.actions} != {a.action_id for a in ctx.eligibility}:
            raise ConflictError("every candidate requires an explicit caller-owned eligibility assessment")
        for action in request.actions:
            for model in (action.model, *(s.model for s in action.scenarios)):
                for ref in model.basis.evidence:
                    if ref.corpus_id != ctx.corpus_id or ref.project_id not in (None, ctx.project_id):
                        raise ConflictError("estimate evidence is outside the authorized scope")
        return request

    def _assessment(self, request, input_hash, *, persisted=False, receipt_id=None):
        eligibility = {v.action_id: v for v in self.context.eligibility}
        values = []
        for action in request.actions:
            score = model_utility(action.model)
            scenarios = {s.scenario_id: model_utility(s.model) for s in action.scenarios}
            known = score is not None and all(v is not None for v in scenarios.values())
            bounds = [score, *scenarios.values()] if known else []
            values.append(ActionAssessment(action_id=action.action_id, eligibility=eligibility[action.action_id],
                expected_utility=score, lower_utility=min(bounds) if bounds else None,
                upper_utility=max(bounds) if bounds else None, scenario_values=scenarios, assessed=known))
        eligible = [v for v in values if v.eligibility.status == "eligible"]
        complete = bool(eligible) and all(v.assessed for v in eligible)
        known = [v for v in eligible if v.expected_utility is not None]
        best = max((v.expected_utility for v in known), default=None)
        winners = tuple(sorted(v.action_id for v in known if v.expected_utility == best))
        stable = complete and len(winners) == 1 and all(
            next(v.lower_utility for v in eligible if v.action_id == winners[0]) > v.upper_utility
            for v in eligible if v.action_id != winners[0])
        recommendation = winners[0] if self.context.mode == "advisory" and stable else None
        return DecisionAssessment(assessment_id=identity((VERSION, input_hash)), input_hash=input_hash,
            policy_hash=self.context.policy_hash, evaluator_version=VERSION, mode=self.context.mode,
            actions=tuple(values), best_action_ids=winners, recommended_action_id=recommendation,
            ranking_stable=stable, complete=complete, persisted=persisted, receipt_id=receipt_id,
            limitations=(*LIMITATIONS, "Evidence references are supplied provenance; persistence authenticates them.",
                *request.candidate_limitations))

    def evaluate(self, request: DecisionRequest, *, record=False):
        request = self._validate(request)
        input_hash = identity({"request": request, "context": self.context, "version": VERSION})
        if not record:
            return self._assessment(request, input_hash)
        if not self.context.allow_recording or self.store is None:
            raise PermissionError("decision recording requires an authorized existing store owner")
        receipt_id = identity((VERSION, request.operation_id, self.scope))
        receipts = ExecutionReceiptService(self.store)
        with self.store.joined_transaction():
            previous = receipts.replay(receipt_id, **self.scope, request_hash=input_hash)
            if previous is not None:
                return DecisionAssessment.model_validate(previous.metadata["assessment"])
            if self.store.graph_revision(**self.scope) != self.context.graph_revision:
                raise ConflictError("decision graph revision is stale")
            if request.context.run_id is not None:
                from .research_run_service import ResearchRunService
                if ResearchRunService(self.store).get_run(request.context.run_id, **self.scope) is None:
                    raise ConflictError("decision run is unavailable in this scope")
            from .evidence_contracts import validate_reference
            for action in request.actions:
                for model in (action.model, *(s.model for s in action.scenarios)):
                    for ref in model.basis.evidence:
                        if validate_reference(self.store, ref, **self.scope, target_id=action.action_id):
                            raise ConflictError("decision evidence authentication failed")
            result = self._assessment(request, input_hash, persisted=True, receipt_id=receipt_id)
            record_id = self.store.put(Record(kind="DecisionAssessment", **self.scope,
                content={"assessment_id": result.assessment_id, "assessment": result.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"), "evaluation_context": self.context.model_dump(mode="json")}))
            receipts.record(ExecutionReceipt(receipt_id=receipt_id, operation_id=request.operation_id,
                stage="expected_utility", **self.scope, run_id=request.context.run_id,
                output_ids=(record_id,), tool_version=VERSION,
                metadata={"request_hash": input_hash, "assessment": result.model_dump(mode="json")}))
            return result

    def observe(self, observation: DecisionObservation):
        observation = DecisionObservation.model_validate(observation.model_dump(mode="json"))
        if not self.context.allow_recording or self.store is None:
            raise PermissionError("decision feedback requires authorized recording")
        with self.store.joined_transaction():
            matches = [(key, record) for key, record in self.store.records_matching(
                "DecisionAssessment", "assessment_id", observation.assessment_id, **self.scope)
                if record.project_id == self.context.project_id]
            if len(matches) != 1:
                raise ConflictError("assessment is unavailable in this project")
            parent_id, parent = matches[0]
            request = DecisionRequest.model_validate(parent.content["request"])
            action = next((a for a in request.actions if a.action_id == observation.action_id), None)
            if action is None:
                raise ConflictError("observed action was not an assessed candidate")
            outcomes = ({o.outcome_id for o in action.model.outcomes} if action.model.information is None else
                {o.observation_id for o in action.model.information.observations})
            if observation.outcome_id is not None and observation.outcome_id not in outcomes:
                raise ConflictError("observed outcome is outside the declared action model")
            receipts = ExecutionReceiptService(self.store)
            for identifier in observation.receipt_ids:
                receipt = receipts.get(identifier, **self.scope)
                if receipt is None or receipt.project_id != self.context.project_id or receipt.run_id != request.context.run_id:
                    raise ConflictError("outcome receipt is unavailable in this scoped run")
            payload = observation.model_dump(mode="json")
            receipt_id = identity((VERSION, "observation", observation.observation_id, self.scope))
            request_hash = identity(payload)
            previous = receipts.replay(receipt_id, **self.scope, request_hash=request_hash)
            if previous is not None:
                return previous.output_ids[0]
            record_id = self.store.put(Record(kind="DecisionObservation", **self.scope, parents=(parent_id,),
                content={**payload, "authority": "recorded_harness_observation", "scientific_status": "uncertified"}))
            receipts.record(ExecutionReceipt(receipt_id=receipt_id, operation_id=observation.observation_id,
                stage="expected_utility_observation", **self.scope, run_id=request.context.run_id,
                input_ids=(parent_id,), output_ids=(record_id,), tool_version=VERSION,
                metadata={"request_hash": request_hash}))
            return record_id


def brier_score(probabilities, outcome_id):
    """Exact multiclass score for one observed categorical outcome."""
    from .decision_contracts import OutcomeEstimate
    outcomes = tuple(OutcomeEstimate(outcome_id=k, probability=v, utility_gain=0) for k, v in probabilities.items())
    if outcome_id not in probabilities or any(o.probability is None for o in outcomes) or sum(o.probability for o in outcomes) != 1:
        raise ValueError("Brier score requires a normalized model and an observed modeled outcome")
    return sum((o.probability - int(o.outcome_id == outcome_id)) ** 2 for o in outcomes)
