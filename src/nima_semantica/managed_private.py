"""Managed loopback private owners with explicit systemd lifecycle files."""
from pathlib import Path
import subprocess
import sys

from .installation import identifier, load_installation, write_json
from .private_service import private_token
from .storage import atomic_bytes


def unit_argument(value):
    if any(character in str(value) for character in "\n\r\x00"):
        raise ValueError("systemd arguments must not contain line breaks or NUL")
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def configure_private_services(binding, config_path, directory, *, proof_port=7881, math_port=7882):
    """Prepare owners before harness reconciliation; existing scope is preserved."""
    if proof_port == math_port or not all(1024 <= port <= 65535 for port in (proof_port, math_port)):
        raise ValueError("private owners require distinct unprivileged ports")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    installation = load_installation(config_path)
    import json
    from .proof_mcp import ProofConfig
    from .math_session_service import MathServiceConfig
    target = dict(binding)
    target["private_services"] = {}
    units = []
    for kind, contract in (("proof", ProofConfig), ("math", MathServiceConfig)):
        selected = contract.model_validate_json(Path(binding[kind + "_config"]).read_text())
        if (selected.corpus_id, selected.project_id) != (binding["corpus_id"], binding["project_id"]):
            raise ValueError("private owner configuration scope differs from binding")
        if Path(binding[kind + "_config"]).stat().st_mode & 0o077:
            raise ValueError("private owner configuration must remain private")
    for kind, port, contract in (("proof", proof_port, ProofConfig), ("math", math_port, MathServiceConfig)):
        selected = contract.model_validate_json(Path(binding[kind + "_config"]).read_text())
        if (selected.corpus_id, selected.project_id) != (binding["corpus_id"], binding["project_id"]):
            raise ValueError("private owner configuration scope differs from binding")
        token_file = directory / (kind + ".token")
        private_token(token_file, create=True)
        environment = {"NIMA_CONFIG": str(Path(config_path).resolve()), "NIMA_STORE_ROOT": binding["store_root"]}
        if binding.get("mcp_url"):
            environment["NIMA_RESEARCH_MCP_URL"] = binding["mcp_url"]
        for worker in ("symbolic", "lean"):
            if getattr(installation, worker + "_url"):
                prefix = "NIMA_" + worker.upper() + "_WORKER"
                environment[prefix + "_URL"] = getattr(installation, worker + "_url")
                environment[prefix + "_TOKEN_FILE"] = getattr(installation, worker + "_token_file")
        command = [sys.executable, "-m", "nima_semantica.private_service", "--kind", kind,
            "--config", binding[kind + "_config"], "--token-file", str(token_file), "--port", str(port)]
        name = "nima-" + identifier(binding["corpus_id"]) + "-" + identifier(binding["project_id"]) + "-" + kind + ".service"
        unit = ("[Unit]\nDescription=NIMA private " + kind + " owner\n[Service]\nType=simple\nUMask=0077\n" +
            "".join("Environment=" + unit_argument(k + "=" + v) + "\n" for k, v in environment.items()) +
            "ExecStart=" + " ".join(unit_argument(arg) for arg in command) +
            "\nRestart=on-failure\nTimeoutStopSec=45\n[Install]\nWantedBy=default.target\n")
        path = directory / name
        if path.exists() and path.read_text() != unit:
            raise ValueError("managed owner unit differs; preserve and reconcile it explicitly")
        atomic_bytes(path, unit.encode())
        units.append(str(path))
        target["private_services"][kind] = {"url": f"http://127.0.0.1:{port}/mcp", "token_file": str(token_file),
            "service_unit": name, "owner_uid": __import__("os").getuid()}
    write_json(directory / "binding.json", target)
    return {"binding_file": str(directory / "binding.json"), "units": units,
            "start": "Install the generated units with systemctl --user link, then enable --now; reconcile the harness to binding.json."}


def start_private_services(result):
    for unit in result["units"]:
        subprocess.run(["systemctl", "--user", "link", unit], check=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for unit in result["units"]:
        subprocess.run(["systemctl", "--user", "enable", "--now", Path(unit).name], check=True)
