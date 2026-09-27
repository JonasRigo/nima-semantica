"""General-mathematics helpers preserve exact values and admission boundaries."""
import json

import pytest

from nima_semantica.math_interface_helpers import expand_aggregations
from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_indexed_count import IndexedSet
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
from nima_semantica.math_mcp_contracts import invoke


def step(identifier, op, args=(), **kwargs):
    return dict(id=identifier, op=op, args=list(args), meaning=identifier, **kwargs)


def setup_sum(members):
    spec = IndexedSet(set_id="terms", fact_id="terms", members=members, required_paths=("answer",))
    return SingleCalculationGraph("sum given terms", (), ("answer",), (spec,)), spec


@pytest.mark.parametrize("domain,members", [
    ("algebra", ("left", "right")), ("probability", ("success", "failure")),
    ("matrix", ("row_one", "row_two")), ("physics", ("electron", "hole"))])
def test_general_aggregation_compiles_exact_member_steps(domain, members):
    graph, spec = setup_sum(members)
    steps = [step("x", "symbol", value="x"), step("y", "symbol", value="y")]
    values = ["x", "y"]
    if domain == "probability":
        steps = [step("p", "rational", value=[1, 3]), step("q", "rational", value=[2, 3])]
        values = ["p", "q"]
    elif domain == "matrix":
        steps += [step("a", "matrix", ("x", "y"), value=[1, 2]),
            step("b", "matrix", ("y", "x"), value=[1, 2])]
        values = ["a", "b"]
    original = dict(steps=steps, outputs={"answer": "total"},
        aggregations=[dict(id="total", set_id="terms", contributions=dict(zip(members, values)))])
    before = json.dumps(original)
    expanded, repairs = expand_aggregations(original, (spec,))
    plan = compile_graph_program(graph, expanded["steps"], expanded["outputs"], (spec,))
    assert plan["steps"][-1]["index_sum"] == "terms"
    assert [s["index_scope"]["member"] for s in expanded["steps"] if "index_scope" in s] == list(members)
    assert [s["args"][0] for s in expanded["steps"] if "index_scope" in s] == values
    assert json.dumps(original) == before
    assert repairs[0]["contributions"] == dict(zip(members, values))


def test_no_indexing_required_for_ordinary_algebra():
    graph = SingleCalculationGraph("Compute x+x", (), ("answer",))
    args, repairs = expand_aggregations(dict(steps=[step("x", "symbol", value="x"),
        step("twice", "add", ("x", "x"))], outputs={"answer": "twice"}), ())
    assert not repairs
    compile_graph_program(graph, args["steps"], args["outputs"])


@pytest.mark.parametrize("terms", [{"left": "x"}, {"left": "x", "wrong": "x"}, {"left": "x", "right": "missing"}])
def test_missing_forged_or_uncomputed_terms_fail(terms):
    _, spec = setup_sum(("left", "right"))
    with pytest.raises(ValueError):
        expand_aggregations(dict(steps=[step("x", "symbol", value="x")],
            aggregations=[dict(id="total", set_id="terms", contributions=terms)]), (spec,))


def test_helper_cannot_hide_global_cardinality_inside_a_member():
    graph, spec = setup_sum(("left", "right"))
    # Supply a harness fact so the unchanged compiler recognizes the count role.
    graph.nodes["fact:terms"] = {"id": "fact:terms", "status": "task_given", "value": "two terms"}
    args, _ = expand_aggregations(dict(steps=[step("x", "symbol", value="x"),
        step("count", "integer", value=2, provenance=["fact:terms"], index_count_of="terms"),
        step("doubled", "multiply", ("count", "x"))], outputs={"answer": "total"},
        aggregations=[dict(id="total", set_id="terms", contributions={"left": "doubled", "right": "x"})]), (spec,))
    with pytest.raises(ValueError, match="global cardinality"):
        compile_graph_program(graph, args["steps"], args["outputs"], (spec,))


