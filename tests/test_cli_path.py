"""User-shell discovery and environment-bound CLI installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from nima_semantica import cli_path
from nima_semantica.cli import main


@pytest.fixture
def home(tmp_path, monkeypatch):
    directory = tmp_path / "user's [home] $value"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return directory


@pytest.mark.parametrize("platform,shell,expected", [
    ("linux", "bash", [".profile", ".bashrc"]),
    ("darwin", "zsh", [".zprofile", ".zshrc"]),
    ("darwin", "bash", [".profile", ".bashrc"]),
    ("linux", "zsh", [".zprofile", ".zshrc"]),
    ("linux", "sh", [".profile"]),
])
def test_platform_shell_startup_files_and_reruns(home, monkeypatch, platform, shell, expected):
    monkeypatch.setattr(cli_path.sys, "platform", platform)
    monkeypatch.setenv("SHELL", "/bin/" + shell)
    for name in expected:
        (home / name).write_bytes(b"# Existing user configuration\nexport KEEP_ME=yes")
        (home / name).chmod(0o600)
    result = cli_path.install_cli_path()
    assert result["status"] == "installed"
    assert [Path(p).name for p in result["startup_files"]] == expected
    assert os.access(result["executable"], os.X_OK)
    for name in expected:
        path = home / name
        assert path.read_text().startswith("# Existing user configuration\nexport KEEP_ME=yes\n")
        assert path.stat().st_mode & 0o777 == 0o600
        backup, = home.glob(name + ".*.bak")
        assert backup.read_bytes() == b"# Existing user configuration\nexport KEEP_ME=yes"
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}
    cli_path.install_cli_path()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before
    assert set(p for p in home.rglob("*") if p.is_file()) == set(before)


@pytest.mark.parametrize("login", [".bash_profile", ".bash_login"])
def test_bash_uses_existing_login_file(home, login):
    (home / login).write_text("# custom login\n")
    (home / ".profile").write_text("# not read by this login shell\n")
    result = cli_path.install_cli_path()
    assert Path(result["startup_files"][0]) == home / login
    assert (home / ".profile").read_text() == "# not read by this login shell\n"


def test_zsh_respects_zdotdir_and_macos_default(home, tmp_path, monkeypatch):
    monkeypatch.setattr(cli_path.sys, "platform", "darwin")
    monkeypatch.delenv("SHELL")
    directory = tmp_path / "zsh configuration"
    monkeypatch.setenv("ZDOTDIR", str(directory))
    result = cli_path.install_cli_path()
    assert result["shell"] == "zsh"
    assert result["startup_files"] == [str(directory / ".zprofile"), str(directory / ".zshrc")]
    assert not (home / ".zshrc").exists()


def test_launcher_upgrade_preserves_python_environment_and_arguments(home, tmp_path, monkeypatch):
    launcher = home / ".local/bin/nima"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n# previous installation\n")
    # Resolving this symlink would bypass the selected environment.
    base = tmp_path / "base-python"
    base.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    base.chmod(0o755)
    python = tmp_path / "env with 'quotes' $value/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(base)
    monkeypatch.setattr(cli_path.sys, "executable", str(python))
    cli_path.install_cli_path()
    result = subprocess.run([str(launcher), "--config", "a path with spaces", "", "--help"],
                            text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ["-m", "nima_semantica.cli", "--config", "a path with spaces", "", "--help"]
    assert str(base) not in launcher.read_text()
    assert str(python).split("/bin/python")[0] not in os.environ["PATH"]
    backup, = launcher.parent.glob("nima.*.bak")
    assert backup.read_text() == "#!/bin/sh\n# previous installation\n"
    monkeypatch.setattr(cli_path.sys, "executable", "/new-environment/bin/python")
    cli_path.install_cli_path()
    assert "/new-environment/bin/python" in launcher.read_text()
    assert len(list(launcher.parent.glob("nima.*.bak"))) == 2
    assert (home / ".bashrc").read_text().count(cli_path.START) == 1


@pytest.mark.parametrize("shell,startup", [("bash", ".profile"), ("bash", ".bashrc"), ("zsh", ".zprofile"), ("zsh", ".zshrc")])
def test_fresh_shell_finds_and_runs_nima_without_activation(home, monkeypatch, shell, startup):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"native {shell} is unavailable on this host")
    monkeypatch.setenv("SHELL", executable)
    result = cli_path.install_cli_path()
    environment = dict(os.environ, PATH="/usr/bin:/bin", HOME=str(home))
    environment.pop("VIRTUAL_ENV", None)
    flags = ["--noprofile", "--norc"] if shell == "bash" else ["-f"]
    command = f'. "$HOME/{startup}"; . "$HOME/{startup}"; command -v nima; printf "%s\\n" "$PATH"; nima setup --help'
    completed = subprocess.run([executable, *flags, "-c", command], env=environment,
                               cwd=home, capture_output=True, text=True, check=True)
    lines = completed.stdout.splitlines()
    assert lines[0] == result["executable"]
    assert lines[1].split(os.pathsep).count(str(home / ".local/bin")) == 1
    assert "--no-modify-path" in completed.stdout


@pytest.mark.parametrize("destination", [".bashrc", ".local/bin/nima"])
def test_symlink_destinations_are_preserved_before_any_writes(home, tmp_path, destination):
    target = tmp_path / "operator-file"
    target.write_text("keep this\n")
    link = home / destination
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    with pytest.raises(ValueError, match="--no-modify-path"):
        cli_path.install_cli_path()
    assert target.read_text() == "keep this\n" and link.is_symlink()
    assert not (home / ".profile").exists()
    if destination == ".bashrc":
        assert not (home / ".local/bin/nima").exists()


def test_malformed_managed_block_is_preserved_before_any_writes(home):
    path = home / ".bashrc"
    path.write_text(cli_path.START + "\n# missing end\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Malformed NIMA PATH block"):
        cli_path.install_cli_path()
    assert path.read_bytes() == before
    assert not (home / ".local/bin/nima").exists()


def test_unknown_shell_gets_manual_instructions(home, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/fish")
    result = cli_path.install_cli_path()
    assert result["status"] == "manual_shell"
    assert result["activation_command"] is None
    assert result["startup_files"] == []
    assert "fish" in result["message"]
    assert Path(result["executable"]).is_file()


@pytest.mark.parametrize("skip", [False, True])
def test_setup_integrates_path_installation_and_opt_out(home, tmp_path, capsys, skip):
    args = ["--config", str(tmp_path / "installation.json"), "setup", "--non-interactive",
            "--model", "fixture", "--data-root", str(tmp_path / "data")]
    if skip:
        args.append("--no-modify-path")
    assert main(args) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)["cli_path"]
    assert result["status"] == ("skipped" if skip else "installed")
    assert (home / ".local/bin/nima").exists() is (not skip)
    assert (home / ".bashrc").exists() is (not skip)
    if not skip:
        assert result["activation_command"] in output.err
        assert "Open a new terminal" in output.err
