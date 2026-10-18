"""Expose the installed CLI without activating its Python environment."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile


START = "# >>> NIMA CLI PATH >>>"
END = "# <<< NIMA CLI PATH <<<"


def _startup_files(home: Path, shell: str) -> list[Path]:
    if shell == "zsh":
        directory = Path(os.environ.get("ZDOTDIR") or home).expanduser().resolve()
        return [directory / ".zprofile", directory / ".zshrc"]
    if shell == "bash":
        login = next((home / name for name in (".bash_profile", ".bash_login", ".profile")
                      if (home / name).exists() or (home / name).is_symlink()), home / ".profile")
        return [login, home / ".bashrc"]
    if shell in {"sh", "dash", "ksh"}:
        return [home / ".profile"]
    return []


def _with_path_block(text: str, block: str) -> str:
    if START not in text and END not in text:
        return text + ("\n" if text and not text.endswith("\n") else "") + "\n" + block
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise ValueError("Malformed NIMA PATH block; repair it or use --no-modify-path")
    start, stop = text.index(START), text.index(END) + len(END)
    return text[:start] + block.rstrip("\n") + text[stop:]


def _write(path: Path, data: bytes, mode: int) -> list[str]:
    previous = path.read_bytes() if path.exists() else None
    backups = []
    if previous == data:
        if stat.S_IMODE(path.stat().st_mode) != mode:
            path.chmod(mode)
        return backups
    path.parent.mkdir(parents=True, exist_ok=True)
    if previous is not None:
        backup = path.with_name(path.name + "." + hashlib.sha256(previous).hexdigest()[:12] + ".bak")
        if backup.is_symlink():
            raise ValueError(f"Refusing symlink PATH backup: {backup}")
        if not backup.exists():
            shutil.copy2(path, backup)
        backups.append(str(backup))
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".nima-path-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return backups


def install_cli_path() -> dict:
    """Install a user launcher and idempotent shell startup entries on Linux/macOS."""
    if sys.platform not in {"linux", "darwin"}:
        return {"status": "unsupported", "message": "Automatic CLI PATH setup supports Linux and macOS."}
    home = Path.home().resolve()
    directory = home / ".local/bin"
    launcher = directory / "nima"
    shell = Path(os.environ.get("SHELL") or ("zsh" if sys.platform == "darwin" else "bash")).name
    files = _startup_files(home, shell)
    quoted = shlex.quote(str(directory))
    activation = f'export PATH={quoted}"${{PATH:+:$PATH}}"'
    block = (f'{START}\ncase ":${{PATH-}}:" in\n'
             f'    *:{quoted}:*) ;;\n    *) {activation} ;;\nesac\n{END}\n')
    # Keep the virtual-environment executable path: resolving its symlink would
    # select the base Python and lose the installation's packages.
    python = shlex.quote(str(Path(sys.executable).absolute()))
    planned = {launcher: (f'#!/bin/sh\n# NIMA managed CLI launcher\n'
                          f'exec {python} -m nima_semantica.cli "$@"\n').encode()}
    for path in [launcher, *files]:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Refusing non-regular PATH destination: {path}; use --no-modify-path to manage PATH yourself")
    for path in files:
        text = path.read_bytes().decode("utf-8") if path.exists() else ""
        planned[path] = _with_path_block(text, block).encode("utf-8")
    backups = []
    for path, data in planned.items():
        mode = 0o755 if path == launcher else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        backups.extend(_write(path, data, mode))
    return {"status": "installed" if files else "manual_shell", "executable": str(launcher),
            "shell": shell, "startup_files": [str(path) for path in files], "backups": backups,
            "activation_command": activation if files else None,
            "message": ("Open a new terminal, or run the activation command below in this terminal."
                        if files else f"Add {directory} to PATH in your {shell} shell configuration.")}
