"""Standalone regressions for the maintained graph implementation."""
import copy
import json
from pathlib import Path
import runpy

import pytest
from nima_semantica.math_calculation_assistance import source_span
from nima_semantica.math_mcp_contracts import invoke
from nima_semantica.math_session_service import MathSessionService, MathServiceConfig
from nima_semantica.math_single_graph_state import SingleCalculationGraph

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def service(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    return MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"),
        project_id="p", run_id="r", allow_execution=True), worker=configured_symbolic_worker())


def call(service, sid, rid, op, **args):
    return invoke(service, op, dict(session_id=sid, request_id=rid,
        expected_revision=service.read(sid)["revision"], **args))


def test_exact_span_unicode_and_scope():
    g = SingleCalculationGraph("task", (), ("answer",))
    node = g.record_source("source", "α\nA = B + C.\nβ", "rev")
    before = copy.deepcopy(g.export())
    selected = source_span(g, node["id"], {"start":2,"end":12})
    assert selected["quote"] == "A = B + C."
    assert selected["provenance"] == node["origin"]
    for span in ({"start":-1,"end":2}, {"start":0,"end":99}, {"start":True,"end":2}):
        with pytest.raises(ValueError):
            source_span(g, node["id"], span)
    with pytest.raises(ValueError):
        source_span(g, "foreign", {"start":0,"end":3})
    assert g.export() == before


def test_selected_quote_reaches_unchanged_applicability_gate():
    from types import SimpleNamespace
    g = SingleCalculationGraph("task", (), ("answer",))
    source = g.record_source("s", "α\nThis exact method has stated assumptions.\nβ", "revision")
    root = g._put("calculation_operation", "2", "observed", [], {}, application_reason="declared_physical_rule")
    g.obligations[root["id"]] = {"root_id":root["id"],"status":"open"}
    evidence = g._put("calculation_output", "2", "observed", [], {})
    args = dict(operation_id=root["id"],evidence_id=evidence["id"],rationale="Try source",method_source_id=source["id"],
        method_span={"start":2,"end":42})
    # The span passes exact quotation binding, but cannot invent operator/source lineage.
    with pytest.raises(ValueError) as failure:
        MathSessionService._dispatch(g,SimpleNamespace(),"substantiate_application",args)
    assert "exact method-source quote" not in str(failure.value)
    assert g.obligations[root["id"]]["status"] == "open"
    args["method_quote"] = "Different text"
    with pytest.raises(ValueError,match="conflicts"):
        MathSessionService._dispatch(g,SimpleNamespace(),"substantiate_application",args)


@pytest.mark.integration
@pytest.mark.symbolic
def test_identical_and_missing_reconstruction_coverage(service):
    sid = service.open("Compute", ["answer"])["session_id"]
    explored = call(service,sid,"e","run_experiment",source="a = 1\nb = a + a\nprint(b)",depends_on=["task"],purpose="Explore")
    result = call(service,sid,"r","run_calculation_graph",reconstructs_receipt=explored["result"]["receipt_id"],
        steps=[dict(id="a",op="integer",value=1,meaning="unit"),dict(id="b",op="add",args=["a","a"],meaning="sum")],
        outputs={"answer":"b"},purpose="Reconstruct")
    comparison = result["result"]["reconstruction_comparison"]
    assert len(comparison["comparisons"]) == 2
    assert comparison["first_difference"] is None
    assert all(c["status"] == "identical" for c in comparison["comparisons"])
    assert "not" in result["result"]["reconstruction"]["authority"]


@pytest.mark.integration
@pytest.mark.symbolic
def test_reuse_preserves_atomic_matrix_and_unresolved_assumption(service):
    sid = service.open("Compute x", ["answer"])["session_id"]
    plan = [dict(id="one",op="integer",value=1,meaning="unit"),
        dict(id="zero",op="integer",value=0,meaning="zero"),
        dict(id="neg",op="negative",args=["one"],meaning="negative unit"),
        dict(id="matrix",op="matrix",args=["one","zero","zero","neg"],value=[2,2],meaning="diagonal matrix")]
    first = call(service,sid,"first","run_calculation_graph",steps=plan,outputs={"definition":"matrix"},purpose="Define")
    assert first["status"] == "completed",first
    second = call(service,sid,"reuse","run_calculation_graph",reuse={"A":{"request_id":"first","output_path":"definition"}},
        steps=[dict(id="square",op="multiply",args=["A","A"],meaning="Square")],outputs={"answer":"square"},purpose="Reuse")
    assert second["status"] == "completed",second
    assert second["result"]["output_nodes"]["answer"]["value"] == "Matrix([[1, 0], [0, 1]])"
    p = call(service,sid,"assume","run_calculation_graph",steps=[dict(id="a",op="integer",value=2,meaning="Proposed",assumption="Unproved coefficient")],outputs={"p":"a"},purpose="Propose")
    reused = call(service,sid,"reuse-p","run_calculation_graph",reuse={"a":p["result"]["output_nodes"]["p"]["id"]},outputs={"answer":"a"},purpose="Reuse provisional")
    assert reused["status"] == "completed",reused
    submit = call(service,sid,"submit","submit",answer={"answer":2},support_nodes={"answer":reused["result"]["output_nodes"]["answer"]["id"]})
    assert not submit["result"]["admitted"]
    bad = call(service,sid,"foreign","run_calculation_graph",reuse={"A":"missing"},outputs={"answer":"A"},purpose="No")
    assert bad["status"] == "rejected"


@pytest.mark.integration
@pytest.mark.symbolic
def test_algebra_is_not_domain_application(service):
    sid = service.open("Compute x + x and twice x", ["answer"])["session_id"]
    steps = [dict(id="x",op="symbol",value="x",meaning="task variable"),
        dict(id="one",op="integer",value=1,meaning="unit"),
        dict(id="two",op="add",args=["one","one"],meaning="two"),
        dict(id="xx",op="multiply",args=["x","x"],meaning="square"),
        dict(id="sum",op="add",args=["xx","xx"],meaning="repeated algebraic term"),
        dict(id="scaled",op="multiply",args=["two","sum"],meaning="algebraic scaling")]
    result = call(service,sid,"algebra","run_calculation_graph",steps=steps,outputs={"answer":"scaled"},purpose="Algebra")
    assert result["result"]["support_summary"]["current_open_count"] == 0
    steps[-1]["application"] = "physical_rule"
    physical = call(service,sid,"physical","run_calculation_graph",steps=steps,outputs={"answer":"scaled"},purpose="Proposed physical scaling")
    assert physical["result"]["support_summary"]["current_open_count"] > 0




@pytest.mark.integration
@pytest.mark.symbolic
def test_operation_exception_has_operands(service):
    sid = service.open("Compute", ["answer"])["session_id"]
    result = call(service,sid,"bad","run_calculation_graph",steps=[dict(id="one",op="integer",value=1,meaning="unit"),
        dict(id="trace",op="trace",args=["one"],meaning="Invalid scalar trace")],outputs={"answer":"trace"},purpose="Diagnostic")
    diagnostic = result["result"]["calculation_diagnostics"][0]
    assert diagnostic["step_id"] == "trace"
    assert diagnostic["inputs"] == {"one":"1"}
    assert diagnostic["error"]
