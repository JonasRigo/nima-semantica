"""Installed Langflow canvas acceptance with explicitly injected offline doubles."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_verify_lean import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured
from test_verify_lean_tool import Verifier

CANVAS=ROOT/"examples/langflow_replacement/verify_lean.json"


def execute(payload, *, writes=False):
    from lfx.graph import Graph
    flow=json.loads(CANVAS.read_text())
    node=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="VerifyLean")
    for name in ("allow_execution","allow_audit_writes"):
        node["data"]["node"]["template"][name]["value"]=writes
    graph=Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}], outputs=["ChatOutput-leanverification"]),30)
        c=graph.get_vertex("VerifyLean-nima").custom_component
        result,preview=await asyncio.gather(c.result_data(),c.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())


def test_snapshot_and_operator_authority():
    flow=json.loads(CANVAS.read_text());assert flow==build();validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name in ("VerifyLeanFields","VerifyLean"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_execution","allow_audit_writes","allow_model_calls","check_plan_base64","max_actions","timeout_seconds","model_manifest_json"):
            if key in template:
                assert not template[key].get("input_types") and not template[key].get("tool_mode")
    for node in flow["data"]["nodes"]:
        template=node["data"]["node"]["template"]
        assert not {"model","retrieval_policy","allow_model_calls"} & set(template)


def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({})["data"]["executed"] is False


def test_disabled_execution_no_writes(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);before=store.revision;store.close()
    assert execute({"mode":"verify","operation_id":"denied"})["status"]=="failed"
    store=GraphStore(configured);assert store.revision==before;store.close()


def test_direct_with_explicit_worker_fixture(configured,monkeypatch):
    worker=Verifier()
    monkeypatch.setattr("nima_semantica.lean_transport.configured_lean_verifier",lambda:worker)
    result=execute({"mode":"verify","operation_id":"fixture"},writes=True)
    assert result["status"]=="complete" and len(worker.calls)==1
    assert execute({"mode":"verify","operation_id":"fixture"},writes=True)==result
    assert len(worker.calls)==1


@pytest.mark.parametrize("extra",[{"allow_execution":True},{"corpus_id":"private"},{"check_plan":{}},{"worker":"evil"},{"max_actions":100}])
def test_public_override_denied(configured,extra):
    with pytest.raises(Exception):execute(extra)


def test_result_branches_execute_once_and_expose_exact_types(configured):
    verifier=Verifier()
    component=load_component("VerifyLean")().set(payload={"mode":"verify","operation_id":"branches"},
        verifier=verifier,allow_execution=True,allow_audit_writes=True)
    async def run():
        result,preview,table=await asyncio.gather(component.result_data(),component.preview_message(),component.table_data())
        assert result.data["status"]=="complete",result.data
        assert len(table)==1 and json.loads(preview.text[8:-4])==result.data
    asyncio.run(run())
    assert len(verifier.calls)==1


@pytest.mark.skipif(os.environ.get("NIMA_LIVE_LEAN")!="1",reason="requires pinned isolated Lean worker")
def test_installed_canvas_with_real_pinned_worker(configured):
    result=execute({"mode":"verify","operation_id":"real-canvas-lean"},writes=True)
    assert result["status"]=="complete",result
    assert result["data"]["formal_verification_accepted"]
    assert result["data"]["verification"]["project_result"]["kernel_replay_succeeded"]
    assert result["data"]["verification"]["project_result"]["declaration_types"]=={"target":"Lean.Expr.const `True []"}
