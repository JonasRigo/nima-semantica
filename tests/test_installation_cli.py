import json
from pathlib import Path

from nima_semantica.cli import main, install_harness
from nima_semantica.installation import Installation, ModelProfile, EmbeddingProfile, write_json
from nima_semantica.workflow_installation import configure_flow, maintained_flows, model_inventory, validate_flow


def test_doctor_rejects_missing_resource_limits_even_when_worker_executes(tmp_path, monkeypatch, capsys):
    import subprocess
    import httpx
    import nima_semantica.cli as cli
    from nima_semantica.symbolic_transport import RemoteSymbolicWorker
    token = tmp_path / "token"
    token.write_text("test-only-token")
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(model="fixture"),
                          symbolic_url="http://127.0.0.1:7871", symbolic_token_file=str(token))
    capabilities = {"MemoryLimit": False, "SwapLimit": False, "PidsLimit": True}
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200))
    monkeypatch.setattr(RemoteSymbolicWorker, "run", lambda *a, **k: {"outcome": "executed", "stdout": "2"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=json.dumps(capabilities)))
    assert cli.doctor(config) == 1
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert checks["symbolic"] is True and checks["docker_resource_limits"] is False
    capabilities.update(MemoryLimit=True, SwapLimit=True)
    assert cli.doctor(config) == 0


def test_all_model_inputs_and_both_embedding_nodes_are_connected(tmp_path):
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(model="default"),
        embedding=EmbeddingProfile(model="emb", revision="pin", dimension=2),
        overrides={"review_research": ModelProfile(model="reviewer")})
    assignments = {}
    for name, path in maintained_flows().items():
        flow, roles = configure_flow(json.loads(path.read_text()), name, config, "papers", "project")
        assert validate_flow(flow)
        assignments.update(roles)
        for node in flow["data"]["nodes"]:
            template = node["data"]["node"]["template"]
            if "project_id" in template:
                assert template["project_id"]["value"] == "project"
        if name == "review_research":
            assert all(v["model"] == "reviewer" for v in roles.values())
    assert len(assignments) == sum(len(v) for v in model_inventory().values()) == 8


def test_setup_project_harness_merge_replay_and_ingestion(tmp_path, capsys):
    config_path = tmp_path / "installation.json"
    root = tmp_path / "data"
    prefix = ["--config", str(config_path)]
    assert main(prefix + ["setup", "--non-interactive", "--model", "fixture", "--data-root", str(root)]) == 0
    project = tmp_path / "work"; project.mkdir()
    (project / "opencode.json").write_text('{"theme":"dark"}')
    init = prefix + ["project", "init", "test", "--corpus", "papers", "--path", str(project), "--offline"]
    assert main(init) == 0
    before = {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert main(init) == 0
    assert before == {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert json.loads((project / "opencode.json").read_text())["theme"] == "dark"
    assert (project / ".claude/skills/nima-conduct-proof/assets/scipost-report.tex").is_file()
    notes = tmp_path / "notes.md"; notes.write_text("# Notes\n\nThe sum of two even integers is even.")
    assert main(prefix + ["ingest", "papers", str(notes), "--project", "test"]) == 0
    assert main(prefix + ["project", "status", "test", "--corpus", "papers"]) == 0
    assert main(prefix + ["ingest", "papers", str(tmp_path)]) == 2
    assert main(prefix + ["ingest", "papers", str(notes), "--project", "missing"]) == 2


def test_unknown_model_override_is_rejected_before_configuration_write(tmp_path):
    source = tmp_path / "source.json"
    config = Installation(data_root=str(tmp_path / "data"), llm=ModelProfile(model="fixture"), overrides={"retired": ModelProfile(model="wrong")})
    write_json(source, config.model_dump())
    destination = tmp_path / "new.json"
    assert main(["--config", str(destination), "setup", "--from-config", str(source)]) == 2
    assert not destination.exists()


def test_v11_deep_research_is_wired_with_discovery_and_provisional_reading(tmp_path):
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(model="fixture"))
    path = maintained_flows()["deep_research"]
    flow, roles = configure_flow(json.loads(path.read_text()), "deep_research", config, "papers", "project")
    assert flow["nima_tool_manifest"]["controller_version"] == "deep-research-passes-v11"
    nodes = {n["data"]["type"]: n for n in flow["data"]["nodes"]}
    assert "ConductDeepResearch" not in nodes
    template = nodes["ConductPassDeepResearch"]["data"]["node"]["template"]
    assert template["allow_fast_read"]["value"] and template["allow_model_calls"]["value"]
    assert not template["allow_source_ingestion"]["value"]
    assert json.loads(template["acquisition_policy_json"]["value"])["domains"] == ["arxiv.org", "export.arxiv.org"]
    policy = nodes["PaperDiscovery"]["data"]["node"]["template"]
    assert json.loads(policy["discovery_providers_json"]["value"]) == ["arxiv", "openalex", "crossref"]
    assert len(roles) == 1
    assert validate_flow(flow)


def test_cli_pdf_uses_configured_advanced_normalizer(tmp_path, monkeypatch):
    from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
    calls = []
    def normalize(client, content):
        assert client.url == "http://pdf-worker:8791"
        calls.append(content)
        return {"text": "Exact normalized PDF evidence about integers.", "diagnostics": [{"kind": "parser_manifest", "provenance": [{"page": 1}]}]}
    monkeypatch.setattr(PdfNormalizerClient, "normalize", normalize)
    path = tmp_path / "config.json"
    config = Installation(data_root=str(tmp_path / "data"), llm=ModelProfile(model="fixture"), pdf_url="http://pdf-worker:8791", pdf_token_file="private-token")
    write_json(path, config.model_dump())
    paper = tmp_path / "paper.pdf"; paper.write_bytes(b"%PDF-fixture")
    assert main(["--config", str(path), "ingest", "papers", str(paper)]) == 0
    assert calls == [paper.read_bytes()]
    config.pdf_url = ""
    write_json(path, config.model_dump())
    paper.write_bytes(b"%PDF-second-fixture")
    assert main(["--config", str(path), "ingest", "papers", str(paper)]) == 1
    assert len(calls) == 1


def test_remote_embeddings_use_a_named_langflow_credential(tmp_path):
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(model="fixture"),
        embedding=EmbeddingProfile(provider="compatible", model="embedding-fixture", revision="pin", dimension=2, credential="NIMA_TEST_EMBEDDING_KEY"))
    flow, _ = configure_flow(json.loads(maintained_flows()["prepare_and_index_sources"].read_text()), "prepare_and_index_sources", config, "papers", "p")
    nodes = [n for n in flow["data"]["nodes"] if n["data"]["type"] == "ConfiguredEmbeddings"]
    assert nodes
    for node in nodes:
        secret = node["data"]["node"]["template"]["api_key"]
        assert secret["value"] == "NIMA_TEST_EMBEDDING_KEY"
        assert secret["load_from_db"] is True
    assert validate_flow(flow)
