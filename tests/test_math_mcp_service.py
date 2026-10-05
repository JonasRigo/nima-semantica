"""Standalone regressions for the maintained graph implementation."""
import asyncio
import json
import os
import sys
import threading
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
from nima_semantica.math_mcp_contracts import invoke


@pytest.fixture
def service(tmp_path):
    return MathSessionService(MathServiceConfig(database_path=str(tmp_path / "private.sqlite"),
        project_id="p", run_id="r", allow_execution=True))


def opened(service):
    return invoke(service, "open", {"request_id": uuid.uuid4().hex, "task": "Compute one plus one", "required_paths": ["answer"]})["session_id"]


def step(service, sid, rid="a", revision=0):
    return invoke(service, "record_step", dict(session_id=sid, request_id=rid,
        expected_revision=revision, kind="claim", statement="A proposal", value=2, depends_on=["task"]))


def test_replay_reopen_scope_and_stale(service):
    created = invoke(service, "open", {"request_id": "create", "task": "A", "required_paths": ["answer"]})
    assert invoke(service, "open", {"request_id": "create", "task": "A", "required_paths": ["answer"]}) == created
    with pytest.raises(ValueError, match="divergent session creation"):
        invoke(service, "open", {"request_id": "create", "task": "B", "required_paths": ["answer"]})
    sid = opened(service)
    first = step(service, sid)
    assert step(service, sid) == first
    reopened = MathSessionService(service.config)
    assert reopened.read(sid, "export") == service.read(sid, "export")
    assert step(service, sid, "stale")["result"]["diagnostic"] == "stale session revision"
    with pytest.raises(ValueError, match="divergent"):
        step(service, sid, revision=1)
    foreign = MathSessionService(service.config.model_copy(update={"project_id": "foreign"}))
    with pytest.raises(ValueError, match="scope"):
        foreign.read(sid)
    with pytest.raises(ValueError, match="exact original"):
        service.open("different", ["answer"], session_id=sid)
    assert service.read(sid, "inspect_action", {"request_id": "stale"})["result"]["outcome"]["status"] == "rejected"


def test_partial_and_terminal(service):
    sid = opened(service)
    first = step(service, sid)
    node = first["result"]["node"]["id"]
    result = service.mutate(sid, "submit", 1, "submit", {"answer": {"answer": 2}, "support_nodes": {"answer": node}})
    assert not result["result"]["admitted"]
    assert result["result"]["repair_guidance"]["unresolved"][0]["node"]["id"] == node
    assert service.mutate(sid, "complete", 1, "close", {"outcome": "completed"})["status"] == "rejected"
    partial = service.mutate(sid, "partial", 1, "close", {"outcome": "partial", "candidate": 2})
    assert partial["result"]["frontier"]["open_obligations"]
    assert step(service, sid, "late", 2)["result"]["diagnostic"] == "terminal session"
    assert service.read(sid, "status")["result"]["state"] == "partial"


def test_bundle_repair_supplies_exact_children_without_selecting_them(service):
    sid = opened(service)
    proposal = invoke(service, "record_step", dict(session_id=sid, request_id="bundle",
        expected_revision=0, kind="claim", statement="Two separate claims",
        value={"a": 2, "b": 3}, depends_on=["task"]))["result"]["node"]
    before = service.read(sid, "export")
    args = dict(session_id=sid, request_id="bad-parent", expected_revision=1,
        kind="claim", statement="Dependent claim", value=5, depends_on=[proposal["id"]])
    failed = invoke(service, "record_step", args)
    assert failed["status"] == "rejected"
    assert failed["result"]["repair_guidance"]["atomic_dependencies"] == {proposal["id"]: proposal["atomic_nodes"]}
    assert invoke(service, "record_step", args) == failed
    assert service.read(sid, "export") == before


def test_symbol_wrapper_lossless_normalization_and_no_assumption_loss(service, monkeypatch):
    from nima_semantica.math_graph_session import PrivateGraphSessionStore
    sid = service.open("Calculate x", ["answer"])["session_id"]
    seen = []
    def dispatch(graph, binding, name, args, **kwargs):
        from nima_semantica.math_graph_program import compile_graph_program
        seen.append(args)
        compile_graph_program(graph, args["steps"], args["outputs"], binding.indexed_sets)
        return {"compiled": True}, False
    monkeypatch.setattr(PrivateGraphSessionStore, "_dispatch", staticmethod(dispatch))
    args = dict(session_id=sid, request_id="symbol", expected_revision=0,
        steps=[dict(id="x", op="symbol", value={"name": "x"}, meaning="Unknown quantity")],
        outputs={"answer": "x"}, purpose="Representation check")
    response = invoke(service, "run_calculation_graph", args)
    assert response["status"] == "completed"
    assert seen[-1]["steps"][0]["value"] == "x"
    assert response["result"]["interface_normalizations"]
    original = service.read(sid, "inspect_action", {"request_id": "symbol"})["result"]["attempt"]
    assert json.loads(original["arguments_json"])["steps"][0]["value"] == {"name": "x"}
    args["request_id"] = "assumptions"
    args["steps"][0]["value"]["positive"] = True
    response = invoke(service, "run_calculation_graph", args)
    assert response["status"] == "rejected"
    assert "symbol" in response["result"]["repair_guidance"]["instruction"]


