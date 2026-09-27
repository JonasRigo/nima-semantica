"""Actual installed Langflow loader and full-canvas Tool Guide acceptance."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("lfx")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_tool_guide", ROOT / "deploy/build_tool_guide.py")
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


def test_saved_component_snapshots_match_current_source():
    flow = json.loads(builder.CANVAS.read_text())
    assert flow["nima_tool_manifest"]["visual_approval"] == "approved"
    assert len(flow["data"]["nodes"]) == 5
    assert len(flow["data"]["edges"]) == 4
    for node in flow["data"]["nodes"]:
        name = node["data"]["type"]
        template = node["data"]["node"]["template"]
        if name in ("GuideFields", "ToolGuide"):
            source = (builder.COMPONENTS / (name + ".py")).read_text()
            assert source == template["code"]["value"]
            assert node["data"]["node"]["metadata"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        if name in ("ChatInput", "ChatOutput"):
            assert template["should_store_message"]["value"] is False
    rebuilt = builder.build()
    assert rebuilt["nima_tool_manifest"] == flow["nima_tool_manifest"]
    assert rebuilt["data"]["edges"] == flow["data"]["edges"]


@pytest.mark.parametrize("override,expected", [(None, "Read Evidence"), ("{}", "Read Evidence"), ('{"tool":""}', ""), ('{"tool":"Tool Guide"}', "Tool Guide")])
def test_named_input_and_explicit_json_override(override, expected):
    from lfx.schema import Message
    component = builder.load_component("GuideFields")().set(tool="Read Evidence", mcp_request=Message(text=override) if override is not None else None)
    assert asyncio.run(component.request_data()).data == {"tool": expected}


@pytest.mark.parametrize("override", ['{"tool":"Tool Guide","execute":true}', '[]', 'null', '{bad', '{"tool":false}', '{"tool":"a","tool":"b"}'])
def test_invalid_transport_fails_closed(override):
    from lfx.schema import Message
    component = builder.load_component("GuideFields")().set(tool="", mcp_request=Message(text=override))
    with pytest.raises(ValueError):
        asyncio.run(component.request_data())


def test_preview_table_and_json_share_one_execution(monkeypatch):
    from lfx.schema import Data
    cls = builder.load_component("ToolGuide")
    module = sys.modules[cls.__module__]
    original = module.tool_guide
    calls = []
    def lookup(request):
        calls.append(request)
        return original(request)
    monkeypatch.setattr(module, "tool_guide", lookup)
    component = cls().set(payload=Data(data={"tool": ""}))
    async def run():
        return await asyncio.gather(component.result_data(), component.preview_message(), component.table_data())
    result, preview, table = asyncio.run(run())
    assert len(calls) == 1
    assert result.data["status"] == "complete"
    assert len(table) == 24
    assert "Visual approval: approved" in preview.text


@pytest.mark.parametrize("payload,expected", [({}, "Calculate Mathematics"), ({"tool": "Read Evidence"}, "## Read Evidence"), ({"tool": "Tool Guide"}, "Input schema:"), ({"tool": "unknown"}, "tool_guide.unknown_tool")])
def test_full_canvas_executes_through_real_langflow(payload, expected):
    from lfx.graph import Graph
    graph = Graph.from_payload(json.loads(builder.CANVAS.read_text())["data"])
    async def run():
        return await asyncio.wait_for(graph.arun(inputs=[{"input_value": json.dumps(payload)}],
            outputs=["ChatOutput-omYFS"]), timeout=30)
    result = asyncio.run(run())
    assert result and result[0].outputs
    assert expected in str(result)
    assert graph.get_vertex("ToolGuide-EBGd-").built


def test_full_canvas_rejects_attempted_execution():
    from lfx.graph import Graph
    graph = Graph.from_payload(json.loads(builder.CANVAS.read_text())["data"])
    async def run():
        return await graph.arun(inputs=[{"input_value": '{"tool":"Save Ontology","execute":true}'}], outputs=["ChatOutput-omYFS"])
    with pytest.raises(Exception):
        asyncio.run(run())
    assert not graph.get_vertex("ToolGuide-EBGd-").built
