"""Actionable Langflow failures without mislabeling model-provider errors."""
import hashlib
import os
from pathlib import Path
import shlex

import httpx
import pytest

from nima_semantica import cli, release_cli


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_chat_names_langflow_and_the_managed_linux_start_command(lexical_installation, monkeypatch, capsys, error_type):
    config = lexical_installation
    compose = Path(config.data_root) / "installation/compose.yaml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n")
    monkeypatch.setattr(cli.sys, "platform", "linux")

    def disconnected(*args):
        raise error_type("All connection attempts failed", request=httpx.Request(
            "POST", config.langflow_url + "/api/v1/mcp/project/test/streamable"))

    monkeypatch.setattr(release_cli, "chat_command", disconnected)
    assert cli.main(["chat", "--corpus", "papers", "--project", "test", "--knowledge", "graph",
                     "--question", "Which assumption remains open?", "--json"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.startswith(f"nima: {error_type.__name__}: All connection attempts failed. Make sure Langflow")
    assert "Make sure Langflow is running and reachable at http://127.0.0.1:7860." in captured.err
    command = captured.err.split("You can start it with: ", 1)[1].strip()
    assert shlex.split(command) == ["docker", "compose", "-p", "nima-release", "-f", str(compose), "start", "langflow"]


def test_managed_macos_hint_uses_its_launchagent(lexical_installation, tmp_path, monkeypatch):
    config = lexical_installation
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    label = "org.nima.langflow." + hashlib.sha256(config.data_root.encode()).hexdigest()[:12]
    agent = tmp_path / "Library/LaunchAgents" / (label + ".plist")
    agent.parent.mkdir(parents=True)
    agent.write_text("placeholder")
    error = httpx.ConnectError("All connection attempts failed", request=httpx.Request("GET", config.langflow_url + "/health"))
    hint = cli._langflow_connection_hint(error, config)
    assert hint.endswith(shlex.join(["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"]))
    assert "docker" not in hint


def test_remote_langflow_hint_does_not_start_a_local_service(lexical_installation):
    config = lexical_installation.model_copy(update={"langflow_url": "https://langflow.example.test"})
    error = httpx.ConnectError("All connection attempts failed", request=httpx.Request("GET", config.langflow_url + "/health"))
    hint = cli._langflow_connection_hint(error, config)
    assert "Langflow is running and reachable at https://langflow.example.test:443" in hint
    assert "start it with" not in hint


def test_stale_project_endpoint_is_identified_without_suggesting_local_startup(lexical_installation):
    error = httpx.ConnectError("All connection attempts failed", request=httpx.Request(
        "POST", "http://old-owner.example.test:7860/api/v1/mcp/project/test/streamable"))
    hint = cli._langflow_connection_hint(error, lexical_installation)
    assert "old-owner.example.test:7860" in hint
    assert "start it with" not in hint


def test_manual_langflow_hint_quotes_the_native_environment_path(lexical_installation):
    config = lexical_installation.model_copy(update={"data_root": lexical_installation.data_root + " with spaces"})
    python = Path(config.data_root) / "langflow-env/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    error = httpx.ConnectError("All connection attempts failed", request=httpx.Request("GET", config.langflow_url + "/health"))
    hint = cli._langflow_connection_hint(error, config)
    command = hint.split("You can start it with: ", 1)[1]
    assert shlex.split(command) == [str(python), "-m", "langflow", "run", "--host", "127.0.0.1", "--port", "7860"]


def test_hint_does_not_echo_endpoint_credentials(lexical_installation):
    url = "http://operator:private-password@127.0.0.1:7860"
    config = lexical_installation.model_copy(update={"langflow_url": url})
    error = httpx.ConnectError("All connection attempts failed", request=httpx.Request("GET", url + "/health?key=private-key"))
    hint = cli._langflow_connection_hint(error, config)
    assert "http://127.0.0.1:7860" in hint
    assert "operator" not in hint and "private-password" not in hint and "private-key" not in hint


@pytest.mark.parametrize("url", ["https://models.example.test/v1/chat/completions", "http://127.0.0.1:11434/api/chat"])
def test_model_provider_connection_error_stays_unchanged(lexical_installation, monkeypatch, capsys, url):
    def disconnected(*args):
        raise httpx.ConnectError("Provider unavailable", request=httpx.Request("POST", url))

    monkeypatch.setattr(release_cli, "chat_command", disconnected)
    assert cli.main(["chat", "--corpus", "papers", "--question", "Question"]) == 2
    assert capsys.readouterr().err == "nima: ConnectError: Provider unavailable\n"


def test_connection_error_without_request_or_configuration_stays_unchanged(lexical_installation):
    error = httpx.ConnectError("No request metadata")
    assert cli._langflow_connection_hint(error, lexical_installation) == ""
    assert cli._langflow_connection_hint(error, None) == ""