def test_index_contract_discovery_and_rejection_guidance(service):
    from nima_semantica.math_mcp_contracts import operation_catalogue
    created = invoke(service, "open", {"request_id": "indexed", "task": "Sum x over both branches",
        "required_paths": ["answer"],
        "task_facts": [{"fact_id": "branches", "kind": "condition", "quote": "both branches", "required_paths": ["answer"]}],
        "indexed_sets": [{"set_id": "branches", "fact_id": "branches", "members": ["left", "right"], "required_paths": ["answer"]}]})
    assert created["result"]["task_facts"][0]["fact_id"] == "branches"
    assert created["result"]["indexed_sets"][0]["members"] == ["left", "right"]
    class NoExecution:
        def run(self, *args):
            pytest.fail("Invalid proposal must not execute")
    service.worker = NoExecution()
    args = dict(session_id=created["session_id"], request_id="missing", expected_revision=0,
        steps=[dict(id="x", op="symbol", value="x", meaning="Unresolved quantity")],
        outputs={"answer": "x"}, purpose="Missing member derivation")
    failed = invoke(service, "run_calculation_graph", args)
    assert failed["status"] == "rejected"
    assert failed["result"]["repair_guidance"]["indexed_sets"] == created["result"]["indexed_sets"]
    assert service.read(created["session_id"], "status")["revision"] == 0
    assert operation_catalogue()["indexed_example"]["steps"][-1]["op"] == "sum"




def test_cancel_concurrent_execution_and_no_late_admission(service):
    sid = opened(service)
    started, release = threading.Event(), threading.Event()
    class Worker:
        def run(self, source, timeout):
            started.set()
            assert release.wait(5)
            return {"outcome": "executed", "exit_code": 0, "stdout": "2", "stderr": ""}
    service.worker = Worker()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(service.mutate, sid, "slow", 0, "run_experiment",
            {"source": "print(2)", "depends_on": [], "purpose": "test"})
        assert started.wait(5)
        assert service.read(sid, "status")["result"]["active_request"] == "slow"
        assert step(service, sid, "busy")["status"] == "rejected"
        service.mutate(sid, "cancel", 0, "cancel", {})
        release.set()
        assert future.result()["status"] == "cancelled"
    assert service.read(sid)["result"]["node_count"] == 1
    assert service.read(sid, "inspect_action", {"request_id": "slow"})["result"]["outcome"]["status"] == "cancelled"
    assert "unadmitted_late_result" in service.read(sid, "inspect_action", {"request_id": "slow"})["result"]


