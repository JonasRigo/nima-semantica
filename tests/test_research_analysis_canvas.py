"""Installed canvas acceptance; deterministic and offline."""
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
import pytest
pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_save_research_analysis import build
from flow_io import validate_edges
from test_source_canvas import configured
from test_research_analysis import request
from nima_semantica.storage import GraphStore

CANVAS=ROOT/"examples/langflow_replacement/save_research_analysis.json"

def execute(payload,writes=False):
    from lfx.graph import Graph
    flow=json.loads(CANVAS.read_text())
    node=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="SaveResearchAnalysis")
    node["data"]["node"]["template"]["allow_artifact_writes"]["value"]=writes
    graph=Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],outputs=["ChatOutput-researchanalysis"]),30)
        component=graph.get_vertex("SaveResearchAnalysis-nima").custom_component
        result,preview=await asyncio.gather(component.result_data(),component.preview_message())
        assert json.loads(preview.text[8:-4])==result.data
        return result.data
    return asyncio.run(run())

def test_snapshot_and_authority():
    flow=json.loads(CANVAS.read_text())
    assert flow==build()
    assert flow["endpoint_name"]=="nima-save-research-analysis"
    validate_edges(flow)
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name in ("SaveAnalysisFields","SaveResearchAnalysis","ResearchAnalysisView"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id","allow_artifact_writes"):
            if key in template:assert not template[key].get("input_types") and not template[key].get("tool_mode")

def test_unconfigured_preview(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    result=execute({"mode":"preview"})
    assert not result["data"]["executed"] and not result["receipt_ids"] and not result["artifacts"]

def test_save_canvas_and_replay(configured):
    with closing(GraphStore(configured)) as store:payload=request(store).model_dump(mode="json")
    assert execute(payload)["status"]=="failed"
    result=execute(payload,True)
    assert result["status"]=="complete",result
    assert execute(payload,True)==result
    with closing(GraphStore(configured)) as store:
        assert len(store.records("ResearchAnalysisBundle"))==1
        assert not store.records("OKFNode")
