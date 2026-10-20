"""Extraction failures remain actionable across provider, receipt and MCP layers."""
import asyncio
import copy
import json
import sys
import tomllib
from pathlib import Path

import pytest

from nima_semantica.cli import install_harness
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.installation import Installation, ModelProfile, write_json
from nima_semantica.deep_extraction_tool import deep_extraction
from nima_semantica.providers import Invocation
from nima_semantica.model_runtime import ModelRequestTimeout
from model_fixture import MANIFEST
from test_deep_extraction import region, request, context, Model, actions
from test_source_pipeline import store


def test_fresh_codex_install_retains_research_timeout_and_replays(tmp_path):
    config=tmp_path/"installation.json"
    write_json(config,Installation(data_root=str(tmp_path/"data"),llm=ModelProfile(model="fixture")).model_dump())
    binding={"corpus_id":"papers","project_id":"research","store_root":str(tmp_path/"store"),
        "math_config":str(tmp_path/"math.json"),"proof_config":str(tmp_path/"proof.json"),
        "mcp_url":"http://127.0.0.1:7860/api/v1/mcp/project/fixture/streamable"}
    root=tmp_path/"workspace"
    install_harness(root,config,binding,["codex","claude","opencode"])
    path=root/".codex/config.toml"
    before=path.read_bytes()
    servers=tomllib.loads(before.decode())["mcp_servers"]
    assert all(servers[name]["tool_timeout_sec"]==3600 for name in ("nima","nima-math","nima-proof"))
    assert servers["nima"]["startup_timeout_sec"]==30 and servers["nima"]["url"]==binding["mcp_url"]
    install_harness(root,config,binding,["codex","claude","opencode"])
    assert path.read_bytes()==before
    assert "tool_timeout_sec" not in (root/".mcp.json").read_text()
    assert "tool_timeout_sec" not in (root/"opencode.json").read_text()


def test_legacy_reconciliation_restores_timeout_and_preserves_policies(tmp_path):
    import base64
    from nima_semantica.config_reconciliation import reconciliation_preview
    config=tmp_path/"installation.json"; config.write_text("{}")
    binding={"corpus_id":"papers","project_id":"research","store_root":str(tmp_path/"store"),
        "mcp_url":"http://127.0.0.1:7860/api/v1/mcp/project/fixture/streamable"}
    for kind in ("math","proof"):
        path=tmp_path/(kind+".json")
        path.write_text(json.dumps({"corpus_id":"papers","project_id":"research"}));path.chmod(0o600)
        binding[kind+"_config"]=str(path)
    root=tmp_path/"workspace";(root/".codex").mkdir(parents=True)
    path=root/".codex/config.toml"
    original='[mcp_servers.nima]\nurl="'+binding["mcp_url"]+'"\nenabled=false\n[mcp_servers.nima.tools.deep_extraction]\napproval_mode="approve"\n'
    path.write_text(original)
    preview=reconciliation_preview(root,config,binding,["codex"])
    planned=next(x for x in preview["changes"] if x["path"]==str(path))
    server=tomllib.loads(base64.b64decode(planned["payload_base64"]).decode())["mcp_servers"]["nima"]
    assert server["tool_timeout_sec"]==3600 and server["enabled"] is False
    assert server["tools"]["deep_extraction"]["approval_mode"]=="approve"
    assert path.read_text()==original


