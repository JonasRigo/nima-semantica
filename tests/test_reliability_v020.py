"""Reliability acceptance on disposable stores and actual MCP transports."""
import asyncio
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from nima_semantica.corpus_snapshot import CorpusSnapshot
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import Record, canonical, ConflictError
from nima_semantica.proof_mcp import ProofConfig, ProofService
from nima_semantica.receipts import ExecutionReceipt


def receipt(**changes):
    return ExecutionReceipt(**({"receipt_id": "large", "operation_id": "attempt", "stage": "fixture",
        "corpus_id": "papers", "project_id": "p", "metadata": {"output": "exact" * 400000}} | changes))


def proof(tmp_path, store):
    service = ProofService(ProofConfig(database_path=str(tmp_path / "proof.sqlite3"), project_id="p", store_root=str(store.root)))
    opened = service.invoke("open", {"request_id": "create", "target": "Exact target"})
    return service, opened


def test_snapshot_keeps_live_writer_and_exact_paginated_scope(store):
    ids = sorted(store.put(Record(kind="Progress", corpus_id="papers", project_id="p", content={"n": n})) for n in range(7))
    foreign = store.put(Record(kind="Progress", corpus_id="papers", project_id="foreign", content={"n": 0}))
    with CorpusSnapshot(store.root, corpus_id="papers", project_id="p") as snapshot:
        before = snapshot.revision
        store.put(Record(kind="Progress", corpus_id="papers", project_id="p", content={"new": True}))
        assert snapshot.revision == before and snapshot.get(foreign) is None
        found, cursor = [], None
        while True:
            page = snapshot.enumerate_records(kind="Progress", after=cursor, limit=2, expected_revision=before)
            found.extend(page["record_ids"])
            cursor = page["next_cursor"]
            if not page["has_more"]: break
        assert found == ids
        with pytest.raises(ConflictError): snapshot.get(ids[0], corpus_id="foreign")
        with pytest.raises(Exception): snapshot.artifact(b"forbidden")
        snapshot.refresh()
        assert snapshot.revision != before


def test_exact_registry_lookup_decodes_only_matching_scoped_records(store, monkeypatch):
    shared = store.put(Record(kind="Lookup", corpus_id="papers", content={"resource_id": "selected"}))
    scoped = store.put(Record(kind="Lookup", corpus_id="papers", project_id="p", content={"resource_id": "selected"}))
    store.put(Record(kind="Lookup", corpus_id="papers", project_id="foreign", content={"resource_id": "selected"}))
    for n in range(50):
        store.put(Record(kind="Lookup", corpus_id="papers", content={"resource_id": str(n), "payload": "x" * 20000}))
    decoded = []
    original = store._decode
    def checked(key, payload):
        decoded.append(key)
        return original(key, payload)
    monkeypatch.setattr(store, "_decode", checked)
    found = store.records_matching("Lookup", "resource_id", "selected", corpus_id="papers", project_id="p")
    assert {key for key, _ in found} == {shared, scoped} and set(decoded) == {shared, scoped}
    with CorpusSnapshot(store.root, corpus_id="papers", project_id="p") as snapshot:
        assert {key for key, _ in snapshot.records_matching("Lookup", "resource_id", "selected", corpus_id="papers")} == {shared, scoped}
        with pytest.raises(ConflictError):
            snapshot.records_matching("Lookup", "resource_id", "selected", corpus_id="papers", project_id="foreign")
    with store._mutex:
        store._db.execute("UPDATE records SET payload=? WHERE id=?", (canonical(Record(kind="Lookup", corpus_id="papers", content={"resource_id": "selected", "tampered": True})).decode(), shared))
    with pytest.raises(Exception, match="integrity"):
        store.records_matching("Lookup", "resource_id", "selected", corpus_id="papers", project_id="p")


