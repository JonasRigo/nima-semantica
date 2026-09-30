import threading

import pytest

from nima_semantica.proof_mcp import ProofConfig, ProofService


@pytest.fixture
def service(tmp_path):
    return ProofService(ProofConfig(database_path=str(tmp_path / "proof.sqlite"), project_id="p"))


def opened(service, mode="conduct"):
    return service.invoke("open", {"request_id": "open", "target": "Prove the sum is even", "mode": mode})


def action(service, session, operation, **values):
    return service.invoke(operation, {"session_id": session["session_id"], "expected_revision": session["revision"],
                                      "request_id": operation + str(session["revision"]), **values})


def test_obligations_replay_reopen_and_uncertified_completion(service):
    s = opened(service)
    gap = action(service, s, "record_node", kind="obligation", statement="Show closure under addition")
    step = action(service, gap, "record_node", kind="step", statement="2a+2b=2(a+b)", depends_on=["target"])
    submitted = action(service, step, "submit", conclusion_node=step["result"]["node_id"], argument="The sum is twice an integer.")
    assert not submitted["result"]["ready"]
    resolved = action(service, submitted, "resolve_obligation", obligation_id=gap["result"]["node_id"], support_nodes=[step["result"]["node_id"]], rationale="Integers are closed under addition.")
    submitted = action(service, resolved, "submit", conclusion_node=step["result"]["node_id"], argument="The sum is twice an integer.")
    assert submitted["result"]["ready"]
    closed = action(service, submitted, "close", outcome="completed", summary="Uncertified argument recorded")
    assert closed["scientific_status"] == "uncertified"
    reopened = ProofService(service.config)
    assert reopened.read("status", {"session_id": s["session_id"]})["lifecycle"] == "completed"
    assert action(reopened, submitted, "close", outcome="completed", summary="Uncertified argument recorded") == closed
    assert action(reopened, closed, "record_node", kind="step", statement="late")["status"] == "rejected"


def test_scope_stale_divergent_and_invalid_dependency(service):
    s = opened(service)
    foreign = ProofService(service.config.model_copy(update={"project_id": "foreign"}))
    with pytest.raises(ValueError, match="foreign"):
        foreign.read("export", {"session_id": s["session_id"]})
    invalid = action(service, s, "record_node", kind="step", statement="bad", depends_on=["foreign"])
    assert invalid["status"] == "rejected" and invalid["revision"] == 0
    assert action(service, s, "record_node", kind="step", statement="bad", depends_on=["foreign"]) == invalid
    assert action(service, s, "record_node", kind="step", statement="changed")["diagnostic"] == "divergent request replay"
    s = service.invoke("record_node", {"session_id": s["session_id"], "request_id": "new", "expected_revision": 0, "kind": "step", "statement": "valid"})
    assert action(service, {**s, "revision": 0}, "cancel")["diagnostic"] == "stale revision"


def test_superseded_support_reopens_obligation(service):
    s = opened(service)
    gap = action(service, s, "record_node", kind="obligation", statement="lemma")
    old = action(service, gap, "record_node", kind="lemma", statement="old lemma")
    resolved = action(service, old, "resolve_obligation", obligation_id=gap["result"]["node_id"], support_nodes=[old["result"]["node_id"]], rationale="argument")
    new = action(service, resolved, "record_node", kind="lemma", statement="corrected lemma", supersedes=old["result"]["node_id"])
    frontier = service.read("frontier", {"session_id": s["session_id"]})
    assert gap["result"]["node_id"] in frontier["open_obligations"]
    assert new["status"] == "complete"


def test_cancel_retains_late_worker_result_without_applying_it(service):
    started, finish = threading.Event(), threading.Event()
    class Worker:
        def run(self, source, timeout):
            started.set(); assert finish.wait(5)
            return {"outcome": "executed", "exit_code": 0, "stdout": "2"}
    service.config = service.config.model_copy(update={"allow_execution": True})
    service.worker = Worker()
    s = opened(service)
    responses = []
    thread = threading.Thread(target=lambda: responses.append(action(service, s, "run_experiment", source="print(1+1)", purpose="check")))
    thread.start(); assert started.wait(5)
    cancelled = action(service, s, "cancel")
    finish.set(); thread.join(5)
    assert cancelled["status"] == "complete" and responses[0]["status"] == "interrupted"
    export = service.read("export", {"session_id": s["session_id"]})
    assert set(export["graph"]["nodes"]) == {"target"}
    assert any("late_result" in r["result"] for r in export["receipts"])


def test_draft_can_retain_open_work_but_inputs_cannot_be_submitted(service):
    s = opened(service, "draft")
    rejected = action(service, s, "submit", conclusion_node="target", argument="assume target")
    assert rejected["status"] == "rejected"
    strategy = action(service, s, "record_node", kind="strategy", statement="Prove an intermediate lemma")
    gap = action(service, strategy, "record_node", kind="obligation", statement="intermediate lemma")
    submitted = action(service, gap, "submit", conclusion_node=strategy["result"]["node_id"], argument="Draft route")
    assert submitted["result"]["ready"] and submitted["result"]["open_obligations"]


def test_real_stdio_proof_lifecycle_and_resume(tmp_path):
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from nima_semantica.proof_mcp import TOOLS
    config = tmp_path / "proof.json"
    config.write_text(json.dumps({"database_path": str(tmp_path / "proof.sqlite"), "project_id": "p"}))
    params = StdioServerParameters(command=sys.executable, args=["-m", "nima_semantica.proof_mcp", "--config", str(config)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})

    async def exercise():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert {t.name for t in (await client.list_tools()).tools} == {"nima_proof_" + op for op in TOOLS} | {"nima_proof_tools"}
                discovery = await client.call_tool("nima_proof_tools", {})
                assert set(json.loads(discovery.content[0].text)["operations"]) == set(TOOLS)
                invalid = await client.call_tool("nima_proof_open", {"request": {"unexpected": "SECRET"}})
                assert invalid.isError and "request.invalid_field" in invalid.content[0].text
                assert "SECRET" not in invalid.content[0].text
                async def call(op, request):
                    response = await client.call_tool("nima_proof_" + op, {"request": request})
                    assert not response.isError, response
                    return json.loads(response.content[0].text)
                opened = await call("open", {"request_id": "transport", "target": "Prove even plus even is even", "mode": "conduct"})
                sid = opened["session_id"]
                node = await call("record_node", {"session_id": sid, "request_id": "step", "expected_revision": 0,
                    "kind": "step", "statement": "2a+2b=2(a+b) for integers a,b", "depends_on": ["target"]})
                submitted = await call("submit", {"session_id": sid, "request_id": "argument", "expected_revision": node["revision"],
                    "conclusion_node": node["result"]["node_id"], "argument": "Integer closure gives the stated form."})
                closed = await call("close", {"session_id": sid, "request_id": "closed", "expected_revision": submitted["revision"],
                    "outcome": "completed", "summary": "Uncertified argument"})
                assert closed["scientific_status"] == "uncertified"
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                response = await client.call_tool("nima_proof_status", {"request": {"session_id": sid}})
                assert json.loads(response.content[0].text)["lifecycle"] == "completed"
    asyncio.run(asyncio.wait_for(exercise(), 30))
