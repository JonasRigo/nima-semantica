"""Platform routing and deployment contracts, independent of the host OS."""
import json
from pathlib import Path
import subprocess

import pytest

from nima_semantica.installation import Installation, ModelProfile, EmbeddingProfile
from nima_semantica import setup_macos
from nima_semantica.workflow_installation import configure_flow, maintained_flows


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:11434/v1", "http://host.docker.internal:11434/v1"),
    ("http://[::1]:11434", "http://host.docker.internal:11434"),
    ("https://localhost/api", "https://host.docker.internal/api"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
])
def test_container_urls(url, expected):
    assert setup_macos.container_url(url) == expected


def configured(tmp_path):
    return Installation(data_root=str(tmp_path / "research data ü"),
        llm=ModelProfile(model="fixture", base_url="http://localhost:11434/v1",
                         container_url="http://host.docker.internal:11434/v1"),
        embedding=EmbeddingProfile(model="fixture", revision="pin", dimension=2,
                                   container_url="http://host.docker.internal:11434"),
        symbolic_url="http://127.0.0.1:7871", symbolic_container_url="http://symbolic:7871",
        symbolic_token_file=str(tmp_path / "symbolic/token"),
        lean_url="http://127.0.0.1:7873", lean_container_url="http://lean:7873",
        lean_token_file=str(tmp_path / "lean/token"),
        pdf_url="http://127.0.0.1:8791", pdf_container_url="http://pdf:8791",
        pdf_token_file=str(tmp_path / "pdf/token"))


def test_compose_separates_controller_and_untrusted_workers(tmp_path):
    config = configured(tmp_path)
    services = setup_macos.compose_document(config, pdf=True, lean=True)["services"]
    assert set(services) == {"symbolic", "pdf", "lean"}
    for name, service in services.items():
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
        assert "network_mode" not in service and not service.get("privileged")
        socket = any(v["source"] == "/var/run/docker.sock" for v in service["volumes"])
        assert socket is (name == "symbolic")
        assert all(v["bind"]["create_host_path"] is False for v in service["volumes"])
    assert services["lean"]["volumes"] == [setup_macos._bind(config.lean_token_file, readonly=True)]
    for name in ("lean", "pdf"):
        assert services[name]["cap_drop"] == ["ALL"] and "cap_add" not in services[name]
        assert any(option.startswith("seccomp=") for option in services[name]["security_opt"])
        assert all(v["source"] not in {config.data_root, str(Path(config.data_root) / "corpus")} for v in services[name]["volumes"])


def test_native_langflow_agent_uses_host_paths_and_loopback(tmp_path):
    config = configured(tmp_path)
    agent = setup_macos.langflow_agent(config, tmp_path / "env/bin/python")
    assert agent["ProgramArguments"][-4:] == ["--host", "127.0.0.1", "--port", "7860"]
    env = agent["EnvironmentVariables"]
    assert env["NIMA_LEAN_WORKER_URL"] == config.lean_url
    assert env["LANGFLOW_SSRF_ALLOWED_HOSTS"] == "127.0.0.1,localhost"
    assert env["NIMA_STORE_ROOT"] == str(Path(config.data_root) / "corpus")
    assert "TOKEN" not in env and "NIMA_MODEL_API_KEY" not in env


