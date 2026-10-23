"""Offline expected-utility CLI; separate proposal and caller context files."""
from pathlib import Path

from .decision_contracts import DecisionRequest, EvaluationContext
from .expected_utility import ExpectedUtilityService


def _read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("decision input exceeds complete-read bound")
    return raw


def evaluate_command(args):
    request = DecisionRequest.model_validate_json(_read(args.request))
    context = EvaluationContext.model_validate_json(_read(args.context))
    if context.allow_recording:
        raise ValueError("offline CLI does not open a store; set allow_recording to false")
    return ExpectedUtilityService(context).evaluate(request).model_dump(mode="json")


def register_commands(commands):
    decision = commands.add_parser("decision", help="Evaluate supplied research actions without model calls or execution")
    actions = decision.add_subparsers(dest="action", required=True)
    evaluate = actions.add_parser("evaluate", help="Compute exact advisory merit under a separate caller-owned context")
    evaluate.add_argument("request", help="DecisionRequest JSON file")
    evaluate.add_argument("--context", required=True, help="Trusted EvaluationContext JSON file")