def test_large_authenticated_receipt_is_stored_once_and_attach_is_bounded(tmp_path, store):
    value = receipt()
    receipts = ExecutionReceiptService(store)
    rid = receipts.record(value)
    assert len(canonical(store.get(rid).content)) < 2048
    assert canonical(receipts.get(value.receipt_id, corpus_id="papers", project_id="p")) == canonical(value)
    service, opened = proof(tmp_path, store)
    args = {"session_id": opened["session_id"], "request_id": "attach", "expected_revision": 0,
            "receipt_id": value.receipt_id, "purpose": "Record authentic evidence"}
    result = service.invoke("attach_receipt", args)
    assert result["status"] == "complete"
    assert service.invoke("attach_receipt", args) == result
    with service._connect() as db:
        assert len(db.execute("SELECT state FROM proof_sessions").fetchone()[0]) < 4096
    node = service.read("inspect", {"session_id": opened["session_id"], "identifier": result["result"]["node_id"]})["node"]
    ref = node["receipt_reference"]
    assert canonical(receipts.resolve(ref, corpus_id="papers", project_id="p")) == canonical(value)
    for tampered in ({**ref, "canonical_payload_sha256": "0" * 64}, {**ref, "project_id": "foreign"}, {**ref, "receipt_id": "absent"}):
        with pytest.raises(ValueError): receipts.resolve(tampered, corpus_id="papers", project_id="p")
    full = service.read("inspect", {"session_id": opened["session_id"], "identifier": result["result"]["node_id"],
        "resolve_receipt": True, "full_payload": True})
    assert canonical(full["receipt_payload"]) == canonical(value)


def test_migration_interruption_resume_preserves_lifecycle_ids_and_replay(tmp_path, store):
    from nima_semantica.receipt_migration import migrate_proof_receipts
    receipts = ExecutionReceiptService(store); value = receipt(); receipts.record(value)
    service, opened = proof(tmp_path, store)
    with service._connect() as db:
        row, state = service._load(db, opened["session_id"])
        state["nodes"]["retained"] = {"kind": "check", "statement": "Authentic", "depends_on": ["target"], "receipt": value.model_dump(mode="json")}
        state["closure"] = {"summary": "Retain accepted campaign", "outcome": "completed"}
        db.execute("UPDATE proof_sessions SET state=?,lifecycle='completed' WHERE id=?", (canonical(state).decode(), row["id"]))
        calls = list(db.execute("SELECT * FROM proof_calls"))
        original = db.execute("SELECT state FROM proof_sessions").fetchone()[0]
    def crash(node_id): raise RuntimeError("simulated interruption")
    directory = tmp_path / "migration"
    with pytest.raises(RuntimeError): migrate_proof_receipts(service, opened["session_id"], 0, directory, checkpoint=crash)
    with service._connect() as db: assert db.execute("SELECT state FROM proof_sessions").fetchone()[0] == original
    result = migrate_proof_receipts(service, opened["session_id"], 0, directory)
    assert result["status"] == "complete" and result["after_bytes"] < 4096
    with service._connect() as db:
        row, state = service._load(db, opened["session_id"])
        assert row["revision"] == 0 and row["lifecycle"] == "completed" and row["active"] is None
        assert list(db.execute("SELECT * FROM proof_calls")) == calls
        assert state["nodes"]["retained"]["depends_on"] == ["target"]
        assert state["closure"]["outcome"] == "completed"


def test_export_pagination_and_exact_large_statement(tmp_path, store):
    service, opened = proof(tmp_path, store)
    session = opened
    for n in range(30):
        session = service.invoke("record_node", {"session_id": opened["session_id"], "request_id": str(n),
            "expected_revision": session["revision"], "kind": "step", "statement": "s" * 20000})
    first = service.invoke("export", {"session_id": opened["session_id"], "limit": 3})
    assert first["has_more"] and first["partial_export"] and len(canonical(first)) < 65536
    nodes, receipts, cursor = {}, [], None
    while True:
        page = service.invoke("export", {"session_id": opened["session_id"], "limit": 3, "cursor": cursor})
        nodes.update(page["graph"]["nodes"]); receipts.extend(page["receipts"])
        cursor = page["next_cursor"]
        if not page["has_more"]: break
    assert len(nodes) == 31 and len(receipts) == 31
    identifier = next(key for key in nodes if key != "target")
    assert "payload_reference" in nodes[identifier]
    assert service.invoke("inspect", {"session_id": opened["session_id"], "identifier": identifier,
        "full_payload": True})["node"]["statement"] == "s" * 20000