def test_launchagent_rejects_operator_changes_and_preserves_them(tmp_path, monkeypatch):
    import hashlib
    import plistlib
    import httpx
    from unittest.mock import MagicMock
    monkeypatch.setattr(setup_macos.socket, "socket", MagicMock())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if "print" in argv else 0)
    monkeypatch.setattr(subprocess, "run", run)
    config = configured(tmp_path)
    progress = {}
    setup_macos.start_langflow(config, tmp_path / "env/bin/python", progress, tmp_path / "progress.json")
    path, = (tmp_path / "Library/LaunchAgents").glob("*.plist")
    agent = plistlib.loads(path.read_bytes())
    assert agent["RunAtLoad"] and agent["KeepAlive"]
    assert progress["launchagent-sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert any("bootstrap" in call for call in calls)
    path.write_bytes(path.read_bytes() + b"\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="reconcile"):
        setup_macos.start_langflow(config, tmp_path / "env/bin/python", progress, tmp_path / "progress.json")
    assert path.read_bytes() == before


def test_flow_uses_container_endpoints_without_changing_host_config(tmp_path):
    config = configured(tmp_path)
    before = config.model_dump()
    seen_model = seen_pdf = False
    for name, path in maintained_flows().items():
        flow, _ = configure_flow(json.loads(path.read_text()), name, config, "papers", "project")
        for node in flow["data"]["nodes"]:
            template = node["data"]["node"]["template"]
            if node["data"]["type"] == "OpenAIModel":
                assert template["openai_api_base"]["value"] == config.llm.container_url
                seen_model = True
            if "pdf_url" in template:
                assert template["pdf_url"]["value"] == "http://pdf:8791"
                seen_pdf = True
    assert seen_model and seen_pdf
    assert config.model_dump() == before


def test_launchagent_waits_for_managed_server_shutdown(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import httpx
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    socket_factory = MagicMock()
    socket_factory.return_value.__enter__.return_value.bind.side_effect = [OSError("busy"), None]
    monkeypatch.setattr(setup_macos.socket, "socket", socket_factory)
    sleep = MagicMock()
    monkeypatch.setattr(setup_macos.time, "sleep", sleep)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(subprocess, "run", run)
    setup_macos.start_langflow(configured(tmp_path), tmp_path / "env/bin/python", {}, tmp_path / "progress.json")
    sleep.assert_called_once_with(1)
    assert [call[1] for call in calls] == ["print", "bootout", "bootstrap"]


def test_tokens_are_private_stable_and_reject_symlinks(tmp_path):
    path = tmp_path / "token"
    setup_macos._token(path)
    value = path.read_text()
    setup_macos._token(path)
    assert path.read_text() == value and len(value) >= 32
    assert path.stat().st_mode & 0o077 == 0
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        setup_macos._token(link)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        setup_macos._token(path)


def test_platform_dispatch(tmp_path, monkeypatch):
    from nima_semantica import setup_services
    monkeypatch.setattr(setup_services.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(setup_macos, "provision_macos", lambda *a, **k: calls.append((a, k)))
    config = configured(tmp_path)
    setup_services.provision(config, tmp_path / "config.json", pdf=True, lean=True)
    assert calls[0][1] == {"pdf": True, "lean": True}


def test_provision_rejects_emulated_engine_before_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_macos.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(setup_macos.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: json.dumps({"OSType": "linux", "Architecture": "x86_64"}))
    config = configured(tmp_path)
    with pytest.raises(ValueError, match="ARM64"):
        setup_macos.provision_macos(config, tmp_path / "config.json")
    assert not Path(config.data_root).exists()


def test_macos_provision_isolation_failure_resume_and_conflict(tmp_path, monkeypatch):
    import nima_semantica.cli as cli
    monkeypatch.setattr(setup_macos.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(setup_macos.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: json.dumps({
        "OSType": "linux", "Architecture": "aarch64", "MemoryLimit": True,
        "SwapLimit": True, "PidsLimit": True}))
    calls = []
    fail = [True]
    def run(command, **kwargs):
        calls.append(command)
        if "bwrap" in command and fail[0]:
            fail[0] = False
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cli, "doctor", lambda config: 0)
    monkeypatch.setattr(setup_macos, "start_langflow", lambda *args: None)
    config = Installation(data_root=str(tmp_path / "data space ü"), llm=ModelProfile(model="fixture"))
    destination = tmp_path / "installation.json"
    with pytest.raises(subprocess.CalledProcessError):
        setup_macos.provision_macos(config, destination, pdf=True, lean=True)
    assert not destination.exists()
    assert not any("up" in command for command in calls)
    installed = setup_macos.provision_macos(config, destination, pdf=True, lean=True)
    assert installed.pdf_container_url == "http://127.0.0.1:8791"
    assert installed.pdf_url == "http://127.0.0.1:8791"
    assert config.pdf_url == ""  # Caller state is not partially mutated.
    before = len([c for c in calls if c[:2] == ["docker", "build"] and "nima-sympy:1.14.0-pilot" not in c])
    setup_macos.provision_macos(installed, destination)
    assert len([c for c in calls if c[:2] == ["docker", "build"] and "nima-sympy:1.14.0-pilot" not in c]) == before
    compose = Path(config.data_root) / "installation/compose-macos.yaml"
    compose.write_text(compose.read_text() + "# operator edit\n")
    with pytest.raises(ValueError, match="reconcile"):
        setup_macos.provision_macos(installed, destination)


def test_outer_seccomp_keeps_default_deny_and_adds_only_namespace_calls():
    policy = json.loads((Path(setup_macos.__file__).with_name("profiles") / "docker-bubblewrap-seccomp.json").read_text())
    assert policy["defaultAction"] == "SCMP_ACT_ERRNO"
    assert policy["syscalls"][-1] == {"names": ["clone", "mount", "umount2", "pivot_root", "unshare", "sethostname", "setns"], "action": "SCMP_ACT_ALLOW"}
