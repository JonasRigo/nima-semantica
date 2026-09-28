"""Standalone regressions for the maintained graph implementation."""
import json
import runpy
from pathlib import Path

import pytest
from nima_semantica.math_mcp_contracts import invoke
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_repair_feedback import repair_feedback
from nima_semantica.math_evidence_handoff import reconstruction_context

ROOT = Path(__file__).resolve().parents[1]


def service_at(tmp_path, worker=None):
    return MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"),
        project_id="p", run_id="r", allow_execution=True), worker=worker)


def test_selection_does_not_resolve_or_invent_missing_paths():
    graph = SingleCalculationGraph("Compute", (), ("a", "b"))
    old = graph.add_step(kind="assumption", statement="old", value=3, depends_on=[])
    new = graph.add_step(kind="assumption", statement="new", value=2, depends_on=[])
    graph._put("calculation_output", 3, "observed", [old["id"]], {"receipt_id":"old", "output_path":"b"})
    current = graph._put("calculation_output", 2, "observed", [new["id"]], {"receipt_id":"new", "output_path":"a"})
    before = graph.export()
    view = repair_feedback(graph)
    assert view["repair_task"]["target_id"] == new["id"]
    assert view["current_candidate"]["missing_required_paths"] == ["b"]
    assert view["current_candidate"]["audit_only_open_obligation_ids"] == [old["id"]]
    explicit = repair_feedback(graph, support_nodes={"a": old["id"]})
    assert explicit["repair_task"]["target_id"] == old["id"]
    with pytest.raises(ValueError):
        repair_feedback(graph, support_nodes={"a": "foreign"})
    assert graph.export() == before


@pytest.mark.parametrize("outcome", ["code_failed", "timeout", "output_limit"])
def test_failed_execution_is_performed_not_resolved(tmp_path, outcome):
    class Worker:
        def run(self, *args):
            return {"outcome": outcome, "exit_code":1, "stdout":"", "stderr":"failed"}
    service = service_at(tmp_path, Worker())
    sid = service.open("Calculate", ["answer"])["session_id"]
    proposal = service.mutate(sid, "p", 0, "record_step", dict(kind="assumption",statement="Proposed",value=2,depends_on=[]))
    target = proposal["result"]["node"]["id"]
    result = service.mutate(sid, "e", 1, "run_experiment", dict(source="raise Exception()",depends_on=[], repair_target=target))
    assert result["result"]["repair_attempt"]["performed"]
    assert result["result"]["repair_task"]["state"] == "attempted"
    assert result["result"]["repair_progress"][0]["evidence_status"] == "open"
    inspected = service.read(sid, "inspect_receipt", {"receipt_id":result["result"]["receipt_id"]})
    assert not inspected["result"]["compilation_handoff"]["reconstruction_available"]


def test_reconstruction_rejects_foreign_failed_and_changed_receipts():
    graph = SingleCalculationGraph("Compute", (), ("answer",))
    with pytest.raises(ValueError, match="this session"):
        reconstruction_context(graph, "unknown")
    graph.record_execution("print(2)", dict(outcome="code_failed", exit_code=1), "failed", evidence_eligible=False)
    with pytest.raises(ValueError, match="successfully"):
        reconstruction_context(graph, "failed")
    graph.record_execution("print(2)", dict(outcome="executed", exit_code=0, stdout="2"), "ok", evidence_eligible=False)
    graph.receipts["ok"]["source"] += "# changed"
    with pytest.raises(ValueError, match="hash"):
        reconstruction_context(graph, "ok")


@pytest.mark.integration
@pytest.mark.symbolic
def test_simple_reconstruction_can_support_claim_without_promoting_receipt(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service = service_at(tmp_path, configured_symbolic_worker())
    sid = service.open("Compute one plus one", ["answer"])["session_id"]
    proposal = service.mutate(sid, "p", 0, "record_step", dict(kind="assumption",statement="Proposed",value=2,depends_on=[]))
    target = proposal["result"]["node"]["id"]
    explored = service.mutate(sid, "e", 1, "run_experiment", dict(source="print(1+1)", depends_on=["task"], repair_target=target))
    rid = explored["result"]["receipt_id"]
    old = service.read(sid, "inspect_receipt", {"receipt_id":rid})["result"]["receipt"]
    result = invoke(service, "run_calculation_graph", dict(session_id=sid,request_id="c",expected_revision=2,
        reconstructs_receipt=rid, steps=[dict(id="one",op="integer",value=1,meaning="Unit"),
        dict(id="sum",op="add",args=["one","one"],meaning="Add units")], outputs={"method.sum":"sum"},purpose="Reconstruct actual addition"))
    assert result["status"] == "completed", result
    assert result["result"]["reconstruction"]["source_sha256"] == old["source_sha256"]
    assert service.read(sid,"inspect_receipt",{"receipt_id":rid})["result"]["receipt"] == old
    rejected = service.mutate(sid,"bad-support",3,"substantiate",dict(hypothesis_id=target,
        evidence_id=explored["result"]["node_ids"][0],rationale="Cannot certify stdout"))
    assert rejected["status"] == "rejected"
    output = result["result"]["output_nodes"]["method.sum"]["id"]
    supported = service.mutate(sid,"support",3,"substantiate",dict(hypothesis_id=target,evidence_id=output,rationale="Independent compiled addition"))
    assert supported["status"] == "completed"
    submitted = service.mutate(sid,"submit",supported["revision"],"submit",dict(answer={"answer":2},support_nodes={"answer":target}))
    assert submitted["result"]["admitted"]


def vertex_square_plan():
    steps = []
    def add(identifier, op, args=(), **kwargs):
        steps.append(dict(id=identifier,op=op,args=list(args),meaning=identifier,**kwargs))
    add("zero","integer",value=0); add("one","integer",value=1)
    add("two","add",["one","one"]); add("minus","negative",["one"])
    add("imag","imaginary_unit"); add("minusimag","negative",["imag"])
    add("lp","symbol",value="L_perp",provenance=["task"])
    add("lz","symbol",value="L_z",provenance=["task"])
    for label, entries in (("x",["zero","one","one","zero"]),("y",["zero","minusimag","imag","zero"]),("z",["one","zero","zero","minus"])):
        add(label,"matrix",entries,value=[2,2])
        add("s"+label,"divide",[label,"two"])
        add("b"+label,"kronecker",["s"+label,label])
        add("v"+label,"multiply",["lz" if label=="z" else "lp","b"+label])
    add("vertex","sum",["vx","vy","vz"])
    add("square","multiply",["vertex","vertex"])
    outputs = {}
    for label in ("x","y","z"):
        add("num"+label,"multiply",["b"+label,"square"])
        add("tr"+label,"trace",["num"+label])
        add("norm"+label,"multiply",["b"+label,"b"+label])
        add("den"+label,"trace",["norm"+label])
        add("coefficient"+label,"divide",["tr"+label,"den"+label])
        add("clean"+label,"simplify",["coefficient"+label])
        outputs["method."+label] = "clean"+label
    return steps, outputs