def test_reconciliation_all_file_recovery_and_input_guards(tmp_path):
    from nima_semantica.config_reconciliation import reconciliation_preview, apply_reconciliation, recover_reconciliation
    config = tmp_path / "installation.json"; config.write_text("{}")
    binding = {"corpus_id": "papers", "project_id": "v2", "store_root": str(tmp_path / "corpus"), "projection_id": "pin"}
    for kind, key in (("proof", "projection_id"), ("math", "retrieval_projection_id")):
        path = tmp_path / (kind + ".json")
        path.write_text(json.dumps({"project_id": "v2", "corpus_id": "papers", key: "pin"})); path.chmod(0o600)
        binding[kind + "_config"] = str(path)
    root = tmp_path / "harness"; (root / ".codex").mkdir(parents=True)
    target = root / ".codex/config.toml"
    original = '[mcp_servers.other]\nurl="https://example.invalid"\n[mcp_servers.nima-proof]\ncommand="legacy"\nargs=[]\nstartup_timeout_sec=99\nenabled=false\n'
    target.write_text(original)
    preview = reconciliation_preview(root, config, binding, ["codex", "claude", "opencode"])
    def crash(path): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): apply_reconciliation(preview, tmp_path / "recovery", checkpoint=crash)
    manifest = tmp_path / "recovery/recovery.json"
    assert recover_reconciliation(manifest, "restore")["status"] == "restored"
    assert target.read_text() == original and not (root / ".nima/project.json").exists()
    assert recover_reconciliation(manifest, "complete")["status"] == "complete"
    import tomllib
    settings = tomllib.loads(target.read_text())["mcp_servers"]
    assert settings["other"]["url"] == "https://example.invalid"
    assert settings["nima-proof"]["enabled"] is False
    assert target.stat().st_uid == __import__("os").getuid()
    assert settings["nima-proof"]["startup_timeout_sec"] == 99
    with pytest.raises(ValueError): apply_reconciliation(preview, tmp_path / "stale")


def test_catalogue_default_bounds_and_complete_selected_contracts():
    from nima_semantica.tool_guide import ToolGuideRequest, tool_guide
    from nima_semantica.tool_usage import private_catalog
    from nima_semantica.proof_mcp import TOOLS
    public = tool_guide(ToolGuideRequest())
    assert len(canonical(public)) <= 32768
    assert all("input_schema" not in item for item in public.data["tools"])
    assert tool_guide(ToolGuideRequest(tool="draft_lean")).data["tools"][0]["input_schema"]
    private = private_catalog(TOOLS)
    assert len(canonical(private)) <= 16384 and set(private["operations"]) == set(TOOLS)
    assert private_catalog(TOOLS, "open")["operations"]["open"]["input_schema"]


def test_math_migration_exact_restore_literal_payload_and_projection_rebind(tmp_path):
    from nima_semantica.math_session_service import MathServiceConfig, MathSessionService
    from nima_semantica.receipt_migration import migrate_math_payloads
    from nima_semantica.private_payloads import compact, expand
    config = MathServiceConfig(database_path=str(tmp_path / "math.sqlite3"), project_id="p", run_id="r",
        retrieval_projection_id="old", allow_retrieval=True)
    service = MathSessionService(config)
    sid = service.open("Retain exact mathematics", ["answer"])["session_id"]
    with service._connect() as db:
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        state = json.loads(row["state_json"])
        state["nodes"]["task"]["value"] = "exact" * 30000
        db.execute("UPDATE sessions SET state_json=? WHERE session_id=?", (canonical(state).decode(), sid))
        literal = {"source": {"private_payload_reference": {"schema_version": "caller text"}}}
        assert expand(db, service._scope(), compact(db, service._scope(), literal)) == literal
    original = service.read(sid, "export", {"full_payload": True})
    def crash(_): raise RuntimeError("interrupted representation migration")
    with pytest.raises(RuntimeError): migrate_math_payloads(service, sid, 0, tmp_path / "migration", checkpoint=crash)
    assert service.read(sid, "export", {"full_payload": True}) == original
    result = migrate_math_payloads(service, sid, 0, tmp_path / "migration")
    assert result["after_bytes"] < result["before_bytes"] / 10
    assert migrate_math_payloads(service, sid, 0, tmp_path / "migration") == result
    assert service.read(sid, "export", {"full_payload": True}) == original
    rebound = MathSessionService(config.model_copy(update={"retrieval_projection_id": "new"}))
    assert rebound.read(sid, "export", {"full_payload": True}) == original
    with service._connect() as db:
        db.execute("UPDATE private_payloads SET payload=?", (b"tampered",))
    with pytest.raises(ValueError, match="digest/size"): service.read(sid)


