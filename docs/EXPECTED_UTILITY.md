# Expected utility evaluation

Use NIMA's expected-utility service to compare supplied research actions against an explicit objective, resource-cost convention and evaluation horizon.
The service computes exact finite scores and reports eligibility and uncertainty separately.
The harness chooses the action and retains responsibility for permissions, interpretation and scientific conclusions.

## Run the offline example

From a source checkout, run:

```sh
nima decision evaluate examples/decisions/request.json --context examples/decisions/context.json
```

The command works without an installation configuration, a running server, credentials, provider calls or database access.
It prints a complete JSON assessment to stdout and does not execute a candidate action or write audit records.
The sample is in shadow mode: `proof` has the greatest supplied expected utility, but `recommended_action_id` remains null.
The example numbers are declared judgments for demonstration, not measured success probabilities.

The context file belongs to the caller responsible for the decision.
Keep it separate from model-authored action proposals.
It pins corpus/project identity, graph revision, decision-state hash, policy hash, each action's eligibility and either `shadow` or `advisory` mode.
The service rejects a proposal with a different policy, scope, revision or state, or without matching eligibility coverage.
The offline context must set `allow_recording` to false.

## Define merit and cost

A request contains the target, utility policy and up to 64 candidate actions.
Each action supplies a finite outcome partition, outcome probabilities, utility gains, expected cost and the basis for those estimates.
Use integers or exact strings such as `"1/3"` and `"0.25"`; floating-point JSON values, non-finite values and oversized arithmetic inputs are rejected.
Probabilities must be nonnegative and sum exactly to one when known.
Unknown probabilities or utility gains remain null and leave that action unassessed.
Input files are bounded to 1 MB.

The score is the expected utility gain minus expected cost, in the policy's declared utility unit.
State how time, tokens, money and other relevant costs were converted into that unit in `cost_definition`, and include those costs once in `expected_cost`.
The service does not infer conversion weights or add an undisclosed resource penalty.
A negative scientific result may have positive utility when it rules out an important route or changes a subsequent decision.
Graph size, a favorable review or an operational completion flag does not by itself establish scientific progress.

Every model has an `EstimateBasis` identifying judgment, observation or a fitted estimate, with its rationale and optional exact source references.
Observed/fitted labels require evidence and sample size; fitted estimates also require a calibration scope.
Pure evaluation checks reference scope but does not read or authenticate source bytes.
Authorized persistence authenticates the supplied exact references through NIMA's evidence service.
An authentic quotation still does not establish that a probability estimate is well calibrated.

## Information value and scenario uncertainty

An action may supply an information-value model instead of outcome gains.
The v0.4.0 model supports one observation step and a fixed finite downstream decision set.
Supply baseline expected utilities, observation probabilities and conditional utilities for every downstream decision.
The service checks normalization, identical decision identities and consistency of the conditional expectations with the baseline.
It computes the expected best decision after observation minus the best baseline decision and the action cost.
It rejects an action that also supplies ordinary outcome gains, preventing the same information benefit from being counted twice.

Up to eight explicitly supplied alternative scenarios can accompany each action.
The assessment reports the point score and the minimum/maximum across the supplied models.
These bounds describe those scenarios; they are not confidence intervals or a claim to cover all possible models.
Probability intervals, automatic probability fitting and general multistep research planning are not implemented in v0.4.0.

`best_action_ids` contains the point-estimate maximizers among eligible assessed candidates.
An advisory recommendation is returned only when all eligible candidates and their scenarios are assessed, there is one maximizer, and its lowest supplied score strictly exceeds every competitor's highest supplied score.
Ties, unknown estimates and unstable rankings return no recommendation.
Ineligible actions retain conditional scores but cannot win the eligible ranking.
An action marked `needs_approval` cannot become eligible through a larger score.
Include an explicit stop/return-partial action when it is a relevant alternative; stopping never resolves a mathematical obligation.

## Use the library

```python
from pathlib import Path
from nima_semantica.decision_contracts import DecisionRequest, EvaluationContext
from nima_semantica.expected_utility import ExpectedUtilityService

request = DecisionRequest.model_validate_json(Path("request.json").read_bytes())
context = EvaluationContext.model_validate_json(Path("context.json").read_bytes())
assessment = ExpectedUtilityService(context).evaluate(request)
print(assessment.model_dump_json(indent=2))
```

For audit recording, the existing authorized database owner may construct `ExpectedUtilityService(context, store)` with `allow_recording=True` and invoke `evaluate(request, record=True)`.
The service checks the live graph revision, optional research-run identity and evidence before atomically storing the assessment and an execution receipt.
It creates `DecisionAssessment` records without scientific graph admission or a graph revision change.
Exact replay returns the original assessment; changed arguments under the same scoped operation ID are rejected.
The service does not open another database owner or bypass an existing owner lease.

The owner may record a `DecisionObservation` with `service.observe(observation)`.
The observation identifies the original assessment, selected action, actual outcome or censoring, actual cost, harness rationale and optional same-run execution receipts.
Repeated identical observation IDs replay; divergent feedback, unknown actions/outcomes and foreign receipts are rejected.
Observations preserve original predictions and do not update policies or manufacture outcomes for unchosen alternatives.
The exact `brier_score` helper evaluates a normalized categorical probability model against one observed modeled outcome; censored/unobserved cases need separate treatment.

Start by collecting shadow assessments and comparing predicted outcomes with actual results.
Move to advisory use only under a declared task policy and an evaluated calibration scope.
The v0.4.0 implementation has no empirical research-policy quality certification and adds no public Langflow/MCP endpoint.

[User guide](README.md) · [v0.4.0 release notes](RELEASE_NOTES_v0.4.0.md)