@pytest.mark.parametrize("error",["truncated","invalid_json"])
def test_model_envelope_failure_feedback_and_authoritative_receipts(store,error):
    r=region(store);captured=[]
    def model(prompt):
        captured.append(copy.deepcopy(prompt))
        return Invocation(result={},manifest=MANIFEST,input_tokens=2,output_tokens=8,
            error=error,finish_reason="length" if error=="truncated" else "stop",raw_output="private raw completion")
    result=deep_extraction(store,request(r),context(max_actions=2),model=model)
    assert result.status=="failed" and result.data["model_error_count"]==2
    assert result.data["last_model_error"]["code"]=="model."+error
    feedback=json.loads(captured[1]["messages"][-2]["content"])
    assert feedback["code"]=="model."+error and "no action was executed" in feedback["instruction"]
    if error=="truncated":assert "output budget" in feedback["instruction"]
    assert {d["code"] for d in result.diagnostics}=={"extraction.failed","model."+error}
    receipts=ExecutionReceiptService(store)
    parent=receipts.get(result.receipt_ids[0],corpus_id="papers",project_id="research")
    assert parent.metadata["result"]["diagnostics"]==list(result.diagnostics)
    children=[receipts.get(ref,corpus_id="papers",project_id="research") for ref in result.receipt_ids[1:]]
    assert all(x.error==error and x.metadata["output"]["raw_output"]=="private raw completion" for x in children)
    assert not store.records("GraphArtifactProposal")
    assert deep_extraction(store,request(r),context(max_actions=2),model=model)==result
    assert len(captured)==2


def test_provider_timeout_is_distinct_and_does_not_expose_exception_payload(store):
    r=region(store)
    result=deep_extraction(store,request(r),context(),model=Model([ModelRequestTimeout(180)]))
    assert result.status=="failed"
    assert result.diagnostics[0]["code"]=="model.request_timeout"
    assert result.diagnostics[0]["timeout_seconds"]==180
    assert "graph_proposal" not in result.artifacts


def test_recovered_truncation_is_partial_with_diagnostic_not_failed(store):
    r=region(store);sequence=iter(actions(r));count=0
    def model(prompt):
        nonlocal count
        count+=1
        if count==1:return Invocation(result={},manifest=MANIFEST,input_tokens=1,output_tokens=1,error="truncated",finish_reason="length")
        return Invocation(result=next(sequence),manifest=MANIFEST,input_tokens=1,output_tokens=1)
    result=deep_extraction(store,request(r),context(),model=model)
    assert result.status=="partial" and result.artifacts["graph_proposal"]
    assert [d["code"] for d in result.diagnostics]==["model.truncated"]


@pytest.mark.parametrize("status,should_raise",[("failed",True),("unavailable",True),("partial",False),("complete",False)])
def test_mcp_failure_flag_path_keeps_visual_preview_and_cached_execution(status,should_raise):
    pytest.importorskip("lfx")
    from lfx.observability import execution_protocol
    from mcp import types
    from mcp.server.lowlevel import Server
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"deploy"))
    from build_tool_guide import load_component
    component=load_component("DeepExtraction")()
    value={"operation":"Deep Extraction","status":status,"diagnostics":[{"code":"model.truncated"}],
        "receipt_ids":["parent"],"artifacts":{"outcome":"retained"},"data":{"raw_output":"private completion"}}
    calls=[]
    async def run():
        calls.append(True)
        return value
    component._checked_run=run
    async def verify():
        server=Server("extraction-reporting-regression")
        @server.list_tools()
        async def tools():
            return [types.Tool(name="deep_extraction",inputSchema={"type":"object"})]
        @server.call_tool()
        async def call(name,arguments):
            with execution_protocol("mcp"):
                return [types.TextContent(type="text",text=(await component.preview_message()).text)]
        wire=(await server.request_handlers[types.CallToolRequest](types.CallToolRequest(
            params=types.CallToolRequestParams(name="deep_extraction",arguments={})))).root
        assert wire.isError is should_raise
        if should_raise:
            assert "private completion" not in wire.content[0].text
            assert json.loads(wire.content[0].text)["receipt_ids"]==["parent"]
        with execution_protocol("mcp"):
            if should_raise:
                with pytest.raises(RuntimeError) as caught:await component.preview_message()
                summary=json.loads(str(caught.value))
                assert summary["receipt_ids"]==["parent"] and summary["artifacts"]["outcome"]=="retained"
                assert "private completion" not in str(caught.value)
            else:assert json.loads((await component.preview_message()).text[8:-4])["status"]==status
        assert json.loads((await component.preview_message()).text[8:-4])["status"]==status
        assert (await component.result_data()).data==value
    asyncio.run(verify())
    assert len(calls)==1
