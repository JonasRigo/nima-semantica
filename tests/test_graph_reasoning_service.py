from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_reasoning import GraphReasoningRequest, GraphReasoningService
from nima_semantica.reasoning_kernel import (
    Assertion,
    Atom,
    ContextPolicy,
    GraphSnapshot,
    HornRule,
    InferenceReport,
    Predicate,
    Support,
)


def logical_graph(*, proposed=False):
    base = Atom(predicate="Base", arguments=("x",))
    derived = Atom(predicate="Derived", arguments=("x",))
    assertion = Assertion(
        atom=base,
        origin="proposed" if proposed else "assumed",
        justification="candidate premise" if proposed else "explicit assumption",
    )
    rule = HornRule(
        name="derive",
        premises=(base,),
        conclusion=derived,
        origin="proposed" if proposed else "assumed",
        justification="candidate rule" if proposed else "explicit rule assumption",
    )
    return GraphSnapshot(
        graph_id="research",
        context_id="context",
        policy=ContextPolicy(allow_assumptions=not proposed),
        predicates=(
            Predicate(name="Base", argument_types=("Record",)),
            Predicate(name="Derived", argument_types=("Record",)),
        ),
        entities={"x": "Record"},
        assertions=(assertion,),
        rules=(rule,),
    )


def backend(graph):
    assertion = graph.assertions[0]
    rule = graph.rules[0]
    base = assertion.atom
    derived = rule.conclusion
    return InferenceReport(
        backend="fake-semantic-backend",
        complete=True,
        atoms=(base, derived),
        supports=(Support(conclusion=derived.key, rule_id=rule.id, premises=(base.key,)),),
        seed_supports={base.key: (assertion.id,)},
        blocked=(),
        diagnostics=(),
        rounds=1,
        assessment="consistent_under_assumptions",
    )


def test_strict_mode_excludes_proposed_items_and_replays(store):
    seen = []

    def strict_backend(graph):
        seen.append(tuple(assertion.origin for assertion in graph.assertions))
        return InferenceReport(
            backend="fake-semantic-backend",
            complete=True,
            atoms=(),
            supports=(),
            seed_supports={},
            blocked=(),
            diagnostics=(),
            rounds=1,
            assessment="not_established",
        )

    request = GraphReasoningRequest(
        graph=logical_graph(proposed=True),
        corpus_id="papers",
        project_id="project-a",
        graph_revision=store.graph_revision("papers", "project-a"),
        mode="strict",
        idempotency_key="strict-reasoning-1",
    )
    service = GraphReasoningService(store, backend=strict_backend)
    result = service.execute(request)
    replay = service.execute(request)

    assert result.status == "completed"
    assert result.admitted_conclusions == ()
    assert seen == [()]
    assert replay == result


def test_hypothesizing_mode_marks_derived_conclusions_conditional(store):
    request = GraphReasoningRequest(
        graph=logical_graph(proposed=True),
        corpus_id="papers",
        project_id="project-a",
        graph_revision=store.graph_revision("papers", "project-a"),
        mode="hypothesizing",
        idempotency_key="hypothesis-reasoning-1",
    )
    result = GraphReasoningService(store, backend=backend).execute(request)

    assert result.status == "completed"
    assert len(result.conditional_conclusions) == 2
    assert all(result.hypothesis_ids for _ in result.conditional_conclusions)
    assert all(item.hypothesis_ids for item in result.conditional_conclusions)
    assert result.authority == "read_only_reasoning"
