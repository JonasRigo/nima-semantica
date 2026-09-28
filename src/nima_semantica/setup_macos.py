"""Native Langflow and Docker workers for macOS Apple silicon.

Only the symbolic controller receives the Docker socket. PDF and Lean retain
their Linux Bubblewrap boundary; setup tests that boundary before starting them.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import yaml

from . import __version__
from .installation import assets, write_json


def container_url(url):
    """Translate only host loopback addresses, preserving ports and URL paths."""
    value = urlsplit(url)
    if value.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return url
    if value.username or value.password:
        raise ValueError("Credentials must not be embedded in service URLs")
    host = "host.docker.internal" + (f":{value.port}" if value.port else "")
    return urlunsplit(value._replace(netloc=host))


def _token(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Worker token must not be a symlink")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not path.is_file() or path.stat().st_mode & 0o077 or len(path.read_text().strip()) < 32:
            raise ValueError("Existing worker token must be a private file of at least 32 characters")
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(48))


def _bind(source, target=None, *, readonly=False):
    return {"type": "bind", "source": str(source), "target": str(target or source),
            "read_only": readonly, "bind": {"create_host_path": False}}


def compose_document(config, *, pdf, lean):
    """Pure deployment description, also used by offline topology tests."""
    root = Path(config.data_root)
    image = f"nima-macos-worker:{__version__}"
    uid = f"{os.getuid()}:{os.getgid()}"
    common = {"restart": "unless-stopped", "init": True,
              "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}}
    services = {
        "symbolic": {**common, "image": image, "ports": ["127.0.0.1:7871:7871"],
            "user": uid, "group_add": ["0"], "environment": {"HOME": "/tmp"},
            "command": ["python", "/opt/nima-app/deploy/serve_symbolic_worker.py", "--host", "0.0.0.0",
                        "--token-file", config.symbolic_token_file],
            "volumes": [_bind("/var/run/docker.sock"), _bind(config.symbolic_token_file, readonly=True)],
            "read_only": True, "tmpfs": ["/tmp:rw,nosuid,size=64m"],
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]},
    }
    # A non-root Bubblewrap launcher needs namespace syscalls and an unmasked
    # outer /proc to construct its own private proc mount. No capabilities are
    # granted. Lean installs its stricter syscall filter inside that boundary.
    policy = Path(__file__).with_name("profiles") / "docker-bubblewrap-seccomp.json"
    isolated = {**common, "user": uid, "read_only": True, "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true", "systempaths=unconfined", f"seccomp={policy}"],
                "pids_limit": 256, "mem_limit": "8g", "memswap_limit": "8g", "cpus": 4,
                "tmpfs": ["/tmp:rw,nosuid,size=256m"]}
    if lean:
        services["lean"] = {**isolated, "image": f"nima-lean:{__version__}",
            "ports": ["127.0.0.1:7873:7873"],
            "command": ["python", "/opt/nima-app/deploy/serve_lean_worker.py", "--host", "0.0.0.0",
                        "--config", "/opt/lean-project.json", "--token-file", config.lean_token_file],
            "volumes": [_bind(config.lean_token_file, readonly=True)]}
    if pdf:
        services["pdf"] = {**isolated, "image": f"nima-pdf:{__version__}",
            "ports": ["127.0.0.1:8791:8791"], "environment": {"HOME": "/tmp", "HF_HUB_OFFLINE": "1"},
            "command": ["python", "-m", "nima_semantica.pdf.server", "--host", "0.0.0.0",
                        "--models", str(root / "pdf-models"), "--data", str(root / "pdf-service")],
            "volumes": [_bind(root / "pdf-models", readonly=True), _bind(root / "pdf-service")]}
    for name, service in services.items():
        port = {"langflow": 7860, "symbolic": 7871, "lean": 7873, "pdf": 8791}[name]
        service["healthcheck"] = {"test": ["CMD", "python", "-c",
            f"import socket; socket.create_connection(('127.0.0.1', {port}), 3).close()"],
            "interval": "5s", "timeout": "5s", "retries": 60, "start_period": "30s"}
    return {"services": services}


def langflow_agent(config, python):
    """Native corpus owner: never cross Docker Desktop's filesystem boundary."""
    root = Path(config.data_root)
    label = "org.nima.langflow." + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    allowed = {urlsplit(p.base_url).hostname for p in [config.llm, *config.overrides.values()]}
    if config.embedding:
        allowed.add(urlsplit(config.embedding.base_url).hostname)
    allowed.discard(None)
    environment = {
        "HOME": str(Path.home()), "PATH": str(Path(python).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "LANGFLOW_AUTO_LOGIN": "true", "LANGFLOW_SKIP_AUTH_AUTO_LOGIN": "true", "DO_NOT_TRACK": "true",
        "LANGFLOW_SSRF_ALLOWED_HOSTS": ",".join(sorted(allowed)),
        "LANGFLOW_CONFIG_DIR": str(root / "langflow"), "LANGFLOW_SAVE_DB_IN_CONFIG_DIR": "true",
        "LANGFLOW_COMPONENTS_PATH": str(assets() / "deploy/langflow_components"),
        "NIMA_STORE_ROOT": str(root / "corpus"),
        "NIMA_SYMBOLIC_WORKER_URL": config.symbolic_url,
        "NIMA_SYMBOLIC_WORKER_TOKEN_FILE": config.symbolic_token_file,
        "NIMA_LEAN_WORKER_URL": config.lean_url, "NIMA_LEAN_WORKER_TOKEN_FILE": config.lean_token_file,
        "LANGFLOW_WORKFLOW_EXECUTION_TIMEOUT": "3600", "LANGFLOW_WORKER_TIMEOUT": "3600",
        "LANGFLOW_MCP_TOOL_EXECUTION_TIMEOUT": "3600", "LANGFLOW_MCP_SESSION_IDLE_TIMEOUT": "4000",
    }
    return {"Label": label, "ProgramArguments": [str(python), "-m", "langflow", "run", "--host", "127.0.0.1", "--port", "7860"],
            "EnvironmentVariables": environment, "WorkingDirectory": str(root),
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "StandardOutPath": str(root / "installation/langflow.stdout.log"),
            "StandardErrorPath": str(root / "installation/langflow.stderr.log")}


def start_langflow(config, python, progress, progress_path):
    agent = langflow_agent(config, python)
    path = Path.home() / "Library/LaunchAgents" / (agent["Label"] + ".plist")
    rendered = plistlib.dumps(agent)
    if path.is_symlink() or (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != progress.get("launchagent-sha256")):
        raise ValueError("Existing Langflow LaunchAgent changed; reconcile it before provisioning")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rendered)
    progress["launchagent-sha256"] = hashlib.sha256(rendered).hexdigest()
    write_json(progress_path, progress)
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + agent["Label"]
    restarting = subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0
    if restarting:
        subprocess.run(["launchctl", "bootout", service], check=True, capture_output=True)
    # bootout returns before Langflow's child server has necessarily exited.
    for attempt in range(31 if restarting else 1):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", 7860))
                break
            except OSError as exc:
                if not restarting or attempt == 30:
                    raise ValueError("Port 7860 is unavailable; stop the conflicting service before managed setup") from exc
        time.sleep(1)
    for attempt in range(31 if restarting else 1):
        loaded = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        if loaded.returncode == 0:
            break
        # launchd may still be removing the prior job after its listener exits.
        if loaded.returncode != 5 or not restarting or attempt == 30:
            loaded.check_returncode()
        time.sleep(1)
    import httpx
    for _ in range(120):
        try:
            response = httpx.get(config.langflow_url + "/health", timeout=3)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise ValueError("Native Langflow failed readiness; inspect installation/langflow.stderr.log")