def test_failed_persistence_recovery_no_reexecution(service, monkeypatch):
    sid = opened(service)
    original = service._save_outcome
    monkeypatch.setattr(service, "_save_outcome", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        step(service, sid)
    monkeypatch.setattr(service, "_save_outcome", original)
    assert service.read(sid)["result"]["node_count"] == 1
    service.recover_interrupted()
    assert step(service, sid)["status"] == "interrupted"
    assert service.read(sid)["result"]["node_count"] == 1


def test_capabilities_and_forged_handles(service):
    disabled = MathSessionService(service.config.model_copy(update={"allow_execution": False}))
    sid = opened(disabled)
    result = disabled.mutate(sid, "x", 0, "run_experiment", {"source": "print(2)"})
    assert result["status"] == "rejected"
    assert disabled.mutate(sid, "r", 0, "retrieve_context", {"query": "x", "purpose": "x"})["status"] == "rejected"
    for kind, key in (("node", "node_id"), ("receipt", "receipt_id"), ("action", "request_id")):
        with pytest.raises(ValueError):
            disabled.read(sid, "inspect_" + kind, {key: "forged"})


def test_retrieval_exact_provenance_preview(service, monkeypatch, lexical_installation):
    from nima_semantica import math_retrieval
    text = "Exact method. " * 100
    passage = {"region_id": "region", "text": text, "evidence": {"source_revision": "v1", "artifact_sha256": "a" * 64}, "locator": {"page": 1}}
    monkeypatch.setattr(math_retrieval, "retrieve_math_context", lambda *args: {
        "passages": [passage], "projection_revision": "p1", "projection_id": "projection", "truncated": False})
    service = MathSessionService(service.config.model_copy(update={"allow_retrieval": True, "retrieval_projection_id": "projection"}), research_store=object())
    sid = opened(service)
    response = invoke(service, "retrieve_context", {"session_id": sid, "request_id": "r", "expected_revision": 0, "query": "method", "purpose": "understand"})
    node = response["result"]["source_nodes"][0]
    assert len(node["preview"]) == 800 and node["preview_truncated"]
    exact = service.read(sid, "inspect_node", {"node_id": node["id"]})["result"]["node"]
    assert exact["value"] == text
    assert exact["retrieval_provenance"]["evidence"] == passage["evidence"]
    assert exact["status"] == "observed_text"


def test_real_retrieval_no_research_writes(service, store, lexical_installation):
    pytest.importorskip("semantica", reason="real ingestion requires the installed Semantica environment")
    from test_source_pipeline import pipeline, context
    from nima_semantica.corpus_registry import CorpusRegistry
    from nima_semantica.registry_contracts import CorpusDescriptor
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
    _, _, prepared = pipeline(store, ctx=context(project_id="p"))
    service = MathSessionService(service.config.model_copy(update={"allow_retrieval": True,
        "retrieval_projection_id": prepared.data["projection"]["projection_id"]}), research_store=store)
    before = store.revision
    sid = opened(service)
    result = invoke(service, "retrieve_context", dict(session_id=sid, request_id="source", expected_revision=0, query="Claim evidence", purpose="Exact method", mode="lexical"))
    assert result["status"] == "completed", result
    assert result["result"]["source_nodes"]
    assert store.revision == before


@pytest.mark.integration
@pytest.mark.symbolic
def test_isolated_calculation_submission_and_failed_receipt(service):
    from nima_semantica.symbolic_transport import configured_symbolic_worker
    service.worker = configured_symbolic_worker()
    sid = opened(service)
    result = invoke(service, "run_calculation_graph", dict(session_id=sid, request_id="calc", expected_revision=0,
        steps=[{"id": "one", "op": "integer", "value": 1, "meaning": "Arithmetic unit"},
            {"id": "two", "op": "add", "args": ["one", "one"], "meaning": "Sum"}],
        outputs={"answer": "two"}, purpose="Calculate two"))
    assert result["status"] == "completed", result
    output = result["result"]["output_nodes"]["answer"]
    assert output["value"] == "2"
    receipt = service.read(sid, "inspect_receipt", {"receipt_id": result["result"]["receipt_id"]})
    assert receipt["result"]["receipt"]["kind"] == "compiled_execution"
    submitted = service.mutate(sid, "submit", result["revision"], "submit", {"answer": {"answer": "2"}, "support_nodes": {"answer": output["id"]}})
    assert submitted["result"]["admitted"] and not submitted["result"]["mathematically_verified"]
    assert service.mutate(sid, "close", submitted["revision"], "close", {"outcome": "completed"})["status"] == "completed"
    sid = opened(service)
    failed = service.mutate(sid, "fail", 0, "run_experiment", {"source": "raise RuntimeError('expected')", "purpose": "Failure recording", "depends_on": []})
    assert failed["status"] == "failed"
    assert failed["result"]["receipt_id"]




def test_real_stdio_lifecycle(tmp_path):
    asyncio.run(asyncio.wait_for(_stdio_lifecycle(tmp_path), timeout=30))


async def _stdio_lifecycle(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(database_path=str(tmp_path / "stdio.sqlite"), project_id="p", run_id="r")))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "nima_semantica.math_mcp", "--config", str(config)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            listed = await client.list_tools()
            assert len(listed.tools) == 16
            discovery = await client.call_tool("nima_math_tools", {})
            assert "run_calculation_graph" in json.loads(discovery.content[0].text)["operations"]
            invalid = await client.call_tool("nima_math_open", {"request": {"unexpected": "SECRET"}})
            assert invalid.isError and "request.invalid_field" in invalid.content[0].text
            assert "SECRET" not in invalid.content[0].text
            result = await client.call_tool("nima_math_open", {"request": {"request_id": "create", "task": "Compute two", "required_paths": ["answer"]}})
            assert not result.isError
            payload = json.loads(result.content[0].text)
            sid = payload["session_id"]
            result = await client.call_tool("nima_math_record_step", {"request": dict(session_id=sid, request_id="a", expected_revision=0, kind="claim", statement="Two", value=2, depends_on=["task"])})
            native = MathSessionService(MathServiceConfig.model_validate_json(config.read_text()))
            from nima_semantica.math_mcp_contracts import invoke
            expected = invoke(native, "frontier", {"session_id": sid})
            result = await client.call_tool("nima_math_frontier", {"request": {"session_id": sid}})
            assert json.loads(result.content[0].text) == expected
            result = await client.call_tool("nima_math_submit", {"request": dict(session_id=sid, request_id="assembled",
                expected_revision=1, support_nodes={"answer":{"request_id":"a","output_path":"$"}})})
            assembled = json.loads(result.content[0].text)
            assert assembled["result"]["proposal"]["answer"] == {"answer":2}
            assert not assembled["result"]["admitted"]
            result = await client.call_tool("nima_math_close", {"request": dict(session_id=sid, request_id="end", expected_revision=1, outcome="partial")})
            assert not result.isError


