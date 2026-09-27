"""Installed Langflow canvas acceptance with explicitly injected offline doubles."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_search_for_counterexamples import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured
from test_counterexample_tool import Worker, encoding

CANVAS=ROOT/"examples/langflow_replacement/search_for_counterexamples.json"


def execute(payload, *, writes=False):
    from lfx.graph import Graph
    flow=json.loads(CANVAS.read_text())
    node=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="SearchForCounterexamples")
    for name in ("allow_execution","allow_audit_writes"):
        node["data"]["node"]["template"][name]["value"]=writes
    graph=Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}], outputs=["ChatOutput-counterexample"]),30)
        c=graph.get_vertex("SearchForCounterexamples-nima").custom_component
        result,preview=await asyncio.gather(c.result_data(),c.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())


def test_snapshot_and_operator_authority():
    flow=json.loads(CANVAS.read_text());assert flow==build();validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name in ("CounterexampleFields","SearchForCounterexamples"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_execution","allow_audit_writes","allow_model_calls","check_plan_base64","max_actions","timeout_seconds","model_manifest_json"):
            if key in template:
                assert not template[key].get("input_types") and not template[key].get("tool_mode")
    retrieval = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "CounterexampleRetrieval")
    template = retrieval["data"]["node"]["template"]
    assert template["enabled"]["value"] is False
    for key in ("enabled", "projection_id"):
        assert not template[key].get("input_types") and not template[key].get("tool_mode")


def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({})["data"]["executed"] is False


def test_disabled_execution_no_writes(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);before=store.revision;store.close()
    assert execute({"mode":"integer","operation_id":"denied","encoding":encoding().model_dump(mode="json")})["status"]=="failed"
    store=GraphStore(configured);assert store.revision==before;store.close()


def test_direct_with_explicit_worker_fixture(configured,monkeypatch):
    worker=Worker()
    monkeypatch.setattr("nima_semantica.symbolic_transport.configured_symbolic_worker",lambda:worker)
    result=execute({"mode":"integer","operation_id":"fixture","encoding":encoding().model_dump(mode="json")},writes=True)
    assert result["status"]=="partial" and len(worker.calls)==1
    assert execute({"mode":"integer","operation_id":"fixture","encoding":encoding().model_dump(mode="json")},writes=True)==result
    assert len(worker.calls)==1


@pytest.mark.parametrize("extra",[{"allow_execution":True},{"corpus_id":"private"},{"check_plan":{}},{"worker":"evil"},{"max_actions":100}])
def test_public_override_denied(configured,extra):
    with pytest.raises(Exception):execute(extra)


def test_connected_model_uses_native_single_action_tools(configured):
    from types import SimpleNamespace
    from model_fixture import MANIFEST
    from test_counterexample_tool import actions
    sequence=iter(actions());bound=[]
    class Model:
        def bind_tools(self,tools,**options):
            bound.append((tools,options));return self
        def invoke(self,messages):
            action=next(sequence)
            return SimpleNamespace(tool_calls=[{"name":action["name"],"args":action["arguments"]}],
                invalid_tool_calls=[],usage_metadata={"input_tokens":10,"output_tokens":20},
                response_metadata={"finish_reason":"tool_calls"})
    worker=Worker()
    component=load_component("SearchForCounterexamples")().set(
        payload={"mode":"agent","operation_id":"canvas-agent"},model=Model(),worker=worker,
        model_manifest_json=MANIFEST.model_dump_json(),allow_execution=True,allow_audit_writes=True,allow_model_calls=True)
    async def run():
        result,preview,table=await asyncio.gather(component.result_data(),component.preview_message(),component.table_data())
        assert result.data["status"]=="partial",result.data
        assert result.data["data"]["result"]["outcome"]=="validated_counterexample_to_encoding"
        assert len(table)>3 and json.loads(preview.text[8:-4])==result.data
    asyncio.run(run())
    assert len(worker.calls)==1 and len(bound)==3
    assert [t["function"]["name"] for t in bound[0][0]]==["plan_search"]
    assert all(options["parallel_tool_calls"] is False for _,options in bound)

