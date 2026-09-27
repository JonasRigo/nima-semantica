"""Standalone regressions for the maintained graph implementation."""
import ast
import hashlib
import json
from pathlib import Path
import runpy

import pytest

from nima_semantica.math_mcp_contracts import invoke
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_graph_program import compile_graph_program

ROOT = Path(__file__).resolve().parents[1]


def service_at(tmp_path, worker=None):
    return MathSessionService(MathServiceConfig(database_path=str(tmp_path / "s.db"),
        project_id="p", run_id="r", allow_execution=True), worker=worker)




@pytest.mark.integration
def test_explicit_assumption_executes_but_cannot_qualify_answer(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service = service_at(tmp_path, configured_symbolic_worker())
    sid = service.open("Compute c*x for the stated x", ["answer"])["session_id"]
    request = dict(session_id=sid, request_id="calc", expected_revision=0, purpose="Test unresolved coefficient",
        steps=[dict(id="x", op="symbol", value="x", meaning="Task variable"),
            dict(id="c", op="integer", value=2, meaning="Candidate coefficient", assumption="The coefficient might be two; not yet derived"),
            dict(id="product", op="multiply", args=["c", "x"], meaning="Conditional product")], outputs={"answer": "product"})
    result = invoke(service, "run_calculation_graph", request)
    assert result["status"] == "completed", result
    bindings = result["result"]["interface_normalizations"]
    assert any(x.get("basis") == "exact_task_symbol" for x in bindings)
    premise_id = next(x["node_id"] for x in bindings if x.get("basis") == "explicit_unresolved_assumption")
    premise = service.read(sid, "inspect_node", {"node_id": premise_id})["result"]["node"]
    assert premise["status"] == "provisional" and premise["kind"] == "assumption"
    submitted = invoke(service, "submit", dict(session_id=sid, request_id="submit", expected_revision=result["revision"],
        answer={"answer": "2*x"}, support_nodes={"answer": {"request_id": "calc", "output_path": "answer"}}))
    assert not submitted["result"]["admitted"]
    assert premise_id in submitted["result"]["proposal"]["unresolved_root_ids"]


def test_plan_python_and_graph_share_each_atomic_operation():
    graph = SingleCalculationGraph("Compute x+x", (), ("answer",))
    plan = compile_graph_program(graph, [dict(id="x", op="symbol", value="x", provenance=["task"], meaning="Task symbol"),
        dict(id="twice", op="add", args=["x", "x"], meaning="Sum")], {"answer": "twice"})
    nodes = graph.stage_compiled_plan(plan, "audit")
    assignments = [node for node in ast.parse(plan["source"]).body if isinstance(node, ast.Assign)]
    assert len(assignments) == len(plan["steps"]) == len(nodes)
    assert [node.targets[0].id for node in assignments] == ["v0", "v1"]
    assert isinstance(assignments[1].value, ast.BinOp)
    assert assignments[1].value.left.id == assignments[1].value.right.id == "v0"
    assert nodes["x"]["id"] in nodes["twice"]["depends_on"]
    assert all(node["origin"]["source_sha256"] == hashlib.sha256(plan["source"].encode()).hexdigest() for node in nodes.values())
    changed = {**plan, "source": plan["source"] + "# different script\n"}
    with pytest.raises(ValueError, match="differs from staged"):
        graph.record_compiled_execution(changed, {}, "audit")


def test_exploratory_result_is_not_evidence_eligible():
    graph = SingleCalculationGraph("Compute", (), ("answer",))
    nodes = graph.record_execution("print(2)", {"outcome": "executed", "exit_code": 0,
        "stdout": '{"answer":2}', "stderr": ""}, "explore", evidence_eligible=False)
    assert nodes
    assert all(node["status"] == "exploratory" for node in nodes)
    premise = graph.add_step(kind="assumption", statement="Unproved answer", value=2, depends_on=[])
    with pytest.raises(ValueError, match="failed or still conditional"):
        graph.substantiate(premise["id"], nodes[0]["id"], "Claim exploratory stdout proves the answer")

def test_unbound_numeric_coefficient_is_rejected_without_execution(tmp_path):
    class NoWorker:
        def run(self, *args):
            pytest.fail('Unbound numeric inputs must fail before execution')
    service = service_at(tmp_path, NoWorker())
    sid = service.open('Compute c*x for x', ['answer'])['session_id']
    before = service.read(sid, 'export')
    result = invoke(service, 'run_calculation_graph', {
        'session_id': sid, 'request_id': 'unbound', 'expected_revision': 0,
        'purpose': 'Check input provenance',
        'steps': [dict(id='x', op='symbol', value='x', meaning='Task variable'),
                  dict(id='c', op='integer', value=3, meaning='Unjustified coefficient'),
                  dict(id='product', op='multiply', args=['c', 'x'], meaning='Product')],
        'outputs': {'answer': 'product'}})
    assert result['status'] == 'rejected'
    assert 'input provenance required' in result['result']['diagnostic']
    assert service.read(sid, 'export') == before
