import os
from pathlib import Path
import subprocess

import pytest

from nima_semantica.studio_export import open_studio


def test_launcher_preserves_paths_and_config_precedence(tmp_path, monkeypatch):
    executable = tmp_path / "OKF Studio.app/Contents/MacOS/okf-viewer"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    directory = tmp_path / "export with spaces"
    directory.mkdir()
    calls = []
    def launch(args, **kwargs):
        calls.append(args)
        return type("Process", (), {"pid": 123})()
    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setenv("NIMA_OKF_STUDIO", "/missing")
    assert open_studio(directory, str(executable)) == 123
    assert calls == [[str(executable), "open", str(directory.resolve())]]
    monkeypatch.setenv("NIMA_OKF_STUDIO", str(executable))
    assert open_studio(directory) == 123


def test_launcher_errors_retain_export(tmp_path, monkeypatch):
    export = tmp_path / "bundle"
    export.mkdir()
    for path in (tmp_path / "missing", export):
        with pytest.raises(ValueError, match="executable"):
            open_studio(export, str(path))
    executable = tmp_path / "studio"
    executable.write_text("invalid executable format")
    executable.chmod(0o700)
    with pytest.raises(ValueError, match="could not launch"):
        open_studio(export, str(executable))
    assert export.exists()