@pytest.mark.integration
@pytest.mark.symbolic
def test_real_stdio_retrieval_calculation_and_submit(tmp_path):
    pytest.importorskip("semantica")
    from nima_semantica.storage import GraphStore
    from nima_semantica.corpus_registry import CorpusRegistry
    from nima_semantica.registry_contracts import CorpusDescriptor
    from test_source_pipeline import pipeline, context
    root = tmp_path / "corpus"
    store = GraphStore(root)
    try:
        CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Fixture", corpus_revision="1"))
        _, _, result = pipeline(store, ctx=context(project_id="p"))
        projection = result.data["projection"]["projection_id"]
        before = store.revision
    finally:
        store.close()
    asyncio.run(asyncio.wait_for(_stdio_calculate(tmp_path, root, projection), timeout=60))
    store = GraphStore(root)
    try:
        assert store.revision == before
    finally:
        store.close()


async def _stdio_calculate(tmp_path, root, projection):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config = tmp_path / "full-config.json"
    config.write_text(json.dumps(dict(database_path=str(tmp_path / "full.sqlite"), project_id="p", run_id="r",
        allow_retrieval=True, allow_execution=True, retrieval_projection_id=projection)))
    params = StdioServerParameters(command=sys.executable, args=["-m", "nima_semantica.math_mcp", "--config", str(config)],
        env={**os.environ, "NIMA_STORE_ROOT": str(root), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            async def call(operation, request):
                result = await client.call_tool("nima_math_" + operation, {"request": request})
                assert not result.isError, result
                return json.loads(result.content[0].text)
            session = await call("open", dict(request_id="create", task="Calculate one plus one", required_paths=["answer"]))
            sid = session["session_id"]
            retrieved = await call("retrieve_context", dict(session_id=sid, request_id="retrieve", expected_revision=0, query="Claim evidence", purpose="Inspect exact source context", mode="lexical"))
            assert retrieved["status"] == "completed", retrieved
            source = retrieved["result"]["source_nodes"][0]["id"]
            exact = await call("inspect", dict(session_id=sid, kind="node", identifier=source))
            assert exact["result"]["node"]["retrieval_provenance"]["evidence"]
            bad = await call("run_calculation_graph", dict(session_id=sid, request_id="bad", expected_revision=1,
                steps=[dict(id="sum", op="add", args=["missing", "missing"], meaning="Invalid dependency")], outputs={"answer": "sum"}, purpose="Test repair"))
            assert bad["status"] == "rejected"
            calculated = await call("run_calculation_graph", dict(session_id=sid, request_id="calculation", expected_revision=1,
                steps=[dict(id="one", op="integer", value=1, meaning="Unit"), dict(id="two", op="add", args=["one", "one"], meaning="Sum")],
                outputs={"answer": "two"}, purpose="Requested sum"))
            assert calculated["status"] == "completed", calculated
            output = calculated["result"]["output_nodes"]["answer"]
            receipt = await call("inspect", dict(session_id=sid, kind="receipt", identifier=calculated["result"]["receipt_id"]))
            assert receipt["result"]["receipt"]["kind"] == "compiled_execution"
            submitted = await call("submit", dict(session_id=sid, request_id="submit", expected_revision=2,
                answer={"answer": output["value"]}, support_nodes={"answer": output["id"]}))
            assert submitted["result"]["admitted"] and not submitted["result"]["mathematically_verified"]
            closed = await call("close", dict(session_id=sid, request_id="close", expected_revision=2, outcome="completed"))
            assert closed["result"]["outcome"] == "completed"
