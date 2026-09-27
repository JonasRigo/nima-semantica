import pytest
from pydantic import ValidationError

from nima_semantica.models import NimaError
from nima_semantica.reasoning_kernel import Atom, Assertion, ContextPolicy, GraphSnapshot, HornRule, Predicate, infer
from nima_semantica.storage import GraphStore


def atom(name, arg="x", negative=False):
    return Atom(predicate=name, arguments=(arg,), negative=negative)


def assertion(name, negative=False, origin="assumed"):
    return Assertion(atom=atom(name, negative=negative), origin=origin, justification="explicit test assumption")


def rule(source, target):
    return HornRule(name=source + target, premises=(atom(source, "?x"),), conclusion=atom(target, "?x"),
                    origin="assumed", justification="explicit test rule assumption")


def snapshot(assertions=None, rules=None, **kwargs):
    return GraphSnapshot(graph_id="test", context_id="context", policy=ContextPolicy(allow_assumptions=True),
        predicates=tuple(Predicate(name=n, argument_types=("Object",)) for n in "ABCD"), entities={"x": "Object"},
        assertions=tuple(assertions if assertions is not None else [assertion("A")]),
        rules=tuple(rules if rules is not None else [rule("A", "B"), rule("B", "C")]), **kwargs)


def test_semantica_closure_and_provenance():
    result = infer(snapshot())
    assert result.complete and not result.blocked
    assert {a.predicate for a in result.atoms} == {"A", "B", "C"}
    assert len(result.supports) == 2
    assert result.assessment == "consistent_under_assumptions"
    assert result.backend.startswith("semantica-0.6.8")


def test_provisional_does_not_become_premise():
    result = infer(snapshot([assertion("A", origin="proposed")]))
    assert not result.atoms and result.assessment == "not_established"
    assert result.diagnostics[0].code == "provisional_knowledge"


def test_contradiction_blocks_dependents_not_independent_support():
    result = infer(snapshot([assertion("A"), assertion("A", negative=True), assertion("D")],
                            [rule("A", "B"), rule("D", "B"), rule("B", "C")]))
    assert atom("A").key in result.blocked
    assert atom("B").key not in result.blocked
    assert atom("C").key not in result.blocked
    assert len([s for s in result.supports if s.conclusion == atom("B").key]) == 2


def test_cycles_do_not_create_proofs():
    assert not infer(snapshot([], [rule("A", "B"), rule("B", "A")])).atoms


def test_unsafe_and_mistyped_rules_rejected():
    with pytest.raises(ValidationError, match="unsafe"):
        HornRule(name="unsafe", premises=(atom("A", "?x"),), conclusion=atom("B", "?y"))
    with pytest.raises(ValidationError, match="undeclared"):
        snapshot([Assertion(atom=atom("Missing"))])
    with pytest.raises(ValidationError, match="context policy"):
        GraphSnapshot.model_validate({**snapshot().model_dump(), "policy": {}})
    with pytest.raises(ValidationError):
        Assertion(atom=atom("A"), origin="verified")


def test_budgets_fail_closed():
    value = snapshot().model_copy(update={"policy": ContextPolicy(allow_assumptions=True, max_rounds=1)})
    report = infer(value)
    assert not report.complete
    assert set(report.blocked) == {a.key for a in report.atoms}
