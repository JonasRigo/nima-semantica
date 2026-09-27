"""Bound-variable joins must not spend their limit on unrelated fact pairs."""
import itertools
import time

import pytest

from nima_semantica.models import NimaError
from nima_semantica.reasoning_kernel import (
    Atom, Assertion, ContextPolicy, GraphSnapshot, HornRule, Predicate, _matches, infer,
)


def atom(name, *args, negative=False):
    return Atom(predicate=name, arguments=args, negative=negative)


def rule(premises, conclusion):
    return HornRule(name="generic dependency propagation", premises=premises,
        conclusion=conclusion, origin="assumed", justification="Declared test rule")


def dense():
    facts = [atom("Depends", f"row{i}", f"col{j}") for i in range(48) for j in range(12)]
    facts += [atom("Open", f"col{j}") for j in range(12)]
    r = rule((atom("Depends", "?x", "?y"), atom("Open", "?y")), atom("Open", "?x"))
    return facts, r


def test_dense_bound_join_completes_without_raising_limits():
    facts, r = dense()
    matches = list(_matches(r, {a.key:a for a in facts}, 1500, time.monotonic()+10))
    assert len(matches) == 576
    assert {a.arguments[0] for a, _ in matches} == {f"row{i}" for i in range(48)}


def test_dense_graph_semantica_closure_preserves_all_alternative_supports():
    facts, r = dense()
    snapshot = GraphSnapshot(graph_id="dense", context_id="generic", policy=ContextPolicy(allow_assumptions=True,max_matches=1500),
        entities={x:"Item" for a in facts for x in a.arguments},
        predicates=(Predicate(name="Depends",argument_types=("Item","Item")),Predicate(name="Open",argument_types=("Item",))),
        assertions=tuple(Assertion(atom=a,origin="assumed",justification="Fixture fact") for a in facts), rules=(r,))
    report = infer(snapshot)
    assert report.complete, report.diagnostics
    assert len(report.supports) == 576
    assert len([a for a in report.atoms if a.predicate=="Open"]) == 60


@pytest.mark.parametrize("negative", [False, True])
@pytest.mark.parametrize("pattern", [("?x","?x"), ("?x","?y"), ("left","?x"), ("missing","?x")])
def test_indexed_matches_equal_small_exhaustive_join(negative, pattern):
    facts = [atom("Pair", a,b,negative=n) for a,b,n in itertools.product(("left","right"), ("left","right"), (False,True))]
    facts += [atom("Mark", x) for x in ("left","right")]
    r = rule((atom("Pair", *pattern, negative=negative), atom("Mark", "?x")), atom("Result", "?x"))
    expected=set()
    for chosen in itertools.product(facts,repeat=2):
        bindings={};valid=True
        for p,a in zip(r.premises,chosen):
            if (p.predicate,p.negative)!=(a.predicate,a.negative):valid=False;break
            for variable,value in zip(p.arguments,a.arguments):
                if variable.startswith("?"):
                    if bindings.setdefault(variable,value)!=value:valid=False;break
                elif variable!=value:valid=False;break
            if not valid:break
        if valid:expected.add((bindings["?x"],tuple(a.key for a in chosen)))
    actual={(a.arguments[0],s.premises) for a,s in _matches(r,{a.key:a for a in facts},1000,time.monotonic()+10)}
    assert actual==expected


def test_actual_cross_product_and_expired_deadline_still_fail_closed():
    facts=[atom("Mark",f"v{i}") for i in range(20)]
    r=rule((atom("Mark","?x"),atom("Mark","?y")),atom("Pair","?x","?y"))
    for limit,deadline in ((50,time.monotonic()+10),(10000,time.monotonic()-1)):
        with pytest.raises(NimaError,match="budget"):
            list(_matches(r,{a.key:a for a in facts},limit,deadline))