def _application(destination):
    """Stage from the installed wheel, not a source checkout assumption."""
    if destination.is_symlink():
        raise ValueError("Generated application must not be a symlink")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copytree(Path(__file__).parent, destination / "src/nima_semantica",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    resource_root = assets()
    for name in json.loads((resource_root / "release-resources.json").read_text())["files"]:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid installed resource path")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resource_root / relative, target)


def provision_macos(config, config_path, *, pdf=False, lean=False):
    if platform.machine() != "arm64":
        raise ValueError("Managed macOS provisioning requires native Apple silicon Python")
    interpreter = sys.executable if sys.version_info[:2] == (3, 11) else shutil.which("python3.11")
    if not interpreter:
        raise ValueError("Native Langflow provisioning requires python3.11")
    if not shutil.which("docker"):
        raise ValueError("Install and start Docker Desktop with Docker Compose before provisioning")
    info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True, timeout=30))
    if info.get("OSType") != "linux" or info.get("Architecture") not in {"aarch64", "arm64"}:
        raise ValueError("Docker must use a Linux ARM64 engine; emulation is not a supported installation")
    if not all(info.get(name) for name in ("MemoryLimit", "SwapLimit", "PidsLimit")):
        raise ValueError("Docker must support memory, swap and process limits")
    subprocess.run(["docker", "compose", "version"], check=True, capture_output=True, timeout=30)
    config = config.model_copy(deep=True)
    pdf = pdf or bool(config.pdf_container_url)
    lean = lean or bool(config.lean_container_url)
    for profile in [config.llm, *config.overrides.values()]:
        profile.container_url = profile.base_url
    root = Path(config.data_root).expanduser().resolve()
    config.data_root = str(root)
    work = root / "installation"
    work.mkdir(parents=True, exist_ok=True)
    progress_path = work / "macos-stages.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}

    def run(name, argv, *, fingerprint="", always=False):
        checkpoint = {"command": [str(v) for v in argv], "fingerprint": fingerprint}
        if not always and progress.get(name) == checkpoint:
            return
        with (work / f"{name}.log").open("w") as log:
            subprocess.run(checkpoint["command"], check=True, stdout=log, stderr=subprocess.STDOUT)
        progress[name] = checkpoint
        write_json(progress_path, progress)

    context = work / "macos-build"
    _application(context / "application")
    app_hash = hashlib.sha256(b"".join(str(p.relative_to(context)).encode() + p.read_bytes()
        for p in sorted((context / "application").rglob("*")) if p.is_file())).hexdigest()
    base = ("FROM python:3.11-slim-trixie\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends git bubblewrap util-linux python3 ca-certificates curl zstd docker-cli && rm -rf /var/lib/apt/lists/*\n"
            "ENV PIP_DEFAULT_TIMEOUT=120\n"
            # Worker entrypoints deliberately do not import the ML stack. The
            # Native Langflow installs the complete application profile;
            # PDF uses its separately tested dependency lock, as on Linux.
            "RUN pip install 'pydantic==2.13.4' 'jsonschema>=4.22,<5' 'PyYAML>=6,<7' 'httpx>=0.28,<0.29' 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'sympy>=1.13,<1.15'\n"
            "COPY application /opt/nima-app\n"
            "RUN pip install --no-deps /opt/nima-app\n")
    dockerfiles = {
        "macos-worker": base,
        "fast-pdf": ("FROM python:3.11-slim-trixie\nRUN pip install pypdf==6.19.0\n"
                     "COPY application/src/nima_semantica/deep_research_fast_pdf.py /reader.py\n"
                     "USER 65534:65534\nENTRYPOINT [\"python\", \"-I\", \"/reader.py\"]\n"),
    }
    if lean:
        dockerfiles["lean"] = base + (
            "RUN curl --retry 5 --retry-all-errors --connect-timeout 30 -fsSL https://github.com/leanprover/lean4/releases/download/v4.32.1/lean-4.32.1-linux_aarch64.tar.zst -o /tmp/lean.tar.zst && "
            "mkdir /opt/lean && tar --zstd -xf /tmp/lean.tar.zst --strip-components=1 -C /opt/lean && rm /tmp/lean.tar.zst\n"
            "RUN python /opt/nima-app/deploy/configure_lean.py --toolchain /opt/lean --output /opt/lean-project.json && chmod 444 /opt/lean-project.json\n")
    if pdf:
        dockerfiles["pdf"] = base + (
            "RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r /opt/nima-app/deploy/pdf-tested-lock.txt\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0t64 && rm -rf /var/lib/apt/lists/*\n"
            "RUN python -c 'import cv2; import rapidocr'\n")
    for name, contents in dockerfiles.items():
        dockerfile = context / f"{name}.Dockerfile"
        dockerfile.write_text(contents)
        image = f"nima-{name}:{__version__}"
        exists = subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0
        run(f"{name}-image", ["docker", "build", "-f", dockerfile, "-t", image, context],
            fingerprint=hashlib.sha256((app_hash + contents).encode()).hexdigest(), always=not exists)
    symbolic_context = work / "symbolic-build"
    symbolic_context.mkdir(exist_ok=True)
    contents = (assets() / "deploy/sympy.Dockerfile").read_text()
    (symbolic_context / "Dockerfile").write_text(contents)
    run("symbolic-image", ["docker", "build", "-t", "nima-sympy:1.14.0-pilot", symbolic_context],
        fingerprint=hashlib.sha256(contents.encode()).hexdigest(), always=True)
    for name, port in (("symbolic", 7871), ("pdf", 8791), ("lean", 7873)):
        if name == "pdf" and not pdf or name == "lean" and not lean:
            continue
        token = root / ("pdf-service" if name == "pdf" else name) / "token"
        _token(token)
        setattr(config, name + "_url", f"http://127.0.0.1:{port}")
        setattr(config, name + "_container_url", f"http://127.0.0.1:{port}")
        setattr(config, name + "_token_file", str(token))
    if config.embedding:
        config.embedding.container_url = config.embedding.base_url
    config.langflow_url = "http://127.0.0.1:7860"
    for path in (root / "langflow", root / "corpus", root / "pdf-models"):
        path.mkdir(parents=True, exist_ok=True)
    compose = compose_document(config, pdf=pdf, lean=lean)
    path = work / "compose-macos.yaml"
    rendered = yaml.safe_dump(compose, sort_keys=True)
    old_hash = progress.get("compose-sha256")
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != old_hash:
        raise ValueError("Existing macOS Compose definition changed; reconcile it before provisioning")
    path.write_text(rendered)
    progress["compose-sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(progress_path, progress)
    command = ["docker", "compose", "-p", "nima-macos-" + hashlib.sha256(str(root).encode()).hexdigest()[:12], "-f", path]
    for name in ("pdf", "lean"):
        if name not in compose["services"]:
            continue
        run(name + "-isolation", [*command, "run", "--rm", "--no-deps", name,
            "bwrap", "--unshare-all", "--die-with-parent", "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/lib", "/lib", "--proc", "/proc", "--dev", "/dev", "--size", "16777216",
            "--tmpfs", "/tmp", "/usr/bin/python3", "-c", "print('isolation-ready')"], always=True)
    if pdf:
        run("pdf-assets", [*command, "run", "--rm", "--no-deps", "-v", f"{root / 'pdf-models'}:{root / 'pdf-models'}:rw",
            "-e", "HF_HUB_OFFLINE=0", "pdf", "python", "-m", "nima_semantica.pdf.assets", "prepare", root / "pdf-models"],
            fingerprint=app_hash)
        run("pdf-verify", [*command, "run", "--rm", "--no-deps", "pdf", "python", "-m",
            "nima_semantica.pdf.assets", "verify", root / "pdf-models"], always=True)
    run("macos-start", [*command, "up", "-d", "--wait", "--wait-timeout", "300"], always=True)
    environment = root / "langflow-env"
    python = environment / "bin/python"
    run("langflow-venv", [interpreter, "-m", "venv", environment], always=not python.exists())
    installer = [shutil.which("uv"), "pip", "install", "--python", python] if shutil.which("uv") else [python, "-m", "pip", "install"]
    run("langflow-dependencies", [*installer, "-c", context / "application/constraints-tested.txt",
        str(context / "application") + "[langflow,mcp]"], fingerprint=app_hash)
    run("langflow-dependency-check", [python, "-m", "pip", "check"], always=True)
    run("langflow-components", [python, "-c",
        "import lfx_openai.components.openai.openai; import lfx_openai.components.openai.openai_chat_model"], always=True)
    start_langflow(config, python, progress, progress_path)
    from .cli import doctor
    if doctor(config):
        raise ValueError("Managed macOS services failed readiness checks; inspect installation logs and rerun setup")
    write_json(config_path, config.model_dump())
    return config
