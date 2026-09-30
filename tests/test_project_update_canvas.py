"""Installed deterministic Langflow canvas execution without model doubles."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_update_project_graph import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured
from test_project_update import request,approved

CANVAS=ROOT/"examples/langflow_replacement/update_project_graph.json"


def execute(payload,writes=False,approval=None):
    from lfx.graph import Graph
    flow=json.loads(CANVAS.read_text())
    node=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="UpdateProjectGraph")
    node["data"]["node"]["template"]["allow_audit_writes"]["value"]=writes
    template=node["data"]["node"]["template"]
    template["allow_graph_writes"]["value"]=writes
    template["allow_projection_writes"]["value"]=writes
    template["approval_json"]["value"]=json.dumps(approval)
    graph=Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],outputs=["ChatOutput-projectupdate"]),30)
        component=graph.get_vertex("UpdateProjectGraph-nima").custom_component
        result,preview=await asyncio.gather(component.result_data(),component.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())


def test_snapshot_and_no_model_or_public_authority():
    flow=json.loads(CANVAS.read_text());assert flow==build();validate_edges(flow)
    assert len(flow["data"]["nodes"])==6 and len(flow["data"]["edges"])==5
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        assert "model" not in template and "worker" not in template
        if name not in ("ChatInput","ChatOutput"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_audit_writes","allow_graph_writes","allow_projection_writes","approval_json","actor","max_changes"):
            if key in template:assert not template[key].get("input_types") and not template[key].get("tool_mode")


def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({})["data"]["executed"] is False


def test_disabled_writes_and_real_graph_execution(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);native=request(store);req=native.model_dump(mode="json");approval=approved(native.delta).approval.model_dump(mode="json");before=store.revision;head=store.graph_revision("papers","research");store.close()
    assert execute(req)["status"]=="failed"
    store=GraphStore(configured);assert store.revision==before;store.close()
    result=execute(req,True,approval)
    assert result["status"]=="complete",result
    assert result["data"]["committed"] and result["data"]["projection"]["status"]=="completed"
    assert execute(req,True,approval)==result
    store=GraphStore(configured);assert store.graph_revision("papers","research").project_revision==head.project_revision+1;store.close()


@pytest.mark.parametrize("extra",[{"allow_audit_writes":True},{"project_id":"private"},{"rules":[]},{"max_facts":99999}])
def test_public_override_denied(configured,extra):
    result = execute(extra)
    assert result["status"] == "failed" and result["data"]["executed"] is False
    assert result["diagnostics"][0]["code"] == "request.invalid_field"


def test_policy_mismatch_rejected(configured):
    component=load_component("UpdateProjectGraph")().set(payload={},policy={"policy_digest":"foreign"})
    with pytest.raises(ValueError,match="policy differs"):asyncio.run(component.result_data())