def test_diagnostics_specific_ownership_access_and_redaction(tmp_path, monkeypatch):
    from nima_semantica.mcp_diagnostics import owner_diagnostic, redacted_error, scoped_mcp_diagnostics
    from nima_semantica.private_service import database_owner
    path = tmp_path / "owned.sqlite3"
    with database_owner(path):
        report = owner_diagnostic(path)
        assert report["state"] == "occupied" and report["owner"]["nima_version"] == "0.2.0"
    assert owner_diagnostic(path)["state"] == "unoccupied"
    monkeypatch.setenv("TEST_API_KEY", "a-very-secret-key")
    error = ExceptionGroup("startup", [ValueError("Bearer other-secret https://user:password@example.invalid token=another-secret a-very-secret-key")])
    rendered = canonical(redacted_error(error))
    assert b"other-secret" not in rendered and b"another-secret" not in rendered and b"a-very-secret-key" not in rendered
    binding = {"corpus_id": "papers", "project_id": "p", "store_root": str(tmp_path),
        "proof_config": str(tmp_path / "proof.json"), "math_config": str(tmp_path / "math.json")}
    original = Path.read_text
    def denied(self, *args, **kwargs):
        if self.name in {"proof.json", "math.json"}: raise PermissionError("private service account")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", denied)
    report = scoped_mcp_diagnostics(binding, root=tmp_path, initialize=False)
    assert all(report["servers"][kind]["file_access"]["state"] == "permission_denied" for kind in ("proof", "math"))
    assert not report["ready"]


def test_authentication_discovery_rejects_successful_spa_fallback():
    import httpx
    from nima_semantica.mcp_diagnostics import authentication_discovery
    assert authentication_discovery(httpx.Response(200, text="<html>Application</html>")) == "unsupported"
    assert authentication_discovery(httpx.Response(200, json={"resource": "https://example.test/mcp"})) == "unsupported"
    assert authentication_discovery(httpx.Response(200, json={"resource": "https://example.test/mcp", "authorization_servers": ["https://example.test"]})) == "available"
    assert authentication_discovery(httpx.Response(404)) == "unsupported"


def test_managed_owner_units_preserve_private_configuration_and_policy(tmp_path):
    from nima_semantica.managed_private import configure_private_services, unit_argument
    from nima_semantica.installation import write_json
    installation = tmp_path / "installation.json"
    write_json(installation, {"data_root": str(tmp_path), "llm": {"model": "fixture"}, "symbolic_url": "http://127.0.0.1:7872"})
    binding = {"corpus_id": "papers", "project_id": "p", "store_root": str(tmp_path / "corpus"), "mcp_url": "http://127.0.0.1:7860/mcp"}
    for kind in ("proof", "math"):
        path = tmp_path / (kind + ".json")
        write_json(path, {"database_path": str(tmp_path / (kind + ".sqlite3")), "project_id": "p", **({"run_id": "r"} if kind == "math" else {})})
        binding[kind + "_config"] = str(path)
    prepared = configure_private_services(binding, installation, tmp_path / "owners")
    result = json.loads(Path(prepared["binding_file"]).read_text())
    for kind in ("proof", "math"):
        endpoint = result["private_services"][kind]
        assert Path(endpoint["token_file"]).stat().st_mode & 0o777 == 0o600
        unit = (tmp_path / "owners" / endpoint["service_unit"]).read_text()
        assert "NIMA_CONFIG=" in unit and "NIMA_RESEARCH_MCP_URL=" in unit and "Restart=on-failure" in unit
    with pytest.raises(ValueError): unit_argument("injected\nExecStart=other")


