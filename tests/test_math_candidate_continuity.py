"""Stable working selection without implicit mixing or admission changes."""
import copy
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_repair_feedback import repair_feedback
from nima_semantica.math_response_views import response_view


def output(g, path, receipt, parents=()):
    return g._put("calculation_output", "1", "observed", list(parents),
        {"output_path": path, "receipt_id": receipt})["id"]


def test_partial_update_does_not_hide_complete_candidate_obligation():
    g = SingleCalculationGraph("compute", (), ("a", "b"))
    root = g._put("assumption", "1", "provisional", [], {})["id"]
    g.obligations[root] = {"status":"open","reason":"unproved","root_id":root}
    a, b = output(g,"a","first",[root]), output(g,"b","first")
    new_b = output(g,"b","repair")
    before = copy.deepcopy(g.export())
    view = repair_feedback(g)
    assert view["current_candidate"]["support_nodes"] == {"a":a,"b":b}
    assert view["current_candidate"]["pending_output_updates"] == {"b":new_b}
    assert view["current_candidate"]["open_obligation_ids"] == [root]
    selected = {"a":a,"b":new_b}
    attempt = ("submit",{"status":"completed","result":{"proposal":{"support_nodes":selected},"admitted":False}})
    selected_view = repair_feedback(g,[attempt])
    assert selected_view["current_candidate"]["support_nodes"] == selected
    assert selected_view["current_candidate"]["selection"] == "last_submitted_candidate"
    compact = response_view({"result":selected_view},"run_experiment")
    assert compact["result"]["support_summary"]["current_open_count"] == 1
    assert g.export() == before
    for status in ("failed","cancelled","rejected"):
        ignored = ("bad",{"status":status,"result":{"proposal":{"support_nodes":{"a":b,"b":a}}}})
        assert repair_feedback(g,[attempt,ignored])["current_candidate"]["support_nodes"] == selected
    # Explicit read selections stay read-only; newer complete batches replace the
    # working candidate atomically, never one path at a time.
    assert repair_feedback(g,[attempt],support_nodes={"b":new_b})["current_candidate"]["missing_required_paths"] == ["a"]
    c, d = output(g,"a","next"), output(g,"b","next")
    next_attempt = ("next",{"status":"completed","result":{"output_nodes":{"a":{"id":c},"b":{"id":d}}}})
    assert repair_feedback(g,[attempt,next_attempt])["current_candidate"]["support_nodes"] == {"a":c,"b":d}
    assert repair_feedback(g,[next_attempt,attempt])["current_candidate"]["support_nodes"] == selected


def test_incomplete_batches_are_never_merged():
    g = SingleCalculationGraph("compute", (), ("a","b"))
    output(g,"a","first")
    b = output(g,"b","second")
    view = repair_feedback(g)["current_candidate"]
    assert view["support_nodes"] == {"b":b}
    assert view["missing_required_paths"] == ["a"]


def test_submitted_selection_survives_service_reopen(tmp_path):
    import json
    from nima_semantica.math_session_service import MathSessionService, MathServiceConfig
    from nima_semantica.math_mcp_contracts import invoke
    g = SingleCalculationGraph("compute", (), ("a","b"))
    root = g._put("assumption","1","provisional",[],{})["id"]
    g.obligations[root] = {"status":"open","reason":"unproved","root_id":root}
    a = output(g,"a","first",[root])
    output(g,"b","first")
    b = output(g,"b","repair")
    config = MathServiceConfig(database_path=str(tmp_path/"s.db"),project_id="p",run_id="r")
    service = MathSessionService(config)
    sid = service.open("compute",["a","b"])["session_id"]
    with service._connect() as db:
        db.execute("UPDATE sessions SET state_json=? WHERE session_id=?",(json.dumps(service._state(g)),sid))
    request = dict(session_id=sid,request_id="choose",expected_revision=0,support_nodes={"a":a,"b":b})
    result = invoke(service,"submit",request)
    assert result["status"] == "completed"
    assert not result["result"]["admitted"]
    reopened = MathSessionService(config)
    assert invoke(reopened,"submit",request) == result
    view = invoke(reopened,"frontier",dict(session_id=sid))["result"]["current_candidate"]
    assert view["support_nodes"] == {"a":a,"b":b}
    assert view["selection"] == "last_submitted_candidate"
    assert view["open_obligation_ids"] == [root]
