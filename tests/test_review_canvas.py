"""Installed Langflow graph and model binding acceptance, using offline responses."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_review_research import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured
from test_review_tool import region,actions,request
from test_claim_dependencies import seed,ref

CANVAS=ROOT/"examples/langflow_replacement/review_research.json"


@pytest.fixture(autouse=True)
def offline_model_construction(monkeypatch):
    # Building the disabled canvas must work without DNS or paid provider calls.
    monkeypatch.setattr("lfx.base.models.provider_ssrf.openai_compatible_client_kwargs",
        lambda *args, **kwargs: {})
    from langchain_openai import ChatOpenAI
    def unexpected_generation(*args, **kwargs):
        pytest.fail("Preview attempted a remote model call")
    monkeypatch.setattr(ChatOpenAI, "_generate", unexpected_generation)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", unexpected_generation)


def execute(payload):
    from lfx.graph import Graph
    data=json.loads(CANVAS.read_text())["data"]
    model=next(n for n in data["nodes"] if n["data"]["type"]=="OpenAIModel")
    model["data"]["node"]["template"]["api_key"].update(value="offline-test-only",load_from_db=False)
    graph=Graph.from_payload(data)
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],outputs=["ChatOutput-review_research"]),30)
        component=graph.get_vertex("ReviewResearch-nima").custom_component
        result,preview=await asyncio.gather(component.result_data(),component.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())


def test_snapshot_and_operator_authority():
    flow=json.loads(CANVAS.read_text());assert flow==build();validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name not in ("ChatInput","ChatOutput","OpenAIModel"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_audit_writes","allow_model_calls","max_actions","model_manifest_json","enabled","projection_id","allow_counterexamples","allow_substantiation","counterexample_target_ids_json"):
            if key in template:assert not template[key].get("input_types") and not template[key].get("tool_mode")
    model=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="OpenAIModel")
    template=model["data"]["node"]["template"]
    assert template["api_key"]["value"]=="NIMA_OPENROUTER_API_KEY" and template["api_key"]["load_from_db"]
    assert template["model_name"]["value"]=="openai/gpt-6-luna" and not template["json_mode"]["value"]


def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({})["data"]["executed"] is False


def test_disabled_execution_no_writes(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);before=store.revision;revision=store.graph_revision("papers","research").model_dump(mode="json");store.close()
    assert execute({"mode":"argument_review","operation_id":"denied","graph_revision":revision,"targets":[__import__("test_review_tool").target().model_dump(mode="json")]})["status"]=="failed"
    store=GraphStore(configured);assert store.revision==before;store.close()


@pytest.mark.parametrize("extra",[{"allow_audit_writes":True},{"corpus_id":"private"},{"model":"evil"},{"max_actions":100}])
def test_public_override_denied(configured,extra):
    result = execute(extra)
    assert result["status"] == "failed" and result["data"]["executed"] is False
    assert result["diagnostics"][0]["code"] == "request.invalid_field"


def test_connected_model_uses_native_actions_and_cached_outputs(configured):
    from types import SimpleNamespace
    from model_fixture import MANIFEST
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);seed(store);r=region(store);req=request(store,r);store.close()
    sequence=iter(actions(r));bound=[]
    class Model:
        def bind_tools(self,tools,**options):bound.append((tools,options));return self
        def invoke(self,messages):
            action=next(sequence)
            return SimpleNamespace(tool_calls=[{"name":action["name"],"args":action["arguments"]}],invalid_tool_calls=[],
                usage_metadata={"input_tokens":10,"output_tokens":20},response_metadata={"finish_reason":"tool_calls"})
    component=load_component("ReviewResearch")().set(payload=req.model_dump(mode="json"),model=Model(),
        model_manifest_json=MANIFEST.model_dump_json(),allow_audit_writes=True,allow_model_calls=True)
    async def run():
        result,preview,table=await asyncio.gather(component.result_data(),component.preview_message(),component.table_data())
        assert result.data["status"]=="partial",result.data
        assert len(table)>3 and json.loads(preview.text[8:-4])==result.data
        view=load_component("ReviewStateView")().set(payload=result)
        assert (await view.result_data()).data["publishable"] is False
    asyncio.run(run())
    assert len(bound)==4
    assert all(opts=={"tool_choice":"required","parallel_tool_calls":False} for _,opts in bound)
    assert "retrieve_context" not in [t["function"]["name"] for t in bound[0][0]]


def test_policy_mismatch_rejected_before_execution(configured):
    component=load_component("ReviewResearch")().set(payload={},policy={"policy_digest":"foreign"})
    with pytest.raises(ValueError,match="policy differs"):asyncio.run(component.result_data())


def test_symbolic_worker_preflight_stops_authorized_search(configured,monkeypatch):
    from nima_semantica.storage import GraphStore
    from model_fixture import MANIFEST
    store=GraphStore(configured);seed(store);req=request(store);store.close()
    class OfflineWorker:
        def run(self,*args):raise RuntimeError("offline")
    component_class=load_component("ReviewResearch")
    monkeypatch.setattr(sys.modules[component_class.__module__],"configured_symbolic_worker",lambda:OfflineWorker())
    component=component_class().set(payload=req.model_dump(mode="json"),
        model=object(),model_manifest_json=MANIFEST.model_dump_json(),allow_audit_writes=True,
        allow_model_calls=True,allow_counterexamples=True,counterexample_target_ids_json='["step"]')
    result=asyncio.run(component.result_data()).data
    assert result["status"]=="unavailable"
    assert result["diagnostics"][0]["code"]=="symbolic_worker_unavailable"