def test_vacuum_refuses_live_corpus_writer_and_retains_history(store, tmp_path):
    from nima_semantica.receipt_migration import reclaim_sqlite
    identifier = store.put(Record(kind="Progress", corpus_id="papers", project_id="p", content={"exact": "saved"}))
    with pytest.raises(ValueError, match="owner must be paused"):
        reclaim_sqlite(store.root / "graph.sqlite3", tmp_path / "refused.sqlite3")
    assert not (tmp_path / "refused.sqlite3").exists()
    root = store.root
    store.close()
    result = reclaim_sqlite(root / "graph.sqlite3", tmp_path / "backup.sqlite3")
    assert result["history_preserved"]
    with CorpusSnapshot(root, corpus_id="papers", project_id="p") as snapshot:
        assert snapshot.get(identifier).content == {"exact": "saved"}


@pytest.mark.parametrize("kind", ["proof", "math"])
def test_killed_private_owner_recovers_once_without_reexecution(tmp_path, kind):
    import os
    import select
    import subprocess
    import sys
    from nima_semantica.private_service import database_owner, OwnerOccupied
    from nima_semantica.math_mcp import ConfiguredMathService
    from nima_semantica.math_mcp_contracts import invoke
    from nima_semantica.math_session_service import MathServiceConfig
    config = (ProofConfig(database_path=str(tmp_path / "private.sqlite3"), project_id="p", allow_execution=True)
        if kind == "proof" else MathServiceConfig(database_path=str(tmp_path / "private.sqlite3"), project_id="p", run_id="r", allow_execution=True))
    service = ProofService(config) if kind == "proof" else ConfiguredMathService(config)
    opened = service.invoke("open", {"request_id": "create", "target": "Target"}) if kind == "proof" else invoke(service, "open", {"request_id": "create", "task": "Task", "required_paths": ["answer"]})
    config_file = tmp_path / "private.json"; config_file.write_text(config.model_dump_json()); config_file.chmod(0o600)
    arguments = {"session_id": opened["session_id"], "request_id": "killed", "expected_revision": 0, "source": "print(1)", "purpose": "Explore"}
    if kind == "math": arguments["depends_on"] = []
    script = '''import sys,json,time
from pathlib import Path
from nima_semantica.private_service import database_owner
from nima_semantica.proof_mcp import ProofConfig,ProofService
from nima_semantica.math_mcp import ConfiguredMathService
from nima_semantica.math_session_service import MathServiceConfig
from nima_semantica.math_mcp_contracts import invoke
class Worker:
    def run(self,*args):
        print("reserved",flush=True)
        time.sleep(60)
kind=sys.argv[1]
config=(ProofConfig if kind=="proof" else MathServiceConfig).model_validate_json(Path(sys.argv[2]).read_text())
with database_owner(config.database_path):
    service=(ProofService if kind=="proof" else ConfiguredMathService)(config,worker=Worker())
    service.recover_interrupted()
    args=json.loads(sys.argv[3])
    service.invoke("run_experiment",args) if kind=="proof" else invoke(service,"run_experiment",args)
'''
    child = subprocess.Popen([sys.executable, "-c", script, kind, str(config_file), json.dumps(arguments)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(Path(__import__("nima_semantica").__file__).parent.parent)})
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == b"reserved"
        with pytest.raises(OwnerOccupied):
            with database_owner(config.database_path): pass
        child.kill(); child.wait(timeout=5)
        with database_owner(config.database_path):
            service.recover_interrupted()
            response = service.invoke("run_experiment", arguments) if kind == "proof" else invoke(service, "run_experiment", arguments)
            assert response["status"] == "interrupted"
            service.recover_interrupted()
            assert (service.invoke("run_experiment", arguments) if kind == "proof" else invoke(service, "run_experiment", arguments)) == response
    finally:
        if child.poll() is None: child.kill()
        child.communicate(timeout=5)


