"""Actual Read Evidence graph and inert previews in the installed Langflow runtime."""
import asyncio
import json
import hashlib
from pathlib import Path
import sys
import pytest

pytest.importorskip("lfx")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"deploy"))
from build_read_evidence import build
from build_tool_guide import load_component
from flow_io import validate_edges
from test_source_canvas import configured, execute as prepare
from test_evidence_reader import publish

CANVAS=ROOT/"examples/langflow_replacement/read_evidence.json"


def execute(payload,project="research"):
    from lfx.graph import Graph
    flow=json.loads(CANVAS.read_text())
    node=next(n for n in flow["data"]["nodes"] if n["data"]["type"]=="ReadEvidence")
    node["data"]["node"]["template"]["project_id"]["value"]=project or ""
    graph=Graph.from_payload(flow["data"])
    async def run():
        await asyncio.wait_for(graph.arun(inputs=[{"input_value":json.dumps(payload)}],outputs=["ChatOutput-evidence"]),30)
        component=graph.get_vertex("ReadEvidence-nima").custom_component
        result,table,preview=await asyncio.gather(component.result_data(),component.table_data(),component.preview_message())
        assert preview.text.startswith("```json\n") and preview.text.endswith("\n```")
        assert preview.text.count("```")==2
        assert "<script>" not in preview.text
        assert json.loads(preview.text[8:-4])==result.data
        assert len(table)==("kind" in result.data["data"])
        return result.data
    return asyncio.run(run())


def test_snapshot_edges_and_operator_scope():
    flow=json.loads(CANVAS.read_text())
    assert flow==build()
    validate_edges(flow)
    assert len(flow["data"]["nodes"])==4
    for node in flow["data"]["nodes"]:
        name=node["data"]["type"];template=node["data"]["node"]["template"]
        if name in ("EvidenceReadFields","ReadEvidence"):
            source=(ROOT/"deploy/langflow_components/nima_tools"/(name+".py")).read_text()
            assert template["code"]["value"]==source
            assert node["data"]["node"]["metadata"]["source_sha256"]==hashlib.sha256(source.encode()).hexdigest()
        for key in ("corpus_id","project_id"):
            if key in template:
                assert not template[key].get("tool_mode") and not template[key].get("input_types")


def test_unconfigured(monkeypatch):
    monkeypatch.delenv("NIMA_STORE_ROOT",raising=False)
    assert execute({"artifact_id":"0"*64})["status"]=="unavailable"


def test_exact_region_reference_and_no_writes(configured):
    from nima_semantica.storage import GraphStore
    prepared=prepare({"mode":"prepare_index","operation_id":"read-fixture"},writes=True)
    store=GraphStore(configured);before=(store.revision,store.records());store.close()
    result=execute({"region_id":prepared["data"]["region_ids"][0]})
    assert result["status"]=="complete",result
    assert result["data"]["content"]=="# Inspection fixture\n\nFor x = 2, $x^2 = 4$.\n"
    assert execute({"reference":result["data"]["reference"]})==result
    assert execute({"region_id":prepared["data"]["region_ids"][0],"max_bytes":1})["status"]=="failed"
    store=GraphStore(configured)
    assert (store.revision,store.records())==before
    store.close()


def test_binary_and_inert_html_render(configured):
    from nima_semantica.storage import GraphStore
    import base64
    store=GraphStore(configured)
    raw=b"%PDF-1.7\xff\x00binary"
    binary=publish(store,raw,media="application/pdf")
    text='```\n<script>alert("x")</script>\n<img src="https://invalid.test/x">'
    html_id=publish(store,text.encode(),media="text/html")
    store.close()
    result=execute({"artifact_id":binary,"encoding":"base64"})
    assert base64.b64decode(result["data"]["content"])==raw
    rendered=execute({"artifact_id":html_id,"render":"escaped_html"})
    assert rendered["data"]["content"]==text
    assert "<script>" not in rendered["data"]["rendered"]["content"]


def test_foreign_project_and_corpus_only_denied(configured):
    from nima_semantica.storage import GraphStore
    store=GraphStore(configured);digest=publish(store,b"Private",project="private");store.close()
    assert execute({"artifact_id":digest})["status"]=="failed"
    assert execute({"artifact_id":digest},project=None)["status"]=="failed"
    assert execute({"artifact_id":digest},project="private")["data"]["content"]=="Private"


@pytest.mark.parametrize("extra", [{"corpus_id":"foreign"},{"project_id":"private"},{"store_path":"/tmp/secret"},
    {"allow_writes":True},{"max_bytes":False},{"render":"execute"}])
def test_public_authority_and_invalid_inputs_rejected(configured,extra):
    result = execute({"artifact_id":"0"*64}|extra)
    assert result["status"] == "failed" and result["data"]["executed"] is False


def test_native_reference_preserves_latex_quotation():
    from lfx.schema import Data
    reference={"region_id":"r","artifact_id":"0"*64,"content_hash":"0"*64,"corpus_id":"papers",
        "source_revision":"1","quotation":r"x \neq 0"}
    component=load_component("EvidenceReadFields")().set(mcp_request=Data(data={"reference":reference}))
    assert asyncio.run(component.result_data()).data["reference"]["quotation"]==reference["quotation"]
