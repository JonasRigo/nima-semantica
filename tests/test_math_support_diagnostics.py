"""Standalone regressions for the maintained graph implementation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nima_semantica.math_calculation_assistance import ApplicationSupportError
from nima_semantica.math_session_service import MathSessionService
from nima_semantica.math_single_graph_state import SingleCalculationGraph




def setup_graph():
    g = SingleCalculationGraph("Compute x", (), ("answer",))
    source = g.record_source("method", "α\nAn exact method with stated assumptions.\nβ", "rev")
    symbol = g._put("calculation_input", "x", "observed", ["task"], {}, operation="symbol")
    trace = g._put("calculation_operation", "1", "observed", [symbol["id"], source["id"]], {}, operation="trace")
    evidence = g._put("calculation_output", "1", "observed", [trace["id"]], {"receipt_id": "independent"})
    root = g._put("calculation_operation", "1", "observed", [], {"receipt_id": "target"},
        operation="multiply", application_reason="declared_physical_rule")
    g.obligations[root["id"]] = {"root_id": root["id"], "status": "open"}
    args = dict(operation_id=root["id"], evidence_id=evidence["id"], rationale="The independently computed quantity matches this application.",
        method_source_id=source["id"], method_span={"start": 2, "end": len(source["value"])-2})
    return g, source, evidence, root, args


def test_span_binding_and_existing_qualified_route():
    g, source, evidence, root, args = setup_graph()
    result, changed = MathSessionService._dispatch(g, SimpleNamespace(), "substantiate_application", args)
    assert changed
    assert result["method_selection"]["quote"] == source["value"][2:-2]
    assert result["method_selection"]["provenance"] == source["origin"]
    assert g.obligations[root["id"]]["status"] == "agent_supported_not_verified"
    # Ordinary multiplication inherits established support, without another obligation.
    product = g._put("calculation_operation", "3", "observed", [root["id"]], {}, operation="multiply")
    assert not g._open_roots(product["id"])
    # Introducing a new application is a distinct obligation, not certified by algebra.
    physical = g._put("calculation_operation", "3", "observed", [product["id"]], {},
        operation="multiply", application_reason="declared_physical_rule")
    g.obligations[physical["id"]] = {"root_id": physical["id"], "status": "open"}
    assert g._open_roots(physical["id"]) == {physical["id"]}


@pytest.mark.parametrize("defect", ["circular", "open", "unrelated", "conflicting_quote", "invalid_span"])
def test_no_support_laundering(defect):
    g, source, evidence, root, args = setup_graph()
    if defect == "circular":
        evidence["depends_on"].append(root["id"])
    elif defect == "open":
        g.obligations[evidence["id"]] = {"root_id": evidence["id"], "status": "open"}
    elif defect == "unrelated":
        other = g.record_source("other", "A similar but unrelated method passage.", "rev")
        args.update(method_source_id=other["id"], method_span={"start":0,"end":len(other["value"])})
    elif defect == "conflicting_quote":
        args["method_quote"] = "Invented quote"
    else:
        args["method_span"]["end"] = 99999
    before = copy.deepcopy(g.export())
    with pytest.raises(ValueError) as failure:
        MathSessionService._dispatch(g, SimpleNamespace(), "substantiate_application", args)
    if defect in {"circular", "open", "unrelated"}:
        assert failure.value.support_diagnostics["method_selection"]["source_id"] == args["method_source_id"]
    assert g.export() == before
