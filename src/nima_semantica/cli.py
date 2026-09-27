"""NIMA installation, file ingestion and project lifecycle commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from .installation import Installation, ModelProfile, assets, configuration_path, identifier, load_installation, project_binding, project_path, store_path, write_json


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def register_corpus(store, corpus):
    from .corpus_registry import CorpusRegistry
    from .registry_contracts import CorpusDescriptor
    registry = CorpusRegistry(store)
    if registry.corpus(corpus) is None:
        registry.register_corpus(CorpusDescriptor(corpus_id=corpus, name=corpus, corpus_revision="1"))


def setup(args):
    from .workflow_installation import model_inventory
    path = Path(args.config or configuration_path()).expanduser().resolve()
    if args.from_config:
        config = load_installation(args.from_config)
    elif path.exists():
        config = load_installation(path)
    else:
        if args.non_interactive and not args.model:
            raise ValueError("--model or --from-config is required for non-interactive setup")
        model = args.model or input("Tool LLM model identifier: ").strip()
        provider = args.provider or ("openrouter" if args.non_interactive else input("Provider [openrouter/ollama/compatible] (openrouter): ").strip() or "openrouter")
        base_url = args.base_url or ("http://127.0.0.1:11434/v1" if provider == "ollama" else "https://openrouter.ai/api/v1")
        root = Path(args.data_root or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nima").expanduser().resolve()
        config = Installation(data_root=str(root), llm=ModelProfile(provider=provider, model=model, base_url=base_url))
        if not args.non_interactive:
            from .installation import EmbeddingProfile
            embedding = input("Ollama embedding model (blank for lexical retrieval): ").strip()
            if embedding:
                from .setup_services import probe_embedding
                config.embedding = EmbeddingProfile(**probe_embedding(embedding))
            for name, roles in model_inventory().items():
                if roles:
                    override = input(f"{name}: model override (blank uses {model}): ").strip()
                    if override:
                        config.overrides[name] = config.llm.model_copy(update={"model": override})
    inventory = model_inventory()
    unknown = set(config.overrides) - set(inventory) - {f"{name}:{role}" for name, roles in inventory.items() for role in roles}
    import re
    unknown = {name for name in unknown if not re.fullmatch(r"[a-z][a-z0-9_-]+\.[a-z][a-z0-9_-]+(?::[^ ]+)?", name)}
    if unknown:
        raise ValueError("Unknown model overrides: " + ", ".join(sorted(unknown)))
    Path(config.data_root).mkdir(parents=True, exist_ok=True)
    store_path(config).mkdir(parents=True, exist_ok=True)
    from .storage import GraphStore
    store = GraphStore(store_path(config)); store.close()
    if path.exists() and path.read_text() != json.dumps(config.model_dump(), indent=2) + "\n":
        backup = path.with_name(path.name + "." + hashlib.sha256(path.read_bytes()).hexdigest()[:12] + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    write_json(path, config.model_dump())
    if args.provision:
        from .setup_services import provision
        config = provision(config, path, pdf=args.pdf, lean=args.lean)
    effective = {name: {role: config.overrides.get(f"{name}:{role}", config.overrides.get(name, config.llm)).model_dump() for role in roles}
                 for name, roles in inventory.items()}
    emit({"configuration": path, "models": effective, "embedding": config.embedding, "next": "nima project init PROJECT_ID --corpus CORPUS_ID"})


def install_harness(root, config_path, binding, harnesses):
    root = Path(root).resolve()
    if set(harnesses) - {"codex", "claude", "opencode"}:
        raise ValueError("harness must be codex, claude or opencode")
    common = {"NIMA_STORE_ROOT": binding["store_root"]}
    config = load_installation(config_path)
    if config.symbolic_url:
        common.update(NIMA_SYMBOLIC_WORKER_URL=config.symbolic_url, NIMA_SYMBOLIC_WORKER_TOKEN_FILE=config.symbolic_token_file)
    servers = {
        "nima-math": {"command": str(Path(sys.executable)), "args": ["-m", "nima_semantica.math_mcp", "--config", binding["math_config"]], "env": common},
        "nima-proof": {"command": str(Path(sys.executable)), "args": ["-m", "nima_semantica.proof_mcp", "--config", binding["proof_config"]], "env": common},
    }
    if binding.get("mcp_url"):
        servers["nima"] = {"url": binding["mcp_url"]}
    planned = {}
    if "codex" in harnesses:
        import tomllib
        path = root / ".codex/config.toml"
        text = path.read_text() if path.exists() else ""
        existing = tomllib.loads(text).get("mcp_servers", {})
        for name, server in servers.items():
            if name in existing:
                if existing[name] != server:
                    raise ValueError(f"Existing Codex {name} configuration differs; reconcile it first")
                continue
            text += f"\n[mcp_servers.{json.dumps(name)}]\n"
            for key, value in server.items():
                if key == "env":
                    text += "env = { " + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()) + " }\n"
                else:
                    text += f"{key} = {json.dumps(value)}\n"
        tomllib.loads(text)
        planned[path] = text
    for harness, name, table in (("claude", ".mcp.json", "mcpServers"), ("opencode", "opencode.json", "mcp")):
        if harness not in harnesses:
            continue
        path = root / name
        value = json.loads(path.read_text()) if path.exists() else {}
        settings = value.setdefault(table, {})
        for key, server in servers.items():
            server = dict(server)
            if harness == "claude" and "url" in server:
                server["type"] = "http"
            if harness == "opencode":
                server = ({"type": "remote", "url": server["url"], "enabled": True} if "url" in server else
                          {"type": "local", "command": [server["command"], *server["args"]], "environment": server["env"], "enabled": True})
            if key in settings and settings[key] != server:
                raise ValueError(f"Existing {harness} {key} configuration differs; reconcile it first")
            settings[key] = server
        planned[path] = json.dumps(value, indent=2) + "\n"
    skill_roots = [root / ".agents/skills"]
    if "claude" in harnesses:
        skill_roots.append(root / ".claude/skills")
    for destination in skill_roots:
        for source in (assets() / "skills").rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                target = destination / source.relative_to(assets() / "skills")
                data = source.read_text()
                if target.exists() and target.read_text() != data:
                    raise ValueError(f"Installed skill changed: {target}; reconcile before upgrading")
                planned[target] = data
    instruction = root / "NIMA_PROJECT.md"
    text = f"# NIMA project\n\nCorpus: `{binding['corpus_id']}`. Project: `{binding['project_id']}`.\nRead `.nima/project.json` and run `nima project status {binding['project_id']} --corpus {binding['corpus_id']}` to reconnect to canonical state.\nDiscover the configured NIMA MCP tools and read the relevant installed skill before work.\nPrivate graph sessions and project knowledge are separate; exports are inspection snapshots.\n"
    if instruction.exists() and instruction.read_text() != text:
        raise ValueError("Existing NIMA_PROJECT.md differs")
    planned[instruction] = text
    for path, content in planned.items():
        if path.is_symlink():
            raise ValueError(f"Refusing symlink destination {path}")
    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() != content:
            backup = path.with_name(path.name + "." + hashlib.sha256(path.read_bytes()).hexdigest()[:12] + ".bak")
            if not backup.exists():
                shutil.copy2(path, backup)
        path.write_text(content); path.chmod(0o600)
    write_json(root / ".nima/project.json", binding)
    return [str(path) for path in planned]


def refresh_project_projection(config, corpus, project):
    """Refresh future session bindings; existing private sessions keep their pins."""
    from .storage import GraphStore
    from .graph_projection import GraphProjectionRequest, GraphProjectionService
    store = GraphStore(store_path(config))
    try:
        result = GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id=corpus, project_id=project))
        if result.status != "completed":
            raise ValueError("Project retrieval projection failed: " + str(result.diagnostics))
    finally:
        store.close()
    directory = project_path(config, corpus, project)
    for name, key in (("math", "retrieval_projection_id"), ("proof", "projection_id")):
        path = directory / f"{name}.json"
        value = json.loads(path.read_text())
        value[key] = result.projection_id
        value["allow_retrieval"] = True
        write_json(path, value)


def private_sessions(binding):
    """Read session indexes without taking ownership from running MCP processes."""
    import sqlite3
    sessions = {}
    for name in ("math", "proof"):
        config = json.loads(Path(binding[name + "_config"]).read_text())
        path = Path(config["database_path"])
        rows = []
        if path.is_file():
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if name == "math" and "sessions" in tables:
                    rows = [{"session_id": r[0], "revision": r[1]} for r in db.execute(
                        "SELECT session_id, revision FROM sessions WHERE project_id=? ORDER BY session_id", (binding["project_id"],))]
                elif name == "proof" and "proof_sessions" in tables:
                    scope = json.dumps({"corpus_id": binding["corpus_id"], "project_id": binding["project_id"]}, sort_keys=True, separators=(",", ":"))
                    rows = [{"session_id": r[0], "revision": r[1], "lifecycle": r[2]} for r in db.execute(
                        "SELECT id, revision, lifecycle FROM proof_sessions WHERE scope=? ORDER BY id", (scope,))]
        sessions[name] = rows
    return sessions


def project_init(args, config):
    from .storage import GraphStore
    corpus, project = identifier(args.corpus), identifier(args.project)
    directory = project_path(config, corpus, project)
    directory.mkdir(parents=True, exist_ok=True)
    store = GraphStore(store_path(config))
    try:
        register_corpus(store, corpus)
    finally:
        store.close()
    manifest = directory / "project.json"
    binding = json.loads(manifest.read_text()) if manifest.exists() else {
        "schema_version": 1, "corpus_id": corpus, "project_id": project, "store_root": str(store_path(config)),
        "math_config": str(directory / "math.json"), "proof_config": str(directory / "proof.json"),
    }
    math = {"database_path": str(directory / "math.sqlite3"), "project_id": project, "run_id": project,
            "corpus_id": corpus, "allow_execution": bool(config.symbolic_url)}
    proof = {"database_path": str(directory / "proof.sqlite3"), "project_id": project, "corpus_id": corpus,
             "store_root": str(store_path(config)), "allow_execution": bool(config.symbolic_url)}
    for name, value in (("math", math), ("proof", proof)):
        path = directory / f"{name}.json"
        if not path.exists():
            write_json(path, value)
    refresh_project_projection(config, corpus, project)
    if not args.offline:
        from .workflow_installation import publish_flows
        published = publish_flows(config, corpus, project, directory)
        binding["mcp_url"] = published["mcp_url"]
    write_json(manifest, binding)
    changed = install_harness(args.path, args.config or configuration_path(), binding, args.harness.split(","))
    emit({"project": binding, "installed_files": changed, "toolbox_connected": bool(binding.get("mcp_url"))})


def ingest(args, config):
    from .source_tools import SourceToolContext, PrepareSourcesRequest, SourceInput, AcquiredSourceInput, prepare_sources, embed_sources, project_sources
    from .storage import GraphStore
    corpus = identifier(args.corpus)
    path = Path(args.file).expanduser()
    if not path.is_file() or path.is_symlink():
        raise ValueError("ingest accepts a local regular file, not a directory, URL or symlink")
    if args.project:
        project_binding(config, corpus, identifier(args.project))
    if path.stat().st_size > 20_000_000:
        raise ValueError("file exceeds the 20 MB preparation limit")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    operation = "cli-ingest-" + hashlib.sha256(json.dumps([corpus, args.project, path.name, digest, args.index_mode]).encode()).hexdigest()
    ctx = SourceToolContext(corpus_id=corpus, project_id=args.project,
        source_scope="project" if args.project else "corpus", allow_corpus_writes=not args.project,
        allow_project_writes=bool(args.project), allow_pdf=bool(config.pdf_url), allow_embeddings=args.index_mode == "vector")
    def pdf_normalizer(content):
        from .orchestration.langflow.pdf_client import PdfNormalizerClient
        result = PdfNormalizerClient(config.pdf_url, config.pdf_token_file).normalize(content)
        return result["text"], result["diagnostics"]
    store = GraphStore(store_path(config))
    try:
        register_corpus(store, corpus)
        # Source bytes remain local to the CLI; never pass a file through model context.
        from .models import Record
        with store.joined_transaction():
            artifact_id = store.artifact(data)
            staged = Record(kind="AcquisitionAttempt", corpus_id=corpus, project_id=args.project,
                content={"status": "completed", "artifact_id": artifact_id, "name": path.name,
                         "staging_only": True, "origin": "explicit_local_file"})
            staging_id = store.put(staged)
        source = AcquiredSourceInput(name=path.name, acquired_name=path.name, artifact_id=artifact_id, acquisition_id=staging_id)
        req = PrepareSourcesRequest(mode="prepare_index", operation_id=operation, index_mode=args.index_mode, sources=[source])
        prepared = prepare_sources(store, req, ctx, pdf_normalizer=pdf_normalizer if config.pdf_url else None)
        provider = manifest = None
        if args.index_mode == "vector":
            from .setup_services import embedding_provider
            provider, manifest = embedding_provider(config)
        result = project_sources(store, embed_sources(store, prepared, ctx, provider=provider, manifest=manifest), ctx)
    finally:
        store.close()
    if result.data.get("index_ready"):
        projects = [args.project] if args.project else [p.name for p in (Path(config.data_root) / "projects" / corpus).glob("*") if (p / "project.json").is_file()]
        for project in projects:
            refresh_project_projection(config, corpus, project)
    emit(result.model_dump(mode="json"))
    return 0 if result.data.get("index_ready") else 1


def doctor(config):
    import importlib.util
    import subprocess
    from urllib.parse import urlsplit
    import httpx
    checks = {"python": (3, 11) <= sys.version_info[:2] <= (3, 13),
              "resources": assets().is_dir(), "mcp": importlib.util.find_spec("mcp") is not None}
    for command in ("docker", "bwrap", "prlimit", "systemctl"):
        checks[command] = shutil.which(command) is not None
    if urlsplit(config.symbolic_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        try:
            result = subprocess.run(["docker", "info", "--format", "{{json .}}"],
                                    capture_output=True, text=True, timeout=10, check=True)
            capabilities = json.loads(result.stdout)
            checks["docker_resource_limits"] = all(capabilities.get(name) is True
                                                  for name in ("MemoryLimit", "SwapLimit", "PidsLimit"))
        except (OSError, ValueError, subprocess.SubprocessError):
            checks["docker_resource_limits"] = False
    try:
        checks["langflow"] = httpx.get(config.langflow_url.rstrip("/") + "/health", timeout=5).is_success
    except httpx.HTTPError:
        checks["langflow"] = False
    for worker in ("pdf", "symbolic", "lean"):
        url = getattr(config, worker + "_url")
        if not url:
            continue
        try:
            token_file = getattr(config, worker + "_token_file")
            if worker == "pdf":
                from .orchestration.langflow.pdf_client import PdfNormalizerClient
                checks[worker] = PdfNormalizerClient(url, token_file).health()
            elif worker == "symbolic":
                from .symbolic_transport import RemoteSymbolicWorker
                result = RemoteSymbolicWorker(url, Path(token_file).read_text().strip()).run("print(1 + 1)", 10)
                checks[worker] = result.get("outcome") == "executed" and result.get("stdout", "").strip() == "2"
            else:
                response = httpx.get(url.rstrip("/") + "/manifest", timeout=10,
                    headers={"Authorization": "Bearer " + Path(token_file).read_text().strip()}, trust_env=False)
                checks[worker] = response.is_success and bool(response.json())
        except Exception:
            checks[worker] = False
    from .workflow_installation import model_inventory
    emit({"checks": checks, "model_inputs": model_inventory(), "data_root": config.data_root,
          "workers": {"pdf": bool(config.pdf_url), "symbolic": bool(config.symbolic_url), "lean": bool(config.lean_url)}})
    return 0 if all(checks.values()) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nima", description=__doc__)
    parser.add_argument("--config", help="Installation JSON; defaults to NIMA_CONFIG or the XDG configuration directory")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup")
    install.add_argument("--from-config"); install.add_argument("--non-interactive", action="store_true")
    install.add_argument("--model"); install.add_argument("--provider", choices=["openrouter", "ollama", "compatible"])
    install.add_argument("--base-url"); install.add_argument("--data-root")
    install.add_argument("--provision", action="store_true", help="Install/start the local stack, including dependency and model downloads")
    install.add_argument("--pdf", action="store_true"); install.add_argument("--lean", action="store_true")
    commands.add_parser("doctor")
    project = commands.add_parser("project").add_subparsers(dest="action", required=True)
    for action in ("init", "status"):
        sub = project.add_parser(action); sub.add_argument("project"); sub.add_argument("--corpus", default="papers")
        if action == "init":
            sub.add_argument("--path", default="."); sub.add_argument("--harness", default="codex,claude,opencode")
            sub.add_argument("--offline", action="store_true", help="Initialize storage and graph MCPs without publishing the Langflow toolbox")
    source = commands.add_parser("ingest")
    source.add_argument("corpus"); source.add_argument("file"); source.add_argument("--project")
    source.add_argument("--index-mode", choices=["lexical", "vector"], default="lexical")
    export = commands.add_parser("export").add_subparsers(dest="format", required=True).add_parser("okf")
    export.add_argument("--corpus", required=True); export.add_argument("--project"); export.add_argument("--output", required=True)
    workflow = commands.add_parser("workflow").add_subparsers(dest="action", required=True)
    for action in ("validate", "install"):
        sub = workflow.add_parser(action); sub.add_argument("file")
        if action == "install":
            sub.add_argument("--corpus", required=True); sub.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup(args); return 0
        config = load_installation(args.config)
        if args.command == "doctor":
            return doctor(config)
        if args.command == "project":
            if args.action == "init":
                project_init(args, config)
            else:
                binding = project_binding(config, args.corpus, args.project)
                from .storage import GraphStore
                store = GraphStore(store_path(config))
                try:
                    emit({**binding, "graph_revision": store.graph_revision(args.corpus, args.project).model_dump(mode="json"),
                          "private_sessions": private_sessions(binding),
                          "inventory": store.inspect_project(corpus_id=args.corpus, project_id=args.project)})
                finally:
                    store.close()
            return 0
        if args.command == "ingest":
            return ingest(args, config)
        if args.command == "export":
            from .storage import GraphStore
            from .graph_service import GraphService
            store = GraphStore(store_path(config))
            try:
                GraphService(store).export_bundle_directory(args.output, corpus_id=args.corpus, project_id=args.project)
                emit({"directory": str(Path(args.output).resolve()), "revision": store.graph_revision(args.corpus, args.project).model_dump(mode="json")})
            finally:
                store.close()
            return 0
        if args.command == "workflow":
            from .custom_workflows import handle
            return handle(args, config)
    except Exception as exc:
        print(f"nima: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
