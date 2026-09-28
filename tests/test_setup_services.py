import json
import subprocess
from pathlib import Path

import pytest

from nima_semantica.installation import Installation, ModelProfile
from nima_semantica.setup_services import provision


def test_provision_failure_resume_and_generated_local_configuration(tmp_path, monkeypatch):
    import nima_semantica.setup_services as services
    monkeypatch.setattr(services.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    fail = [True]
    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "build", "-t"] and fail[0]:
            fail[0] = False
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(services.subprocess, "run", run)
    config = Installation(data_root=str(tmp_path / "data"), llm=ModelProfile(provider="ollama", model="fixture", base_url="http://127.0.0.1:11434/v1"))
    path = tmp_path / "installation.json"
    with pytest.raises(subprocess.CalledProcessError):
        provision(config, path)
    assert not path.exists()
    provision(config, path)
    previous_builds = len([c for c in calls if c[:2] == ["docker", "build"]])
    stale = tmp_path / "data/installation/langflow-build/application/src/nima_semantica/retired.py"
    stale.write_text("# removed from the installed release\n")
    provision(config, path)
    assert not stale.exists()
    assert len([c for c in calls if c[:2] == ["docker", "build"]]) == previous_builds
    import yaml
    compose = yaml.safe_load((tmp_path / "data/installation/compose.yaml").read_text())
    environment = compose["services"]["langflow"]["environment"]
    assert environment["LANGFLOW_SSRF_ALLOWED_HOSTS"] == "127.0.0.1"
    assert environment["LANGFLOW_MCP_TOOL_EXECUTION_TIMEOUT"] == "3600"
    assert environment["NIMA_STORE_ROOT"] == str(tmp_path / "data/corpus")
    assert json.loads(path.read_text())["symbolic_url"] == "http://127.0.0.1:7871"
    assert (tmp_path / "home/.config/systemd/user/nima-symbolic.service").is_file()
    # SymbolicWorker overrides the entrypoint with this path, including on a fresh host.
    dockerfile = (tmp_path / "data/installation/symbolic-build/Dockerfile").read_text()
    assert "python -m venv /app/.venv" in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/python", "-I", "-u"]' in dockerfile
    langflow_dockerfile = (tmp_path / "data/installation/langflow-build/Dockerfile").read_text()
    assert "-c /opt/nima-app/constraints-tested.txt" in langflow_dockerfile
    assert (tmp_path / "data/installation/langflow-build/application/constraints-tested.txt").is_file()
    application = tmp_path / "data/installation/langflow-build/application"
    inventory = json.loads((application / "release-resources.json").read_text())["files"]
    assert all((application / name).is_file() for name in inventory)
    assert (application / ".gitignore").is_file()
    assert (application / "deploy/release_policy.py").is_file()
    assert "apt-get install -y --no-install-recommends git" in langflow_dockerfile
    def locate_lean(command, **kwargs):
        # Elan selects toolchains through the environment for its `which` command.
        assert command == ["elan", "which", "lean"]
        assert kwargs["env"]["ELAN_TOOLCHAIN"] == "leanprover/lean4:v4.32.1"
        return str(tmp_path / "lean/bin/lean") + "\n"
    monkeypatch.setattr(services.subprocess, "check_output", locate_lean)
    provision(config, path, lean=True)
    assert json.loads(path.read_text())["lean_url"] == "http://127.0.0.1:7873"
    assert (tmp_path / "home/.config/systemd/user/nima-lean.service").is_file()
