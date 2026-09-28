"""Discovery remains possible; compact presentation cannot waive admission."""
import copy
import json
import pytest
from nima_semantica.math_mcp_contracts import invoke, TOOLS
from nima_semantica.math_response_views import response_view, assemble_answer
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_session_service import MathSessionService, MathServiceConfig


def test_compact_view_does_not_mutate_durable_outcome():
    response = {"revision":1,"result":{"output_nodes":{"answer":{"id":"n2","value":"2"}},
        "current_candidate":{"open_obligation_ids":["n1"],"audit_only_open_obligation_ids":["old"]},
        "repair_task":{"target_id":"n1"},"repair_progress":[{"target_id":"n1"},{"target_id":"old"}],
        "open_obligations":[{"root_id":"n1"},{"root_id":"old"}],
        "calculation_diagnostics":[{"issue":"zero_denominator"}],"evidence_handoff":{"large":"audit"}}}
    before = copy.deepcopy(response)
    compact = response_view(response,"run_calculation_graph")
    assert "repair_task" not in compact["result"]
    assert compact["result"]["calculation_diagnostics"] == response["result"]["calculation_diagnostics"]
    assert compact["result"]["support_summary"]["current_open_count"] == 1
    qualified = response_view(response,"frontier")["result"]
    assert qualified["open_obligations"] == [{"root_id":"n1"}]
    assert qualified["repair_progress"] == [{"target_id":"n1"}]
    assert response == before


def test_assembly_preserves_arbitrary_json_and_rejects_ambiguous_paths():
    for value in (None, [1,{"x":"y"}], {"hello":False}, 2):
        g = SingleCalculationGraph("task", (), ("$",))
        n = g._put("claim", value, "provisional", [], {})
        assert assemble_answer(g,{"$":n["id"]}) == value
    g = SingleCalculationGraph("task", (), ("a.b","a.c"))
    a = g._put("claim", "x", "provisional", [], {})
    b = g._put("claim", [1,2], "provisional", [], {})
    assert assemble_answer(g,{"a.b":a["id"],"a.c":b["id"]}) == {"a":{"b":"x","c":[1,2]}}
    g.required_paths = ("a","a.b")
    with pytest.raises(ValueError,match="overlapping"):
        assemble_answer(g,{"a":a["id"],"a.b":b["id"]})


@pytest.mark.integration
@pytest.mark.symbolic
def test_unresolved_support_does_not_block_exploration_but_blocks_assembly_submission(tmp_path):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path/"s.db"),project_id="p",run_id="r",allow_execution=True),worker=configured_symbolic_worker())
    sid = service.open("Compute one plus one",["answer"])["session_id"]
    def call(op,rid,**args):
        return invoke(service,op,dict(session_id=sid,request_id=rid,expected_revision=service.read(sid)["revision"],**args))
    p = call("record_step","premise",kind="assumption",statement="Unproved",value=2,depends_on=[])
    assert "repair_task" not in p["result"]
    target = p["result"]["node"]["id"]
    explored = call("run_experiment","explore",source="a=1\nb=a+a\nprint(b)",purpose="Independent work",depends_on=["task"])
    assert explored["status"] == "completed"
    rejected = call("submit","unsupported",support_nodes={"answer":target})
    assert not rejected["result"]["admitted"]
    assert rejected["result"]["proposal"]["answer"] == {"answer":2}
    qualified = invoke(service,"frontier",dict(session_id=sid,support_nodes={"answer":target}))
    assert target in qualified["result"]["current_candidate"]["open_obligation_ids"]
    compiled = call("run_calculation_graph","calculate",purpose="Independent sum",steps=[
        dict(id="one",op="integer",value=1,meaning="Unit"),dict(id="sum",op="add",args=["one","one"],meaning="Sum")],outputs={"answer":"sum"})
    output = compiled["result"]["output_nodes"]["answer"]
    passed = call("submit","supported",support_nodes={"answer":output["id"]})
    assert passed["result"]["admitted"]
    assert passed["result"]["proposal"]["answer"] == {"answer":output["value"]}
    full = invoke(service,"inspect",dict(session_id=sid,kind="action",identifier="calculate"))
    assert "repair_progress" in full["result"]["outcome"]["result"]
    assert any(x["target_id"] == target for x in full["result"]["outcome"]["result"]["repair_progress"])
    # Explicit JSON null remains a supplied answer, not a request for assembly.
    explicit = call("submit","explicit-null",answer=None,support_nodes={"answer":output["id"]})
    assert explicit["status"] == "rejected"


def test_tool_metadata_separates_reads_from_mutations():
    for operation in ("retrieve_context","run_experiment","run_calculation_graph","submit"):
        assert "sequentially" in TOOLS[operation][1]
    for operation in ("status","frontier","inspect","export"):
        assert "Read-only" in TOOLS[operation][1]
