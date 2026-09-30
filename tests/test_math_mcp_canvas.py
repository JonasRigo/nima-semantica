"""Saved canvas sources/ports plus installed-component equivalence."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import pytest
from nima_semantica.math_mcp_contracts import TOOLS, invoke
from nima_semantica.math_session_service import MathServiceConfig, MathSessionService

ROOT = Path(__file__).resolve().parents[1]


def test_saved_canvases_expose_all_operations_and_connected_ports():
    sys.path.insert(0, str(ROOT / "deploy"))
    from flow_io import validate_edges
    files = list((ROOT / "examples/langflow_replacement/math_mcp").glob("*.json"))
    assert {p.stem for p in files} == {*TOOLS, "operations"}
    for file in files:
        flow = json.loads(file.read_text())
        for source, expected in flow["nima_tool_manifest"]["native_sources_sha256"].items():
            assert hashlib.sha256((ROOT / "src/nima_semantica" / source).read_bytes()).hexdigest() == expected
        validate_edges(flow)
        nodes = {n["id"]: n for n in flow["data"]["nodes"]}
        assert len(nodes) == 3 and len(flow["data"]["edges"]) == 2
        middle = next(n for n in nodes.values() if n["data"]["type"].startswith("MathMCP"))
        name = middle["data"]["type"]
        template = middle["data"]["node"]["template"]
        assert template["code"]["value"] == (ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")).read_text()
        assert template["execute"]["value"] is False
        for edge in flow["data"]["edges"]:
            sh, th = edge["data"]["sourceHandle"], edge["data"]["targetHandle"]
            assert sh["name"] in {x["name"] for x in nodes[edge["source"]]["data"]["node"]["outputs"]}
            assert th["fieldName"] in nodes[edge["target"]]["data"]["node"]["template"]
            assert json.loads(edge["sourceHandle"].replace("œ", '"')) == sh
            assert json.loads(edge["targetHandle"].replace("œ", '"')) == th


def test_installed_canvas_preview_and_native_equivalence(tmp_path, monkeypatch):
    pytest.importorskip("lfx")
    service = MathSessionService(MathServiceConfig(database_path=str(tmp_path / "session.sqlite"), project_id="p", run_id="r"))
    sid = service.open("compute", ["answer"])["session_id"]
    for operation in [*TOOLS, "operations"]:
        name = "MathMCP" + "".join(x.title() for x in operation.split("_"))
        path = ROOT / "deploy/langflow_components/nima_tools" / (name + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "canvas_service", lambda: service)
        component = getattr(module, name)()
        component.set(execute=False, request_json="{}")
        result = json.loads(component.run_request().text)
        assert result.get("executed") is False or operation == "operations"
        if operation != "operations":
            component.set(execute=True, request_json='{"unexpected":"SECRET"}')
            rejected = json.loads(component.run_request().text)
            assert rejected["status"] == "failed" and rejected["data"]["executed"] is False
            assert "SECRET" not in json.dumps(rejected)
        if operation == "record_step":
            request = dict(session_id=sid, request_id="same", expected_revision=0, kind="claim", statement="A proposal", value=2, depends_on=["task"])
            expected = invoke(service, operation, request)
            component.set(execute=True, request_json=json.dumps(request))
            assert json.loads(component.run_request().text) == expected