def test_named_references_exact_scope_replay_and_provisional_status(tmp_path):
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"), project_id="p", run_id="r"))
    sid = service.open("Compute", ["answer"])["session_id"]
    producer = invoke(service, "record_step", dict(session_id=sid, request_id="candidate", expected_revision=0,
        kind="claim", statement="Unproved outputs", value={"answer": 2, "other": 3}, depends_on=["task"]))
    ref = {"request_id": "candidate", "output_path": "answer"}
    request = dict(session_id=sid, request_id="submit", expected_revision=1, answer={"answer": 2}, support_nodes={"answer": ref})
    submitted = invoke(service, "submit", request)
    assert not submitted["result"]["admitted"]
    assert submitted["result"]["resolved_references"][0]["node_id"] == producer["result"]["node"]["atomic_nodes"]["answer"]
    assert invoke(service, "submit", request) == submitted
    bad = {**request, "request_id": "bad-path", "support_nodes": {"answer": {**ref, "output_path": "absent"}}}
    assert invoke(service, "submit", bad)["status"] == "rejected"
    other = service.open("Different session", ["answer"])["session_id"]
    assert invoke(service, "submit", {**request, "session_id": other, "expected_revision": 0})["status"] == "rejected"
    assert invoke(service, "submit", {**request, "request_id": "stale", "expected_revision": 0})["status"] == "rejected"
    dependent = invoke(service, "record_step", dict(session_id=sid, request_id="dependent", expected_revision=1,
        kind="claim", statement="Consequence", value=4, depends_on=[ref]))
    assert dependent["result"]["node"]["status"] == "provisional"
    assert dependent["result"]["node"]["depends_on"] == [producer["result"]["node"]["atomic_nodes"]["answer"]]


@pytest.mark.integration
@pytest.mark.parametrize("domain,expected", [("algebra", "x + y"),
    ("probability", "1"), ("matrix", "Matrix([[x + y, x + y]])"), ("physics", "x + y")])
def test_aggregation_executes_and_named_submission_preserves_status(tmp_path, domain, expected):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"),
        project_id="p", run_id="r", allow_execution=True), worker=configured_symbolic_worker())
    members = ["electron", "hole"] if domain == "physics" else ["left", "right"]
    sid = invoke(service, "open", dict(request_id="open", task="Sum supplied terms x and y; p = 1/3, q = 2/3.", required_paths=["answer"],
        task_facts=[dict(fact_id="terms", kind="condition", quote="supplied terms", required_paths=["answer"])],
        indexed_sets=[dict(set_id="terms", fact_id="terms", members=members, required_paths=["answer"])]))["session_id"]
    steps = [step("x", "symbol", value="x"), step("y", "symbol", value="y")]
    values = ["x", "y"]
    if domain == "probability":
        steps = [step("p", "rational", value=[1, 3], provenance=["task"], raw_definition={"evidence_id": "task", "quote": "p = 1/3"}),
            step("q", "rational", value=[2, 3], provenance=["task"], raw_definition={"evidence_id": "task", "quote": "q = 2/3"})]
        values = ["p", "q"]
    elif domain == "matrix":
        steps += [step("a", "matrix", ("x", "y"), value=[1, 2]), step("b", "matrix", ("y", "x"), value=[1, 2])]
        values = ["a", "b"]
    calculated = invoke(service, "run_calculation_graph", dict(session_id=sid, request_id="sum", expected_revision=0,
        steps=steps, outputs={"answer": "total"}, purpose="General aggregation acceptance",
        aggregations=[dict(id="total", set_id="terms", contributions=dict(zip(members, values)))]))
    assert calculated["status"] == "completed", calculated
    assert calculated["result"]["output_nodes"]["answer"]["value"] == expected
    assert calculated["result"]["interface_normalizations"]
    # Convenience changes linking only: named and explicit support produce the
    # same admission decision, including any unresolved numeric assumptions.
    request = dict(session_id=sid, expected_revision=calculated["revision"], answer={"answer": expected})
    named = invoke(service, "submit", dict(**request, request_id="named", support_nodes={"answer": {"request_id": "sum", "output_path": "answer"}}))
    direct = invoke(service, "submit", dict(**request, request_id="direct", support_nodes={"answer": calculated["result"]["output_nodes"]["answer"]["id"]}))
    assert named["result"]["admitted"] == direct["result"]["admitted"]
    assert not named["result"]["mathematically_verified"]