@pytest.mark.parametrize("kind", ["proof", "math"])
def test_two_real_mcp_clients_reconnect_during_active_mutation(tmp_path, kind):
    pytest.importorskip("mcp")
    import socket
    import uvicorn
    from nima_semantica.private_service import http_app, database_owner
    from nima_semantica.math_mcp import create_server as math_server, ConfiguredMathService
    from nima_semantica.math_session_service import MathServiceConfig
    from nima_semantica.proof_mcp import create_server as proof_server
    started, finish = threading.Event(), threading.Event()
    class Worker:
        calls = 0
        def run(self, source, timeout):
            self.calls += 1; started.set(); assert finish.wait(10)
            return {"outcome": "executed", "exit_code": 0, "stdout": "2", "stderr": ""}
    worker = Worker()
    path = tmp_path / "private.sqlite3"
    service = (ProofService(ProofConfig(database_path=str(path), project_id="p", allow_execution=True), worker=worker)
        if kind == "proof" else ConfiguredMathService(MathServiceConfig(database_path=str(path), project_id="p", run_id="r", allow_execution=True), worker=worker))
    server = (proof_server if kind == "proof" else math_server)(service)
    token = "t" * 48
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    running = uvicorn.Server(uvicorn.Config(http_app(server, service, token), log_level="error", lifespan="on"))
    thread = threading.Thread(target=lambda: running.run(sockets=[sock]), daemon=True)
    with database_owner(path):
        service.recover_interrupted(); thread.start()
        deadline = time.monotonic() + 5
        while not running.started and time.monotonic() < deadline: time.sleep(.02)
        assert running.started
        async def exercise():
            import httpx
            from contextlib import AsyncExitStack
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            async def connect(stack):
                http = await stack.enter_async_context(httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False))
                read, write, _ = await stack.enter_async_context(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http))
                client = await stack.enter_async_context(ClientSession(read, write))
                initialization = await client.initialize()
                assert initialization.serverInfo.version == "0.2.0"
                assert (await client.list_tools()).tools
                return client
            async def call(client, op, request):
                response = await client.call_tool("nima_" + kind + "_" + op, {"request": request})
                assert not response.isError, response
                return json.loads(response.content[0].text)
            async with AsyncExitStack() as first:
                client = await connect(first)
                creation = {"request_id": "create", **({"target": "Target"} if kind == "proof" else {"task": "Task", "required_paths": ["answer"]})}
                opened = await call(client, "open", creation)
                sid = opened["session_id"]
                args = {"session_id": sid, "request_id": "running", "expected_revision": 0, "source": "print(2)", "purpose": "Explore"}
                if kind == "math": args["depends_on"] = []
                work = asyncio.create_task(call(client, "run_experiment", args))
                assert await asyncio.to_thread(started.wait, 5)
                async with AsyncExitStack() as second:
                    other = await connect(second)
                    status = await call(other, "status", {"session_id": sid})
                    assert status["revision"] == 0
                    conflict = await call(other, "run_experiment", {**args, "request_id": "conflict"})
                    assert conflict["status"] == "rejected"
                    await call(other, "open", {**creation, "request_id": "independent"})
                async with AsyncExitStack() as third:
                    reconnected = await connect(third)
                    status = await call(reconnected, "status", {"session_id": sid})
                    assert status["revision"] == 0
                    finish.set(); result = await work
                    assert result["status"] in {"complete", "completed"}
                    assert await call(reconnected, "run_experiment", args) == result
                # A fresh stdio harness connects through the existing owner.
                import sys
                from nima_semantica.mcp_diagnostics import initialize_server
                token_path = tmp_path / "proxy.token"; token_path.write_text(token); token_path.chmod(0o600)
                report = await initialize_server({"command": sys.executable,
                    "args": ["-m", "nima_semantica.private_proxy", "--url", f"http://127.0.0.1:{port}/mcp", "--token-file", str(token_path)],
                    "env": {"PYTHONPATH": str(Path(__import__("nima_semantica").__file__).parent.parent)}})
                assert report["transport"] == "verified" and report["server_version"] == "0.2.0"
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client
                async with AsyncExitStack() as proxy_stack:
                    read, write = await proxy_stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,
                        args=["-m", "nima_semantica.private_proxy", "--url", f"http://127.0.0.1:{port}/mcp", "--token-file", str(token_path)],
                        env={**__import__("os").environ, "PYTHONPATH": str(Path(__import__("nima_semantica").__file__).parent.parent)})))
                    forwarded = await proxy_stack.enter_async_context(ClientSession(read, write))
                    await forwarded.initialize()
                    assert (await call(forwarded, "status", {"session_id": sid}))["revision"] == result["revision"]
                assert worker.calls == 1
        try: asyncio.run(asyncio.wait_for(exercise(), 20))
        finally:
            finish.set(); running.should_exit = True; thread.join(5); sock.close()
