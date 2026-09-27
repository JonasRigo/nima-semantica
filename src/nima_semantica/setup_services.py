"""Explicit local Linux provisioning and configured embedding transport."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import httpx

from .installation import assets, write_json


def probe_embedding(model, base_url="http://127.0.0.1:11434"):
    with httpx.Client(base_url=base_url, timeout=60) as client:
        response = client.get("/api/tags"); response.raise_for_status()
        matches = [m for m in response.json()["models"] if m["name"] == model or m.get("model") == model]
        if len(matches) != 1:
            raise ValueError("Install the selected embedding model in Ollama before setup")
        response = client.post("/api/embed", json={"model": model, "input": ["NIMA installation check"]}); response.raise_for_status()
        vector = response.json()["embeddings"][0]
    return {"provider": "ollama", "model": model, "base_url": base_url, "container_url": base_url,
            "revision": matches[0]["digest"], "dimension": len(vector)}


def embedding_provider(config, *, credential_value=None):
    from .providers import ModelManifest
    profile = config.embedding
    if profile is None:
        raise ValueError("Vector indexing requires a configured embedding profile")
    manifest = ModelManifest(provider=profile.provider, model=profile.model, revision=profile.revision,
                             dimension=profile.dimension, normalization="l2", parameters={})
    class Provider:
        def embed(self, ignored, texts):
            headers = {}
            if profile.provider != "ollama":
                token = credential_value or os.environ.get(profile.credential)
                if not token:
                    raise ValueError("Embedding credential environment variable is missing")
                headers["Authorization"] = "Bearer " + token
            with httpx.Client(base_url=profile.base_url.rstrip("/"), headers=headers, timeout=120) as client:
                if profile.provider == "ollama":
                    tags = client.get("/api/tags"); tags.raise_for_status()
                    exact = [m for m in tags.json()["models"] if m.get("name") == profile.model or m.get("model") == profile.model]
                    if len(exact) != 1 or exact[0]["digest"] != profile.revision:
                        raise ValueError("Embedding model revision changed; configure a new index explicitly")
                    response = client.post("/api/embed", json={"model": profile.model, "input": texts})
                    response.raise_for_status(); vectors = response.json()["embeddings"]
                else:
                    response = client.post("/embeddings", json={"model": profile.model, "input": texts})
                    response.raise_for_status(); vectors = [r["embedding"] for r in sorted(response.json()["data"], key=lambda r: r["index"])]
            if len(vectors) != len(texts) or any(len(v) != profile.dimension for v in vectors):
                raise ValueError("Embedding response dimension/count differs from configured identity")
            return vectors, manifest
        def embed_query(self, profile_name, text):
            return self.embed(profile_name, [text])
    return Provider(), manifest


def provision(config, config_path, *, pdf=False, lean=False):
    if sys.platform != "linux":
        raise ValueError("Managed provisioning currently supports Linux")
    for command in ("docker", "systemctl", "bwrap", "prlimit"):
        if not shutil.which(command):
            raise ValueError(f"Install prerequisite {command} before provisioning")
    root = Path(config.data_root)
    work = root / "installation"
    work.mkdir(parents=True, exist_ok=True)
    progress_path = work / "stages.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    def run(name, command, fingerprint="", always=False):
        checkpoint = {"command": command, "fingerprint": fingerprint}
        if not always and progress.get(name) == checkpoint:
            return
        with (work / f"{name}.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        progress[name] = checkpoint; write_json(progress_path, progress)
    # Build from this installed application's source, including installation resources.
    context = work / "langflow-build"
    application = context / "application"
    # This directory is generated from the installed release, never a user workspace.
    # Recreate it so upgrades cannot carry removed modules into the next image.
    if application.is_symlink():
        raise ValueError("Generated application directory must not be a symlink")
    if application.exists():
        shutil.rmtree(application)
    application.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(__file__).parent, application / "src/nima_semantica", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    resource_root = assets()
    inventory = json.loads((resource_root / "release-resources.json").read_text())["files"]
    for name in inventory:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid installed resource path")
        target = application / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resource_root / relative, target)
    (context / "Dockerfile").write_text("FROM python:3.13-slim\nRUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\nRUN pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu\nCOPY application /opt/nima-app\nRUN pip install -c /opt/nima-app/constraints-tested.txt '/opt/nima-app[langflow,mcp]'\nCMD [\"langflow\",\"run\",\"--host\",\"127.0.0.1\",\"--port\",\"7860\"]\n")
    fingerprint = hashlib.sha256(b"".join(str(p.relative_to(context)).encode() + p.read_bytes() for p in sorted(context.rglob("*")) if p.is_file())).hexdigest()
    run("langflow-image", ["docker", "build", "-t", "nima-langflow:0.1.0", str(context)], fingerprint=fingerprint)
    sympy_context = work / "symbolic-build"; sympy_context.mkdir(exist_ok=True)
    symbolic_dockerfile = (assets() / "deploy/sympy.Dockerfile").read_text()
    (sympy_context / "Dockerfile").write_text(symbolic_dockerfile)
    run("symbolic-image", ["docker", "build", "-t", "nima-sympy:1.14.0-pilot", str(sympy_context)], fingerprint=hashlib.sha256(symbolic_dockerfile.encode()).hexdigest())
    def service(name, command):
        # Quoted argv preserves paths; percent is escaped for systemd specifiers.
        argv = " ".join('"' + str(a).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"' for a in command)
        unit = "[Unit]\nDescription=NIMA " + name + "\n[Service]\nExecStart=" + argv + "\nRestart=on-failure\n[Install]\nWantedBy=default.target\n"
        target = Path.home() / ".config/systemd/user" / ("nima-" + name + ".service")
        if target.exists() and target.read_text() != unit:
            raise ValueError(f"Existing service {target} differs; reconcile before managed setup")
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(unit)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", target.name], check=True)
    config.symbolic_url = "http://127.0.0.1:7871"
    config.symbolic_token_file = str(root / "symbolic/token")
    service("symbolic", [sys.executable, assets() / "deploy/serve_symbolic_worker.py", "--token-file", config.symbolic_token_file])
    if pdf:
        interpreter = shutil.which("python3.11")
        if not interpreter:
            raise ValueError("PDF provisioning requires python3.11")
        environment = root / "pdf-env"
        run("pdf-venv", [interpreter, "-m", "venv", str(environment)])
        python = str(environment / "bin/python")
        run("pdf-dependencies", [python, "-m", "pip", "install", "--extra-index-url", "https://download.pytorch.org/whl/cpu", "-r", str(assets() / "deploy/pdf-tested-lock.txt"), "--no-deps", str(application)])
        models = root / "pdf-models"
        run("pdf-assets", [python, "-m", "nima_semantica.pdf.assets", "prepare", str(models)])
        run("pdf-verify", [python, "-m", "nima_semantica.pdf.assets", "verify", str(models)])
        config.pdf_url = "http://127.0.0.1:8791"; config.pdf_token_file = str(root / "pdf-service/token")
        service("pdf", [python, "-m", "nima_semantica.pdf.server", "--models", models, "--data", root / "pdf-service"])
    if lean:
        if not shutil.which("elan"):
            raise ValueError("Install Elan before provisioning Lean")
        version = "leanprover/lean4:v4.32.1"
        run("lean-toolchain", ["elan", "toolchain", "install", version])
        binary = Path(subprocess.check_output(["elan", "which", "lean"],
            env={**os.environ, "ELAN_TOOLCHAIN": version}, text=True).strip())
        pin = root / "lean-project.json"
        if not pin.exists():
            run("lean-pin", [sys.executable, str(assets() / "deploy/configure_lean.py"), "--toolchain", str(binary.parent.parent), "--output", str(pin)])
        config.lean_url = "http://127.0.0.1:7873"; config.lean_token_file = str(root / "lean/token")
        service("lean", [sys.executable, assets() / "deploy/serve_lean_worker.py", "--config", pin, "--token-file", config.lean_token_file])
    import yaml
    from urllib.parse import urlsplit
    allowed_hosts = {urlsplit(profile.base_url).hostname for profile in [config.llm, *config.overrides.values()]}
    if config.embedding:
        allowed_hosts.add(urlsplit(config.embedding.container_url).hostname)
    allowed_hosts.discard(None)
    compose = {"services": {"langflow": {"image": "nima-langflow:0.1.0", "network_mode": "host",
        "environment": {"LANGFLOW_AUTO_LOGIN": "true", "LANGFLOW_SKIP_AUTH_AUTO_LOGIN": "true", "DO_NOT_TRACK": "true",
            "LANGFLOW_SSRF_ALLOWED_HOSTS": ",".join(sorted(allowed_hosts)),
            "LANGFLOW_CONFIG_DIR": "/var/lib/langflow", "LANGFLOW_SAVE_DB_IN_CONFIG_DIR": "true", "LANGFLOW_COMPONENTS_PATH": "/nima-components",
            "LANGFLOW_WORKFLOW_EXECUTION_TIMEOUT": "3600", "LANGFLOW_WORKER_TIMEOUT": "3600",
            "LANGFLOW_MCP_TOOL_EXECUTION_TIMEOUT": "3600", "LANGFLOW_MCP_SESSION_IDLE_TIMEOUT": "4000",
            "NIMA_STORE_ROOT": str(root / "corpus"), "NIMA_SYMBOLIC_WORKER_URL": config.symbolic_url,
            "NIMA_SYMBOLIC_WORKER_TOKEN_FILE": config.symbolic_token_file, "NIMA_LEAN_WORKER_URL": config.lean_url,
            "NIMA_LEAN_WORKER_TOKEN_FILE": config.lean_token_file},
        "volumes": [f"{root}:{root}", f"{root / 'langflow'}:/var/lib/langflow", f"{assets() / 'deploy/langflow_components'}:/nima-components:ro"]}}}
    compose_path = work / "compose.yaml"; compose_path.write_text(yaml.safe_dump(compose))
    run("langflow-start", ["docker", "compose", "-p", "nima-release", "-f", str(compose_path), "up", "-d"], always=True)
    write_json(config_path, config.model_dump())
    return config
