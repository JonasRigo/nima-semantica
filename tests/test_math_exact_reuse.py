"""Offline exact-identity reuse and submission friction regressions."""
import copy
import json
from types import SimpleNamespace

import pytest

from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_session_service import MathSessionService
from nima_semantica.math_interface_helpers import bind_input_origins
from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_calculation_assistance import expand_reuse
from nima_semantica.math_response_views import submission_support


def original():
    g = SingleCalculationGraph("Compute x", (), ("answer",))
    args, _ = bind_input_origins(g, {"steps": [
        dict(id="x", op="symbol", value="x", meaning="Task variable"),
        dict(id="weight", op="negative", args=["x"], meaning="Proposed application", application="physical_rule")],
        "outputs": {"answer":"weight"}})
    plan = compile_graph_program(g, args["steps"], args["outputs"])
    plan["typed_applicability"] = True
    result = g.record_compiled_execution(plan, {"outcome":"executed", "exit_code":0,
        "stdout":json.dumps({"steps":{"x":"x","weight":"-x"}, "outputs":{"answer":"-x"}})}, "original")
    return g, result["steps"]["weight"]["id"], result["outputs"]["answer"]["id"]


class ReplayWorker:
    """Synthetic deterministic execution observations, not scientific evidence."""
    def __init__(self, mismatch=False):
        self.mismatch = mismatch

    def run(self, source, timeout):
        import re
        ids = re.findall(r"_nima_eval\('([^']+)'", source)
        values = {k: ("x" if i == 0 else "-x") for i,k in enumerate(ids)}
        if self.mismatch:
            values[ids[1]] = "x"
        return {"outcome":"executed", "exit_code":0,
            "stdout":json.dumps({"steps":values,"outputs":{"answer":values[ids[-1]]}})}


def reuse(g, node, *, mismatch=False):
    return MathSessionService._dispatch(g, SimpleNamespace(allow_execution=True, timeout_seconds=30,indexed_sets=()),
        "run_calculation_graph", {"steps":[],"reuse":{"old":node},"outputs":{"answer":"old"}},
        worker=ReplayWorker(mismatch))[0]


@pytest.mark.parametrize("qualified", [False, True])
def test_exact_identity_preserves_support_status_and_chained_reuse(qualified):
    g, root, out = original()
    if qualified:
        g.obligations[root]["status"] = "agent_supported_not_verified"
    before = copy.deepcopy(g.obligations)
    first = reuse(g, out)
    second = reuse(g, first["output_nodes"]["answer"]["id"])
    assert g.obligations == before
    assert root in first["step_nodes"].values()
    output = second["output_nodes"]["answer"]["id"]
    assert g._open_roots(output) == (set() if qualified else {root})
    # Reopen/serialization must retain observed operation identities.
    restored = copy.deepcopy(g)
    restored.compiled_plans = json.loads(json.dumps(g.compiled_plans))
    assert reuse(restored, output)["output_nodes"]["answer"]["value"] == "-x"


def test_changed_execution_does_not_inherit_identity():
    g, root, out = original()
    before = copy.deepcopy(g.nodes[root])
    result = reuse(g, out, mismatch=True)
    assert "output_nodes" not in result
    assert g.nodes[root] == before
    assert g.obligations[root]["status"] == "open"


def test_equal_value_does_not_merge_different_calculation_nodes():
    g, root, out = original()
    other = copy.deepcopy(g.compiled_plans["original"]["plan"])
    obs = copy.deepcopy(g.receipts["original"]["output"])
    second = g.record_compiled_execution(other, obs, "different")
    other_root = second["steps"]["weight"]["id"]
    g.obligations[root]["status"] = "agent_supported_not_verified"
    replay = reuse(g, second["outputs"]["answer"]["id"])
    assert g._open_roots(replay["output_nodes"]["answer"]["id"]) == {other_root}


@pytest.mark.parametrize("corruption", ["foreign", "hash", "failed"])
def test_reuse_requires_local_successful_integral_receipt(corruption):
    g, _, out = original()
    if corruption == "hash":
        g.receipts["original"]["source"] += "# modified"
    elif corruption == "failed":
        g.receipts["original"]["output"]["exit_code"] = 1
    else:
        out = "foreign"
    with pytest.raises(ValueError):
        expand_reuse(g, {"steps":[],"reuse":{"A":out},"outputs":{"answer":"A"}})


def test_new_step_limit_and_internal_metadata_remain_owned():
    g, _, _ = original()
    with pytest.raises(ValueError, match="128 new"):
        expand_reuse(g, {"steps":[{}]*129})
    with pytest.raises(ValueError, match="controller-owned"):
        MathSessionService._dispatch(g, None, "run_calculation_graph", {"_reuse_identities":{}})


def test_controller_expansion_remains_bounded():
    g, _, _ = original()
    steps = [dict(id="unit",op="integer",value=1,meaning="Unit",provenance=["task"])]
    steps += [dict(id=f"s{i}",op="negative",args=["unit" if i==0 else f"s{i-1}"],meaning="Negation") for i in range(128)]
    with pytest.raises(ValueError,match="128"):
        compile_graph_program(g,steps,{"answer":"s127"})
    assert len(compile_graph_program(g,steps,{"answer":"s127"},step_limit=512)["steps"]) == 129
    with pytest.raises(ValueError,match="512"):
        compile_graph_program(g,steps*4,{"answer":"s127"},step_limit=512)


def test_supplemental_submission_replay_and_reopen(tmp_path):
    from nima_semantica.math_session_service import MathServiceConfig
    from nima_semantica.math_mcp_contracts import invoke
    g, root, out = original()
    config = MathServiceConfig(database_path=str(tmp_path/"s.db"),project_id="p",run_id="r")
    service = MathSessionService(config)
    sid = service.open("Compute x",["answer"])["session_id"]
    with service._connect() as db:
        db.execute("UPDATE sessions SET state_json=? WHERE session_id=?",(json.dumps(service._state(g)),sid))
    request = dict(session_id=sid,request_id="submit",expected_revision=0,support_nodes={"answer":out,"derivation.weight":root})
    result = invoke(service,"submit",request)
    assert result["status"] == "completed" and not result["result"]["admitted"]
    assert invoke(MathSessionService(config),"submit",request) == result
    action = service.read(sid,"inspect_action",{"request_id":"submit"})["result"]
    assert json.loads(action["attempt"]["arguments_json"])["support_nodes"] == request["support_nodes"]


def test_supplementary_references_never_fill_missing_or_foreign_support():
    g, root, out = original()
    required, extras = submission_support(g, {"answer":out,"derivation.weight":root})
    assert required == {"answer":out} and extras == {"derivation.weight":root}
    with pytest.raises(ValueError, match="missing required support paths: answer"):
        submission_support(g,{"derivation.weight":out})
    with pytest.raises(ValueError, match="unknown local"):
        submission_support(g,{"answer":out,"derivation.bad":"foreign"})
    result, _ = MathSessionService._dispatch(g,None,"submit", {"support_nodes":{"answer":out,"detail":root}})
    assert not result["admitted"]
    assert result["proposal"]["support_nodes"] == {"answer":out}
    assert result["supplemental_support"]["nodes"] == {"detail":root}
