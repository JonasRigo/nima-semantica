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
from build_draft_lean import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured
from test_lean_draft import region,actions,request
from test_claim_dependencies import seed,ref

CANVAS=ROOT/"examples/langflow_replacement/draft_lean.json"


def execute(payload):
    from lfx.graph import Graph
    graph=Graph.from_payload(json.loads(CANVAS.read_text())["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],outputs=["ChatOutput-draft_lean"]),30)
        component=graph.get_vertex("DraftLean-nima").custom_component
        result,preview=await asyncio.gather(component.result_data(),component.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())


def test_snapshot_and_operator_authority():
    flow=json.loads(CANVAS.read_text());assert flow==build();validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name not in ("ChatInput","ChatOutput"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_audit_writes","allow_model_calls","max_actions","model_manifest_json","enabled","projection_id","max_verifications","max_resolutions","allow_query_disclosure","endpoint","index_revision","max_results"):
            if key in template:assert not template[key].get("input_types") and not template[key].get("tool_mode")


def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({})["data"]["executed"] is False


def test_disabled_execution_no_writes(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);before=store.revision;revision=store.graph_revision("papers","research").model_dump(mode="json");store.close()
    assert execute({"mode":"draft","environment_digest":"a"*64,"operation_id":"denied","graph_revision":revision,"target":__import__("test_lean_draft").target().model_dump(mode="json")})["status"]=="failed"
    store=GraphStore(configured);assert store.revision==before;store.close()


@pytest.mark.parametrize("extra",[{"allow_audit_writes":True},{"corpus_id":"private"},{"model":"evil"},{"max_actions":100}])
def test_public_override_denied(configured,extra):
    with pytest.raises(Exception):execute(extra)


def test_connected_model_uses_native_actions_and_cached_outputs(configured):
    from types import SimpleNamespace
    from model_fixture import MANIFEST
    from nima_semantica.lean_draft_backend import DraftLeanBackend
    from nima_semantica.verify_lean_tool import verify_lean
    from test_verify_lean_tool import Verifier
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);seed(store);r=region(store);req=request(store,source_region_ids=(r.id,));store.close()
    sequence=iter(actions());bound=[]
    class Model:
        def bind_tools(self,tools,**options):bound.append((tools,options));return self
        def invoke(self,messages):
            action=next(sequence)
            return SimpleNamespace(tool_calls=[{"name":action["name"],"args":action["arguments"]}],invalid_tool_calls=[],
                usage_metadata={"input_tokens":10,"output_tokens":20},response_metadata={"finish_reason":"tool_calls"})
    component=load_component("DraftLean")().set(payload=req.model_dump(mode="json"),model=Model(),
        model_manifest_json=MANIFEST.model_dump_json(),allow_audit_writes=True,allow_model_calls=True,
        backend=DraftLeanBackend(True,Verifier,verify_lean))
    async def run():
        result,preview,table=await asyncio.gather(component.result_data(),component.preview_message(),component.table_data())
        assert result.data["status"]=="partial",result.data
        assert len(table)>3 and json.loads(preview.text[8:-4])==result.data
        view=load_component("LeanDraftStateView")().set(payload=result)
        assert (await view.result_data()).data["publishable"] is False
    asyncio.run(run())
    assert len(bound)==3
    assert all(opts=={"tool_choice":"required","parallel_tool_calls":False} for _,opts in bound)
    assert "retrieve_context" not in [t["function"]["name"] for t in bound[0][0]]


def test_policy_mismatch_rejected_before_execution(configured):
    component=load_component("DraftLean")().set(payload={},policy={"policy_digest":"foreign"})
    with pytest.raises(ValueError,match="policy differs"):asyncio.run(component.result_data())

def test_search_capability_is_lazy_and_requires_disclosure(monkeypatch):
    import urllib.request
    def forbidden(*args,**kwargs):pytest.fail("no network before disclosure approval")
    monkeypatch.setattr(urllib.request,"build_opener",forbidden)
    cap=asyncio.run(load_component("LeanSearch")().set(enabled=True).build_tool())
    assert cap.metadata["nima_lean_search"]["enabled"]
    with pytest.raises(ValueError,match="disclosure"):cap.invoke({"query":"True.intro"})

def test_verifier_capability_is_lazy_and_gated():
    cap=asyncio.run(load_component("LeanDraftVerification")().build_backend())
    assert not cap.enabled
    with pytest.raises(ValueError,match="not authorized"):cap.verify_exact(None,None,None,verifier=None)
