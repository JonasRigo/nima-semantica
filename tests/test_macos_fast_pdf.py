import subprocess

import pytest

from nima_semantica.deep_research_fast_pdf import extract_macos


def test_macos_pdf_isolated_and_cleaned_up(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"extracted", stderr=b"")
    monkeypatch.setattr(subprocess, "run", run)
    assert extract_macos(b"%PDF-fixture") == b"extracted"
    command, options = calls[0]
    assert {"--network=none", "--read-only", "--memory=1g", "--memory-swap=1g",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true"} <= set(command)
    assert options["input"] == b"%PDF-fixture" and options["timeout"] == 50
    assert calls[1][0] == ["docker", "rm", "-f", command[command.index("--name") + 1]]


def test_macos_pdf_timeout_still_removes_container(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 50)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="unavailable"):
        extract_macos(b"%PDF-fixture")
    assert calls[-1][:3] == ["docker", "rm", "-f"]


def test_invalid_pdf_never_starts_docker(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Docker started for invalid input"))
    with pytest.raises(ValueError, match="invalid"):
        extract_macos(b"not PDF")
