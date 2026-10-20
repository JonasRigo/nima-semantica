"""Readiness requires the saved runtime, including the independent image model."""
import json
from types import SimpleNamespace

import httpx
import pytest

from nima_semantica import workflow_installation, workflow_readiness as readiness
from nima_semantica.credential_setup import credential_session
from nima_semantica.installation import EmbeddingProfile, Installation, ModelProfile, PdfRecognitionSettings
from nima_semantica.pdf import workflow as pdf_workflow


def configured(tmp_path):
    return Installation(data_root=str(tmp_path), llm=ModelProfile(model="main", credential="MAIN_KEY"),
        pdf_recognition=PdfRecognitionSettings(model=ModelProfile(model="vision", credential="VISION_KEY")))


def test_formula_probe_does_not_require_optional_pdf_dependencies(monkeypatch):
    import base64
    import builtins
    import hashlib
    original = builtins.__import__
    def without_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("Pillow is not installed")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_pillow)
    probe = readiness.formula_probe()
    data = base64.b64decode(probe["png_base64"])
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert probe["crop_hash"] == hashlib.sha256(data).hexdigest()
    assert readiness.formula_probe()["id"] != probe["id"]


def backend(monkeypatch, *, reject_pdf=False, missing_metadata=False):
    flows = {}
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.startswith("/api/v1/variables/") and request.method != "GET":
            saved = json.loads(request.content)
            assert saved.get("id") == "VISION_KEY"
            assert saved["value"] == "fixture-key-file-value"
            return httpx.Response(200, json={"name": saved["name"], "type": "Credential", "has_value": True})
        if path == "/api/v1/variables/":
            return httpx.Response(200, json=[{"name": name, "id": name, "type": "Credential", **({} if missing_metadata else {"has_value": True})}
                for name in ("MAIN_KEY", "VISION_KEY")])
        if request.method == "POST" and path == "/api/v1/flows/":
            identifier = "flow-" + str(len(flows))
            flows[identifier] = json.loads(request.content)
            return httpx.Response(201, json={"id": identifier})
        identifier = path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json=flows[identifier])
        if request.method == "PATCH":
            flows[identifier].update(json.loads(request.content))
            return httpx.Response(200, json={"id": identifier})
        assert path.startswith("/api/v1/run/")
        payload = json.loads(request.content)
        assert payload["session_id"].startswith("nima-setup-")
        value = json.loads(payload["input_value"])
        if "crop_hash" in value:
            if reject_pdf:
                return httpx.Response(500, json={"error": "401: Missing Authentication header; fixture-provider-secret"})
            result = {"id": value["id"], "crop_hash": value["crop_hash"], "status": "transcribed", "latex": "x=1"}
        else:
            result = {"ready": True}
        return httpx.Response(200, json={"outputs": [{"outputs": [{"component_id": "ChatOutput-ocr",
            "results": {"message": {"text": json.dumps(result)}}}]}]})
    def client(config):
        return httpx.Client(base_url="http://langflow.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(workflow_installation, "api", client)
    monkeypatch.setattr(pdf_workflow, "api", client)
    monkeypatch.setattr(readiness, "formula_probe", lambda: {"id": "formula-check", "crop_hash": "0" * 64, "png_base64": "fixture"})
    return flows, calls, client


def test_unknown_metadata_is_verified_through_saved_text_and_formula_flows(tmp_path, monkeypatch):
    config = configured(tmp_path)
    flows, calls, _ = backend(monkeypatch, missing_metadata=True)
    with credential_session(interactive=False):
        binding = pdf_workflow.install_recognition(config)
        assert binding["readiness"]["ready"] is False
        result = readiness.verify_installation(config)
    assert result["ready"] is True
    assert result["checks"]["PDF recognition"]["credential"] == "VISION_KEY"
    assert sum(path.startswith("/api/v1/run/") for _, path in calls) == 2
    assert all(flow["access_type"] == "PRIVATE" and not flow["mcp_enabled"] for flow in flows.values())
    saved = json.loads((tmp_path / "pdf-service/recognition.json").read_text())
    assert saved["readiness"]["ready"] is True


def test_healthy_service_and_saved_canvas_do_not_hide_formula_auth_failure(tmp_path, monkeypatch, capsys):
    config = configured(tmp_path)
    _, _, client = backend(monkeypatch, reject_pdf=True)
    with client(config) as api:
        assert api.get("/health").is_success
    with credential_session(interactive=False):
        pdf_workflow.install_recognition(config)
        with pytest.raises(readiness.WorkflowReadinessError) as caught:
            readiness.verify_installation(config)
    detail = caught.value.diagnostic
    assert detail["code"] == "workflow.authentication_failed"
    assert (detail["role"], detail["model"], detail["credential"]) == ("PDF recognition", "vision", "VISION_KEY")
    report = json.loads((tmp_path / "installation/workflow-readiness.json").read_text())
    assert report["ready"] is False and report["checks"]["tool model"]["ready"] is True
    assert json.loads((tmp_path / "pdf-service/recognition.json").read_text())["readiness"]["ready"] is False
    assert "fixture-provider-secret" not in str(caught.value) + json.dumps(report) + capsys.readouterr().err


@pytest.mark.parametrize("field,value", [("value", "WRONG_CREDENTIAL"), ("load_from_db", False)])
def test_mismatched_saved_credential_binding_is_rejected_before_provider_call(tmp_path, field, value):
    config = configured(tmp_path)
    data = readiness.check_flow(config, config.llm)["data"]
    node = next(node for node in data["nodes"] if node["data"]["type"] == "ConfiguredModel")
    node["data"]["node"]["template"]["api_key"][field] = value
    with pytest.raises(readiness.WorkflowReadinessError, match="binding mismatch"):
        readiness.check_bindings(data, "tool model", config.llm)


def test_setup_exits_incomplete_when_saved_formula_flow_cannot_authenticate(tmp_path, monkeypatch, capsys):
    from nima_semantica.cli import main
    config = configured(tmp_path / "data")
    path = tmp_path / "installation.json"
    path.write_text(config.model_dump_json())
    backend(monkeypatch, reject_pdf=True)
    assert main(["--config", str(path), "setup", "--non-interactive", "--no-modify-path", "--verify-workflows"]) == 2
    output = capsys.readouterr()
    assert "vision" in output.err and "VISION_KEY" in output.err and "incomplete" in output.err
    assert "fixture-provider-secret" not in output.err + output.out
    assert not json.loads((tmp_path / "data/installation/workflow-readiness.json").read_text())["ready"]


def test_setup_file_source_explicit_replacement_runs_saved_pdf_probe(tmp_path, monkeypatch, capsys):
    from nima_semantica.cli import main
    config = configured(tmp_path / "data")
    path = tmp_path / "installation.json"
    original = config.model_dump_json()
    path.write_text(original)
    key = tmp_path / "vision.key"
    key.write_text("fixture-key-file-value\n")
    _, calls, _ = backend(monkeypatch)
    assert main(["--config", str(path), "setup", "--non-interactive", "--no-modify-path", "--pdf",
        "--credential-file", f"VISION_KEY={key}", "--replace-credential", "VISION_KEY"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["setup_complete"] is True
    assert "fixture-key-file-value" not in output.out + output.err + path.read_text()
    assert sum(method == "PATCH" and route.startswith("/api/v1/variables/") for method, route in calls) == 1
    assert any(backup.read_text() == original for backup in tmp_path.glob("installation.json.*.bak"))


def test_saved_embedding_runtime_is_required_as_well_as_text(tmp_path, monkeypatch):
    config = configured(tmp_path)
    config.pdf_recognition = None
    config.embedding = EmbeddingProfile(provider="compatible", model="embedding", revision="pinned", dimension=2,
        credential="MAIN_KEY", base_url="http://embeddings.test/v1")
    flows, calls, _ = backend(monkeypatch)
    with credential_session(interactive=False):
        report = readiness.verify_installation(config)
    assert report["checks"]["embeddings"]["ready"] is True
    assert sum(route.startswith("/api/v1/run/") for _, route in calls) == 2
    assert any(node["data"]["type"] == "ConfiguredEmbeddings" for flow in flows.values() for node in flow["data"]["nodes"])


def test_doctor_reports_services_and_workflows_separately(tmp_path, monkeypatch, capsys):
    import subprocess
    from nima_semantica import cli
    config = configured(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/fixture")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout=json.dumps({"MemoryLimit": True, "SwapLimit": True, "PidsLimit": True})))
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200))
    def unavailable(config):
        raise readiness.WorkflowReadinessError("PDF recognition", config.pdf_recognition.model, "authentication_failed")
    monkeypatch.setattr(readiness, "verify_installation", unavailable)
    args = SimpleNamespace(mcp=False, workflows=True)
    assert cli.doctor(config, args) == 1
    report = json.loads(capsys.readouterr().out)
    assert all(report["service_health"].values())
    assert report["workflow_readiness"]["ready"] is False and report["setup_complete"] is False


def test_online_project_reports_shared_credential_dependencies(tmp_path, monkeypatch, capsys):
    from nima_semantica.cli import main
    config = configured(tmp_path / "data")
    path = tmp_path / "installation.json"
    path.write_text(config.model_dump_json())
    backend(monkeypatch)
    monkeypatch.setattr(workflow_installation, "publish_flows", lambda *args: {"mcp_url": "http://langflow.test/project-mcp"})
    with credential_session(interactive=False):
        pdf_workflow.install_recognition(config)
    assert main(["--config", str(path), "project", "init", "research", "--corpus", "papers",
        "--path", str(tmp_path / "workspace"), "--harness", "codex", "--non-interactive"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["credentials_verified"] is True and report["toolbox_verified"] is False
    assert {row["credential"] for row in report["credential_dependencies"]} == {"MAIN_KEY", "VISION_KEY"}


def test_previously_ready_report_is_invalidated_before_credential_resolution(tmp_path, monkeypatch):
    from nima_semantica.cli import main
    from nima_semantica.credential_setup import CredentialError
    config = configured(tmp_path / "data")
    path = tmp_path / "installation.json"
    path.write_text(config.model_dump_json())
    backend(monkeypatch)
    with credential_session(interactive=False):
        pdf_workflow.install_recognition(config)
        readiness.verify_installation(config)
    def unavailable(*args):
        raise CredentialError("Credential VISION_KEY is empty")
    monkeypatch.setattr(pdf_workflow, "ensure_credentials", unavailable)
    assert main(["--config", str(path), "setup", "--non-interactive", "--no-modify-path", "--pdf"]) == 2
    assert json.loads((tmp_path / "data/installation/workflow-readiness.json").read_text())["ready"] is False
    assert json.loads((tmp_path / "data/pdf-service/recognition.json").read_text())["readiness"]["ready"] is False


def test_pdf_auth_diagnostic_survives_frontend_stage_translation():
    from nima_semantica.orchestration.langflow.http_errors import raise_stage_error
    from nima_semantica.ingestion_diagnostics import preparation_diagnostic
    response = httpx.Response(502, request=httpx.Request("POST", "http://pdf.test/normalize"), json={"detail": {
        "code": "workflow.authentication_failed", "role": "PDF recognition", "model": "vision", "credential": "VISION_KEY",
        "message": "arbitrary provider body fixture-secret"}})
    with pytest.raises(readiness.WorkflowReadinessError) as caught:
        raise_stage_error(response, "PDF normalization", ("fixture-secret",))
    detail = preparation_diagnostic(caught.value)
    assert detail["credential"] == "VISION_KEY" and detail["model"] == "vision"
    assert "fixture-secret" not in json.dumps(detail)


def test_full_cli_ingestion_retains_model_and_credential_diagnostic(tmp_path, monkeypatch, capsys):
    from nima_semantica.cli import main
    from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
    config = configured(tmp_path / "data")
    config.pdf_url = "http://pdf.test"
    config.pdf_token_file = str(tmp_path / "token")
    path = tmp_path / "installation.json"
    path.write_text(config.model_dump_json())
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-fixture")
    def fail(client, data):
        raise readiness.WorkflowReadinessError("PDF recognition", config.pdf_recognition.model, "authentication_failed")
    monkeypatch.setattr(PdfNormalizerClient, "profile", lambda client: {"profile_hash": "1" * 64})
    monkeypatch.setattr(PdfNormalizerClient, "normalize", fail)
    assert main(["--config", str(path), "ingest", "papers", str(source), "--mode", "full", "--retry"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["failed_stage"] == "source_preparation"
    assert result["diagnostics"][0]["code"] == "workflow.authentication_failed"
    assert result["diagnostics"][0]["credential"] == "VISION_KEY"
