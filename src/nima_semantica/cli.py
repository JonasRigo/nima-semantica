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


def _langflow_connection_hint(error, config):
    import httpx
    import shlex
    from urllib.parse import urlsplit

    if config is None or not isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return ""
    try:
        failed = urlsplit(str(error.request.url))
        configured = urlsplit(config.langflow_url)
        origin = lambda url: (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        same_service = origin(failed) == origin(configured)
    except (RuntimeError, ValueError):
        return ""
    if not same_service and not failed.path.startswith("/api/v1/mcp/project/"):
        return ""
    # Display the service address without credentials, query strings or project IDs.
    host = failed.hostname or "localhost"
    display_host = f"[{host}]" if ":" in host else host
    port = origin(failed)[2]
    hint = f" Make sure Langflow is running and reachable at {failed.scheme}://{display_host}:{port}."
    if not same_service or host not in {"localhost", "127.0.0.1", "::1"} or failed.scheme != "http":
        return hint
    root = Path(config.data_root).expanduser()
    compose = root / "installation/compose.yaml"
    label = "org.nima.langflow." + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    agent = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    native_python = root / "langflow-env/bin/python"
    if sys.platform == "darwin" and agent.is_file():
        command = ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"]
    elif sys.platform == "linux" and compose.is_file():
        command = ["docker", "compose", "-p", "nima-release", "-f", str(compose), "start", "langflow"]
    else:
        command = ([str(native_python), "-m", "langflow"] if native_python.is_file() else ["langflow"])
        command += ["run", "--host", host, "--port", str(port)]
    return hint + " You can start it with: " + shlex.join(command)


def register_corpus(store, corpus):
    from .corpus_registry import CorpusRegistry
    from .registry_contracts import CorpusDescriptor
    registry = CorpusRegistry(store)
    if registry.corpus(corpus) is None:
        registry.register_corpus(CorpusDescriptor(corpus_id=corpus, name=corpus, corpus_revision="1"))


def setup(args):
    from .credential_setup import credential_session
    with credential_session(interactive=not args.non_interactive,
                            replacements=getattr(args, "replace_credential", ()) or ()):
        return _setup(args)


def save_configuration(path, config):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("refusing a symlink configuration destination")
    value = json.dumps(config.model_dump(), indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        previous = path.read_bytes()
        if previous == value.encode():
            return
        backup = path.with_name(path.name + "." + hashlib.sha256(previous).hexdigest()[:12] + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    write_json(path, config.model_dump())


def _setup(args):
    from .workflow_installation import model_inventory
    path = Path(args.config or configuration_path()).expanduser().resolve()
    if args.from_config:
        config = load_installation(args.from_config)
    elif path.exists():
        config = load_installation(path)
    else:
        if args.non_interactive and not args.model:
            raise ValueError("--model or --from-config is required for non-interactive setup")
        from .model_setup import model_profile, embedding_profile
        profile = model_profile(args)
        root = Path(args.data_root or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nima").expanduser().resolve()
        config = Installation(data_root=str(root), llm=profile, embedding=embedding_profile(args))
        if not args.non_interactive:
            for name, roles in model_inventory().items():
                if roles:
                    override = input(f"{name}: model override (blank uses {profile.model}): ").strip()
                    if override:
                        config.overrides[name] = config.llm.model_copy(update={"model": override})
    inventory = model_inventory()
    from .credential_setup import credential_name, credential_inventory
    from .installation import CredentialSource
    for option, kind in (("credential_file", "file"), ("credential_env", "environment")):
        for item in getattr(args, option, ()) or ():
            name, separator, reference = item.partition("=")
            if not separator or not reference:
                raise ValueError("Credential source must be NAME=PATH or NAME=ENVIRONMENT_VARIABLE")
            config.credential_sources[credential_name(name)] = CredentialSource(source=kind, reference=reference)
    if getattr(args,"pdf_model_profile",None):
        from .installation import PdfRecognitionSettings
        config.pdf_recognition=PdfRecognitionSettings(model=ModelProfile.model_validate_json(Path(args.pdf_model_profile).read_bytes()))
    if args.pdf and config.pdf_recognition is None:
        if args.non_interactive:
            raise ValueError("PDF recognition requires --pdf-model-profile FILE or pdf_recognition in installation JSON")
        from .installation import PdfRecognitionSettings
        from .model_setup import model_profile
        import copy
        selected=copy.copy(args)
        for field in ("provider","model","base_url","credential","model_parameters","context_window","model_timeout"):
            setattr(selected,field,None)
        selected.max_tokens=8192
        print("Select the dedicated PDF vision model.",file=sys.stderr)
        config.pdf_recognition=PdfRecognitionSettings(model=model_profile(selected))
    unknown = set(config.overrides) - set(inventory) - {f"{name}:{role}" for name, roles in inventory.items() for role in roles}
    import re
    unknown = {name for name in unknown if not re.fullmatch(r"[a-z][a-z0-9_-]+\.[a-z][a-z0-9_-]+(?::[^ ]+)?", name)}
    if unknown:
        raise ValueError("Unknown model overrides: " + ", ".join(sorted(unknown)))
    Path(config.data_root).mkdir(parents=True, exist_ok=True)
    store_path(config).mkdir(parents=True, exist_ok=True)
    from .storage import GraphStore
    store = GraphStore(store_path(config)); store.close()
    save_configuration(path, config)
    cli_path = {"status": "skipped"}
    if not args.no_modify_path:
        from .cli_path import install_cli_path
        cli_path = install_cli_path()
        print("NIMA CLI: " + cli_path["message"], file=sys.stderr)
        if cli_path.get("activation_command"):
            print(cli_path["activation_command"], file=sys.stderr)
    if args.minimal: config.reporting_enabled = False
    if args.provision or args.pdf or getattr(args, "verify_workflows", False):
        from .workflow_readiness import invalidate_readiness
        invalidate_readiness(config)
    if args.provision and config.reporting_enabled:
        from .reporting import provision_reporting
        provision_reporting(config)
        from .desktop_dependencies import provision_studio
        provision_studio(config)
        write_json(path, config.model_dump())
    if args.provision:
        from .setup_services import provision
        config = provision(config, path, pdf=args.pdf, lean=args.lean)
    elif args.pdf:
        from .pdf.workflow import install_recognition
        install_recognition(config)
    readiness = {"ready": False, "status": "configuration_only", "verification_command": "nima setup --verify-workflows"}
    if args.provision or args.pdf or getattr(args, "verify_workflows", False):
        if getattr(args, "verify_workflows", False) and config.pdf_recognition and not args.provision and not args.pdf:
            from .pdf.workflow import install_recognition
            install_recognition(config)
        from .workflow_readiness import verify_installation
        try:
            readiness = verify_installation(config)
        finally:
            save_configuration(path, config)
    save_configuration(path, config)
    effective = {name: {role: config.overrides.get(f"{name}:{role}", config.overrides.get(name, config.llm)).model_dump() for role in roles}
                 for name, roles in inventory.items()}
    emit({"configuration": path, "models": effective, "pdf_model": config.pdf_recognition,
          "embedding": config.embedding, "cli_path": cli_path, "credentials": credential_inventory(config),
          "workflow_readiness": readiness, "setup_complete": readiness["ready"],
          "next": "nima project init PROJECT_ID --corpus CORPUS_ID"})


def install_harness(root, config_path, binding, harnesses):
    binding = dict(binding, cli_command=[str(Path(sys.executable)), "-m", "nima_semantica.cli"])
    root = Path(root).resolve()
    if set(harnesses) - {"codex", "claude", "opencode"}:
        raise ValueError("harness must be codex, claude or opencode")
    common = {"NIMA_STORE_ROOT": binding["store_root"], "NIMA_CONFIG": str(Path(config_path).resolve())}
    config = load_installation(config_path)
    if config.symbolic_url:
        common.update(NIMA_SYMBOLIC_WORKER_URL=config.symbolic_url, NIMA_SYMBOLIC_WORKER_TOKEN_FILE=config.symbolic_token_file)
    servers = {
        "nima-math": {"command": str(Path(sys.executable)), "args": ["-m", "nima_semantica.math_mcp", "--config", binding["math_config"]], "env": common},
        "nima-proof": {"command": str(Path(sys.executable)), "args": ["-m", "nima_semantica.proof_mcp", "--config", binding["proof_config"]], "env": common},
    }
    from .config_reconciliation import harness_servers
    for name, server in harness_servers(binding, config_path).items():
        servers[name] = {**server, **({"env": {**common, **server.get("env", {})}} if "command" in server else {})}
    planned = {}
    if "codex" in harnesses:
        import tomllib
        path = root / ".codex/config.toml"
        text = path.read_text() if path.exists() else ""
        existing = tomllib.loads(text).get("mcp_servers", {})
        for name, server in servers.items():
            if name in existing:
                if any(existing[name].get(key) != value for key, value in server.items()):
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
            server = {k: v for k, v in server.items() if k not in {"startup_timeout_sec", "tool_timeout_sec"}}
            if harness == "claude" and "url" in server:
                server["type"] = "http"
            if harness == "opencode":
                server = ({"type": "remote", "url": server["url"], "enabled": True} if "url" in server else
                          {"type": "local", "command": [server["command"], *server["args"]], "environment": server["env"], "enabled": True})
            if key in settings and settings[key] != server:
                raise ValueError(f"Existing {harness} {key} configuration differs; reconcile it first")
            settings[key] = server
        planned[path] = json.dumps(value, indent=2) + "\n"
    from .skill_installation import managed_skill_plan
    skill_writes,conflicts=managed_skill_plan(root,binding,harnesses,assets()/"skills")
    if conflicts: raise ValueError("Installed skills were edited; preserve and reconcile: "+", ".join(conflicts))
    planned.update({target: data.decode() for target,data in skill_writes.items()})
    instruction = root / "NIMA_PROJECT.md"
    text = f"# NIMA project\n\nCorpus: `{binding['corpus_id']}`. Project: `{binding['project_id']}`.\nRead `.nima/project.json` and run `nima project status {binding['project_id']} --corpus {binding['corpus_id']}` to reconnect to canonical state.\nDiscover the configured NIMA MCP tools and read the relevant installed skill before work.\nPrivate graph sessions and project knowledge are separate; exports are inspection snapshots.\n"
    legacy_text = text
    text += "\nIf `nima` is not on PATH, use the `cli_command` argument array in `.nima/project.json`.\n"
    if binding.get("layout"):
        text += "\nResolve human-readable output folders from the `layout.paths` entries in `.nima/project.json`.\n"
    if instruction.exists() and instruction.read_text() not in (text, legacy_text):
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


def projection_limits(config, override=None):
    from .projection_limits import ProjectionLimits
    return ProjectionLimits.model_validate({**config.projection_limits.model_dump(),
        **({"max_tokens": override} if override is not None else {})})


def bind_project_projection(config, corpus, project, result):
    directory = project_path(config, corpus, project)
    for name, pin in (("math", "retrieval_projection_id"), ("proof", "projection_id")):
        path = directory / f"{name}.json"
        value = json.loads(path.read_text())
        value[pin] = result.projection_id
        value["allow_retrieval"] = True
        write_json(path, value)


def refresh_project_projection(config, corpus, project, max_tokens=None):
    """Refresh future session bindings; existing private sessions keep their pins."""
    from .storage import GraphStore
    from .graph_projection import GraphProjectionRequest, GraphProjectionService
    from .models import ConflictError
    try:
        store = GraphStore(store_path(config))
    except ConflictError as exc:
        if str(exc) != "another service owns this corpus": raise
        from .graph_projection import GraphProjectionResult
        data = index_through_owner(config, corpus, project, projection_limits(config, max_tokens))
        if not data.get("index_ready"):
            raise ValueError("Project retrieval projection failed: " + str(data.get("diagnostics")))
        result = GraphProjectionResult.model_validate({k: data[k] for k in GraphProjectionResult.model_fields})
    else:
        try:
            result = GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id=corpus, project_id=project,
                **projection_limits(config, max_tokens).model_dump()))
            if result.status != "completed":
                raise ValueError("Project retrieval projection failed: " + str(result.diagnostics))
        finally:
            store.close()
    bind_project_projection(config, corpus, project, result)


def index_through_owner(config, corpus, project, limits, attempt_id=None):
    from .owner_operations import call_owner
    from .corpus_snapshot import CorpusSnapshot
    from .graph_projection import GraphProjectionService
    from .models import identity, ConflictError
    if project:
        binding = project_binding(config, corpus, project)
    else:
        candidates = [json.loads(p.read_text()) for p in sorted(
            (Path(config.data_root) / "projects" / corpus).glob("*/project.json"))]
        binding = next((b for b in candidates if b.get("mcp_url")), {})
    with CorpusSnapshot(store_path(config), corpus_id=corpus, project_id=project) as store:
        revision = store.graph_revision(corpus, project)
        operation = attempt_id or identity({"stage": "index_recovery", "corpus": corpus, "project": project,
            "limits": limits, "graph_revision": revision, "source_revision": str(store.embedding_revision(corpus))})
    response = call_owner(binding.get("mcp_url"), "Prepare and Index Sources",
        {"mode": "index_rebuild", "operation_id": operation, "sources": [],
         "index_scope": "project" if project else "corpus", "projection_max_tokens": limits.max_tokens,
         "projection_max_regions": limits.max_regions})
    data = response.data
    if (data.get("corpus_id"), data.get("project_id")) != (corpus, project):
        raise ConflictError("index owner response scope mismatch")
    if data.get("index_ready"):
        with CorpusSnapshot(store_path(config), corpus_id=corpus, project_id=project) as store:
            manifest = GraphProjectionService(store).get_current(data["projection_id"], corpus_id=corpus, project_id=project)
            if manifest.model_dump(mode="json") != data.get("manifest"):
                raise ConflictError("index owner returned a different projection manifest")
    return data


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
    from .deletion import installation_lifecycle
    from .credential_setup import credential_session
    with installation_lifecycle(config), credential_session(interactive=not getattr(args, "non_interactive", False)):
        return _project_init(args, config)


def _project_init(args, config):
    from .storage import GraphStore
    corpus, project = identifier(args.corpus), identifier(args.project)
    directory = project_path(config, corpus, project)
    directory.mkdir(parents=True, exist_ok=True)
    from .corpus_snapshot import CorpusSnapshot
    from .corpus_registry import CorpusRegistry
    registered = False
    if (store_path(config) / "graph.sqlite3").exists():
        with CorpusSnapshot(store_path(config),corpus_id=corpus,project_id=project) as reader:
            registered = CorpusRegistry(reader).corpus(corpus) is not None
    if not registered:
        store = GraphStore(store_path(config))
        try: register_corpus(store, corpus)
        finally: store.close()
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
    refresh_project_projection(config, corpus, project, getattr(args, "projection_max_tokens", None))
    if not args.offline:
        from .workflow_installation import publish_flows
        from .workflow_readiness import invalidate_readiness
        invalidate_readiness(config)
        binding["credential_readiness"] = {}
        write_json(manifest, binding)
        try:
            published = publish_flows(config, corpus, project, directory)
            binding["mcp_url"] = published["mcp_url"]
            from .workflow_readiness import verify_installation
            readiness = verify_installation(config)
            binding["credential_readiness"] = readiness["checks"]
        finally:
            save_configuration(args.config or configuration_path(), config)
    write_json(manifest, binding)
    from .project_layout import resolve_layout, scaffold
    local_manifest = Path(args.path) / ".nima/project.json"
    if args.layout or not local_manifest.exists():
        override = json.loads(Path(args.layout).read_text()) if args.layout else None
        binding["layout"] = resolve_layout(binding.get("layout", config.project_layout.model_dump()), override).model_dump()
    binding["workspaces"] = sorted(set(binding.get("workspaces", []) + [str(Path(args.path).resolve())]))
    changed = install_harness(args.path, args.config or configuration_path(), binding, args.harness.split(","))
    write_json(manifest, binding)
    if binding.get("layout"):
        write_json(manifest, binding)
        scaffold(args.path, binding)
    from .credential_setup import credential_inventory
    emit({"project": binding, "installed_files": changed, "toolbox_configured": bool(binding.get("mcp_url")),
          "credential_dependencies": credential_inventory(config),
          "credentials_verified": bool(binding.get("credential_readiness")) and not args.offline,
          "toolbox_verified": False, "verification_command": "nima doctor --project " + binding["project_id"] + " --corpus " + binding["corpus_id"] + " --mcp"})


def ingest(args, config):
    if getattr(args,"ocr_output_dir",None) and args.mode != "full":
        raise ValueError("--ocr-output-dir requires --mode full")
    import copy
    files = args.file if isinstance(args.file, list) else [args.file]
    if len(files) > 16:
        raise ValueError("ingestion accepts at most 16 papers per batch")
    results = []
    for filename in files:
        item = copy.copy(args)
        item.file = filename
        print(f"Ingesting {filename}: mode={args.mode}, index={args.index_mode}", file=sys.stderr, flush=True)
        try:
            result = _ingest_one(item, config)
            results.append({"input": filename, "result": result.model_dump(mode="json")})
            print(f"{filename}: {len(result.data.get('region_ids', []))} regions; "
                  f"index_ready={result.data.get('index_ready', False)}; status={result.status}" +
                  (f"; failed_stage={result.data['failed_stage']}" if result.data.get('failed_stage') else ""),
                  file=sys.stderr, flush=True)
        except Exception as exc:
            if len(files) == 1:
                raise
            results.append({"input": filename, "error": str(exc), "status": "failed"})
    emit(results[0]["result"] if len(files) == 1 else {"papers": results})
    return 0 if all(item.get("result", {}).get("data", {}).get("index_ready") and not
        item.get("result", {}).get("data", {}).get("ocr_export_errors") for item in results) else 1


def project_promote(args, config):
    """Preview an exact corpus-sharing proposal; commit only its approved hash."""
    from .storage import GraphStore
    from .models import identity
    from .evidence_contracts import build_promotion_proposal, commit_promotion, PromotionApproval
    project_binding(config, args.corpus, args.project)
    store = GraphStore(store_path(config))
    try:
        snapshot = store.read_okf_snapshot(corpus_id=args.corpus, project_id=args.project)
        proposal = build_promotion_proposal(snapshot,
            proposal_id="share-" + identity({"snapshot": snapshot, "rationale": args.rationale}),
            target_corpus_id=args.corpus, rationale=args.rationale)
        digest = identity(proposal)
        if not args.approve_proposal:
            emit({"proposal": proposal.model_dump(mode="json"), "proposal_hash": digest,
                  "committed": False, "note": "Sharing preserves preparation quality and scientific status."})
            return 0
        if args.approve_proposal != digest or not args.approved_by:
            raise ValueError("supply the exact current --approve-proposal hash and --approved-by identity")
        result = commit_promotion(store, snapshot, proposal, PromotionApproval(
            proposal_id=proposal.proposal_id, proposal_hash=digest,
            approval_id="approval-" + identity((digest, args.approved_by)),
            approved_by=args.approved_by, rationale=args.rationale))
        output = {"committed": result.status == "committed", "result": result.model_dump(mode="json"),
                  "note": "Sharing does not upgrade preparation quality or scientific status."}
    finally:
        store.close()
    output["projection_ready"] = False
    if not output["committed"]:
        emit(output)
        return 1
    try:
        from .graph_projection import GraphProjectionRequest, GraphProjectionService
        store = GraphStore(store_path(config))
        try:
            projection = GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id=args.corpus,
                **projection_limits(config).model_dump()))
            if projection.status != "completed":
                raise ValueError("Corpus retrieval projection failed: " + str(projection.diagnostics))
        finally:
            store.close()
        for path in sorted((Path(config.data_root) / "projects" / identifier(args.corpus)).glob("*/project.json")):
            refresh_project_projection(config, args.corpus, path.parent.name)
        output["projection_ready"] = True
    except Exception as exc:
        output["projection_error"] = str(exc)
        output["note"] += " Graph commit is retained; retrieval projection refresh needs repair."
    emit(output)
    return 0 if output["projection_ready"] else 1


def _ingest_one(args, config):
    from .source_tools import SourceToolContext, PrepareSourcesRequest, SourceInput, AcquiredSourceInput, prepare_sources, embed_sources, project_sources
    from .storage import GraphStore
    corpus = identifier(args.corpus)
    from urllib.parse import urlsplit
    address = urlsplit(args.file)
    remote = address.scheme in ("http", "https")
    path = Path(args.file).expanduser()
    if not remote and (not path.is_file() or path.is_symlink()):
        raise ValueError("ingest accepts a local regular file, not a directory, URL or symlink")
    if args.project:
        project_binding(config, corpus, identifier(args.project))
    if not remote and path.stat().st_size > 20_000_000:
        raise ValueError("file exceeds the 20 MB preparation limit")
    preparation_mode = getattr(args, "mode", "full")
    input_format = getattr(args, "format", "auto")
    if preparation_mode == "fast" and not config.allow_fast_read:
        raise ValueError("fast reading is disabled in the installation configuration")
    ctx = SourceToolContext(corpus_id=corpus, project_id=args.project,
        source_scope="project" if args.project else "corpus", allow_corpus_writes=not args.project,
        allow_project_writes=bool(args.project), allow_pdf=bool(config.pdf_url) or preparation_mode == "fast", allow_embeddings=args.index_mode == "vector",
        max_pdf_evidence_bytes=config.max_pdf_evidence_bytes,
        projection_limits=projection_limits(config, getattr(args, "projection_max_tokens", None)).model_dump())
    from .ocr import configured_normalizer, export_ingested
    pdf_normalizer=configured_normalizer(config) if config.pdf_url else None
    try:
        store = GraphStore(store_path(config))
    except __import__("nima_semantica.models",fromlist=["ConflictError"]).ConflictError as error:
        if str(error)!="another service owns this corpus" or remote: raise
        from .local_staging import ingest_through_owner
        return export_ingested(args,config,ingest_through_owner(args,config))
    try:
        register_corpus(store, corpus)
        if remote:
            from .literature_acquisition import LiteratureAcquisitionRequest, LiteratureAcquisitionService
            acquired = LiteratureAcquisitionService(store).execute(LiteratureAcquisitionRequest(
                corpus_id=corpus, project_id=args.project, url=args.file, policy=config.acquisition,
                staging_only=True, motivating_gap="Explicit CLI paper ingestion"))
            if acquired.status != "completed":
                raise ValueError("URL acquisition failed under the configured policy; no automatic PDF fallback")
            data = store.read_artifact(acquired.result["artifact_id"])
            acquired_name = acquired.result["name"]
        else:
            data, acquired_name = path.read_bytes(), path.name
        digest = hashlib.sha256(data).hexdigest()
        detected = "pdf" if data.startswith(b"%PDF-") else (
            "html" if b"<html" in data[:8192].lower() or b"<!doctype html" in data[:8192].lower()
            or Path(acquired_name).suffix.lower() in (".html", ".htm") else "other")
        if input_format != "auto" and detected != input_format:
            raise ValueError("input format differs from the declared --format; no implicit conversion")
        if detected == "pdf" and getattr(args,"ocr_output_dir",None):
            from .ocr import destinations
            output=Path(args.ocr_output_dir)/(Path(acquired_name).stem+"-"+digest[:12]+".md")
            destinations(output,force=args.overwrite_ocr,source=path if not remote else None)
        name = acquired_name
        if detected in ("pdf", "html") and Path(name).suffix.lower() not in ("." + detected, ".htm" if detected == "html" else ".pdf"):
            name = "paper-" + digest[:24] + "." + detected
        operation = "cli-ingest-" + hashlib.sha256(json.dumps(
            [corpus, args.project, name, digest, args.index_mode, preparation_mode]).encode()).hexdigest()
        if getattr(args, "retry", False):
            import uuid
            operation += "-retry-" + uuid.uuid4().hex
        # Source bytes remain local to the CLI; never pass a file through model context.
        from .models import Record
        if remote:
            artifact_id, staging_id = acquired.result["artifact_id"], acquired.result["acquisition_id"]
        else:
            with store.joined_transaction():
                artifact_id = store.artifact(data)
                staged = Record(kind="AcquisitionAttempt", corpus_id=corpus, project_id=args.project,
                    content={"status": "completed", "artifact_id": artifact_id, "name": acquired_name,
                             "staging_only": True, "origin": "explicit_local_file"})
                staging_id = store.put(staged)
        source = AcquiredSourceInput(name=name, acquired_name=acquired_name, artifact_id=artifact_id,
            acquisition_id=staging_id)
        req = PrepareSourcesRequest(mode="prepare_index", preparation_mode=preparation_mode,
            operation_id=operation, index_mode=args.index_mode, sources=[source])
        prepared = prepare_sources(store, req, ctx, pdf_normalizer=pdf_normalizer if config.pdf_url else None)
        print(f"Preparation: {prepared.status}; {len(prepared.data.get('region_ids', []))} regions. " +
              (f"Next: {args.index_mode} indexing." if prepared.status == "complete" else "Indexing cannot proceed."),
              file=sys.stderr, flush=True)
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
            refresh_project_projection(config, corpus, project, getattr(args, "projection_max_tokens", None))
    return export_ingested(args,config,result)


def doctor(config, args=None):
    import importlib.util
    import subprocess
    from urllib.parse import urlsplit
    import httpx
    checks = {"python": (3, 11) <= sys.version_info[:2] <= (3, 13),
              "resources": assets().is_dir(), "mcp": importlib.util.find_spec("mcp") is not None}
    commands = ("docker",) if sys.platform == "darwin" else ("docker", "bwrap", "prlimit", "systemctl")
    for command in commands:
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
    from . import __version__
    output = {"nima_version": __version__, "checks": checks, "model_inputs": model_inventory(), "data_root": config.data_root,
          "workers": {"pdf": bool(config.pdf_url), "symbolic": bool(config.symbolic_url), "lean": bool(config.lean_url)}}
    if args is not None and args.mcp:
        from .mcp_diagnostics import scoped_mcp_diagnostics
        binding = project_binding(config, args.corpus, args.project)
        output["mcp_diagnostics"] = scoped_mcp_diagnostics(binding, root=args.path,
            installation_path=args.config or configuration_path())
        checks["project_mcp"] = output["mcp_diagnostics"]["ready"]
    output["service_health"] = dict(checks)
    output["workflow_readiness"] = {"ready": False, "status": "not_checked", "verification_command": "nima doctor --workflows"}
    if args is not None and getattr(args, "workflows", False):
        from .workflow_readiness import verify_installation
        from .credential_setup import credential_session
        try:
            with credential_session(interactive=False):
                output["workflow_readiness"] = verify_installation(config)
        except Exception as error:
            output["workflow_readiness"] = {"ready": False, "error": getattr(error, "diagnostic", {"code": "workflow.check_failed", "message": "Credential or saved-workflow verification failed"})}
    output["setup_complete"] = all(checks.values()) and output["workflow_readiness"]["ready"]
    emit(output)
    return 0 if all(checks.values()) and (not (args is not None and getattr(args, "workflows", False)) or output["workflow_readiness"]["ready"]) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nima", description=__doc__)
    parser.add_argument("--config", help="Installation JSON; defaults to NIMA_CONFIG or the XDG configuration directory")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup")
    install.add_argument("--from-config"); install.add_argument("--non-interactive", action="store_true")
    install.add_argument("--no-modify-path", action="store_true", help="Skip installing the user CLI launcher and editing shell startup files")
    from .installation import MODEL_ENDPOINTS
    install.add_argument("--model"); install.add_argument("--provider", choices=list(MODEL_ENDPOINTS))
    install.add_argument("--base-url"); install.add_argument("--data-root")
    install.add_argument("--credential", help="Shared Langflow credential name, not a key value or file path; '-' means no authentication")
    install.add_argument("--credential-file", action="append", metavar="NAME=PATH", help="Read a named credential's key from this file")
    install.add_argument("--credential-env", action="append", metavar="NAME=VARIABLE", help="Read a named credential's key from this environment variable")
    install.add_argument("--replace-credential", action="append", metavar="NAME", help="Explicitly replace an existing shared credential using the selected source")
    install.add_argument("--verify-workflows", action="store_true", help="Resolve credentials and make small saved-workflow model requests before reporting readiness")
    install.add_argument("--max-tokens", type=int, default=8192)
    install.add_argument('--context-window', type=int, help='Model context tokens, including input and reserved output; Ollama default: 32768')
    install.add_argument('--model-timeout', type=float, help='Provider request timeout in seconds (default: 180)')
    install.add_argument("--model-parameters", help="Provider-specific generation parameters as a JSON object")
    install.add_argument("--embedding-provider", choices=["none", "ollama", "openai", "compatible"])
    install.add_argument("--embedding-model"); install.add_argument("--embedding-base-url")
    install.add_argument("--embedding-credential", help="Independent embedding credential variable; '-' means no authentication")
    install.add_argument("--embedding-revision"); install.add_argument("--embedding-dimension", type=int)
    install.add_argument("--provision", action="store_true", help="Install/start the local stack, including dependency and model downloads")
    install.add_argument("--pdf", action="store_true"); install.add_argument("--lean", action="store_true")
    install.add_argument("--pdf-model-profile", help="JSON ModelProfile for the independent PDF vision model")
    install.add_argument("--minimal", action="store_true", help="Explicitly omit managed Blender and reporting dependencies")
    diagnostic = commands.add_parser("doctor")
    diagnostic.add_argument("--workflows", action="store_true", help="Test saved model workflows, including one formula image for PDF recognition")
    diagnostic.add_argument("--mcp", action="store_true")
    diagnostic.add_argument("--project")
    diagnostic.add_argument("--corpus", default="papers")
    diagnostic.add_argument("--path", default=".")
    harness = commands.add_parser("harness").add_subparsers(dest="action", required=True)
    reconcile = harness.add_parser("reconcile", help="Preview or apply a hashed, recoverable harness transition")
    reconcile.add_argument("--binding", required=True)
    reconcile.add_argument("--path", default=".")
    reconcile.add_argument("--harness", default="codex,claude,opencode")
    reconcile.add_argument("--preview-file", required=True)
    reconcile.add_argument("--apply", help="Exact preview hash; omission writes a preview only")
    reconcile.add_argument("--recovery-directory")
    reconcile.add_argument("--project-manifest", help="Also reconcile the installation's registered project manifest")
    reconcile.add_argument("--owner-uid", type=int, help="Explicit owner of all destination configurations")
    reconcile.add_argument("--owner-gid", type=int, help="Explicit group of all destination configurations")
    recovery = harness.add_parser("recover")
    recovery.add_argument("manifest")
    recovery.add_argument("--mode", choices=["restore", "complete"], required=True)
    maintenance = commands.add_parser("maintenance").add_subparsers(dest="action", required=True)
    migration = maintenance.add_parser("receipt-migration")
    migration_config = migration.add_mutually_exclusive_group(required=True)
    migration_config.add_argument("--proof-config")
    migration_config.add_argument("--math-config")
    migration.add_argument("--session", required=True)
    migration.add_argument("--expected-revision", required=True, type=int)
    migration.add_argument("--directory", required=True)
    reclaim = maintenance.add_parser("reclaim-sqlite")
    reclaim.add_argument("--database", required=True)
    reclaim.add_argument("--backup", required=True)
    private = commands.add_parser("private").add_subparsers(dest="action", required=True)
    service_setup = private.add_parser("configure", help="Prepare managed single-owner private MCP services")
    service_setup.add_argument("--binding", required=True)
    service_setup.add_argument("--directory", required=True)
    service_setup.add_argument("--proof-port", type=int, default=7881)
    service_setup.add_argument("--math-port", type=int, default=7882)
    service_setup.add_argument("--start", action="store_true")
    listing = commands.add_parser("list", help="List registered corpora, or projects in --corpus (local, no service calls)")
    listing.add_argument("--corpus", help="List projects registered in this corpus")
    project = commands.add_parser("project").add_subparsers(dest="action", required=True)
    corpus_commands = commands.add_parser("corpus", help="Manage corpus lifecycle").add_subparsers(dest="action", required=True)
    for parent, is_project in ((project, True), (corpus_commands, False)):
        deletion = parent.add_parser("delete", help="Preview deletion; apply only an exact saved preview hash")
        if is_project:
            deletion.add_argument("project")
            deletion.add_argument("--corpus", required=True)
        else:
            deletion.add_argument("corpus")
            deletion.set_defaults(project=None)
        deletion.add_argument("--preview-file")
        deletion.add_argument("--apply", help="Exact saved preview hash")
        deletion.add_argument("--workspace", action="append", default=[], help="Workspace location for a legacy binding; repeat as needed")
    deletion_recovery = maintenance.add_parser("deletion-recover", help="Complete an interrupted approved deletion")
    deletion_recovery.add_argument("manifest")
    deletion_recovery.add_argument("--mode", choices=["complete"], required=True)
    promotion = project.add_parser("promote", help="Preview or explicitly approve sharing the project's graph with its corpus")
    promotion.add_argument("project"); promotion.add_argument("--corpus", default="papers")
    promotion.add_argument("--rationale", required=True)
    promotion.add_argument("--approve-proposal", help="Exact hash returned by preview; omission performs no graph commit")
    promotion.add_argument("--approved-by", help="Identity of the approving operator")
    for action in ("init", "status", "refresh"):
        sub = project.add_parser(action); sub.add_argument("project"); sub.add_argument("--corpus", default="papers")
        if action == "init":
            sub.add_argument("--path", default="."); sub.add_argument("--harness", default="codex,claude,opencode")
            sub.add_argument("--layout", help="JSON folder-role overrides for this workspace")
            sub.add_argument("--offline", action="store_true", help="Initialize storage and graph MCPs without publishing the Langflow toolbox")
            sub.add_argument("--non-interactive", action="store_true", help="Use configured credential sources without prompting")
        if action in {"init", "refresh"}:
            sub.add_argument("--projection-max-tokens", type=int, help="Explicit projection token limit (1..10000000)")
    configure = project.add_parser("configure", help="Configure optional vocabulary guidance for new investigations")
    configure.add_argument("project"); configure.add_argument("--corpus", default="papers")
    configure.add_argument("--normalized-reasoning", choices=["on", "off"])
    configure.add_argument("--layout", help="JSON layout overrides; preview by default")
    configure.add_argument("--preview-file"); configure.add_argument("--apply", help="Apply an exact saved layout preview hash")
    configure.add_argument("--vocabulary", help="Bind a validated structured vocabulary JSON file")
    configure.add_argument("--path", default=".", help="Update the matching workspace binding if present")
    vocabulary = commands.add_parser("vocabulary").add_subparsers(dest="action", required=True)
    for action in ("context", "read", "header"):
        sub = vocabulary.add_parser(action)
        sub.add_argument("project"); sub.add_argument("--corpus", default="papers")
        if action == "context": sub.add_argument("--query", default="")
        elif action == "read": sub.add_argument("--entry", required=True)
    for action in ("validate", "seal"):
        sub = vocabulary.add_parser(action)
        sub.add_argument("file"); sub.add_argument("--corpus", required=True); sub.add_argument("--project", required=True)
        if action == "seal":
            sub.add_argument("--output", required=True); sub.add_argument("--markdown", required=True)
    indexing = commands.add_parser("index").add_subparsers(dest="action", required=True)
    for action in ("check", "rebuild"):
        sub = indexing.add_parser(action)
        sub.add_argument("corpus"); sub.add_argument("--project")
        sub.add_argument("--projection-max-tokens", type=int)
        if action == "rebuild":
            sub.add_argument("--attempt-id", help="Explicit new indexing attempt; historical failed receipts are retained")
    source = commands.add_parser("ingest")
    source.add_argument("corpus"); source.add_argument("file", nargs="+"); source.add_argument("--project")
    source.add_argument("--mode", choices=["full", "fast"], default="full")
    source.add_argument("--format", choices=["auto", "pdf", "html"], default="auto")
    source.add_argument("--index-mode", choices=["lexical", "vector"], default="lexical")
    source.add_argument("--projection-max-tokens", type=int, help="Explicit projection token limit (1..10000000)")
    source.add_argument("--retry", action="store_true", help="Start a new receipted attempt instead of replaying a saved result; preserves existing evidence")
    source.add_argument("--ocr-output-dir", help="Export the exact full-PDF normalization and evidence into this directory")
    source.add_argument("--overwrite-ocr", action="store_true", help="Replace OCR export files only; corpus evidence remains immutable")
    ocr = commands.add_parser("ocr", help="Save PDF OCR as Markdown with LaTeX and an evidence sidecar; no corpus writes")
    ocr.add_argument("file")
    ocr.add_argument("--output","-o",required=True,help="Destination Markdown file")
    ocr.add_argument("--force",action="store_true",help="Replace existing OCR export files")
    export = commands.add_parser("export").add_subparsers(dest="format", required=True).add_parser("okf")
    export.add_argument("--corpus", required=True); export.add_argument("--project"); export.add_argument("--output", required=True)
    workflow = commands.add_parser("workflow").add_subparsers(dest="action", required=True)
    for action in ("validate", "install"):
        sub = workflow.add_parser(action); sub.add_argument("file")
        if action == "install":
            sub.add_argument("--corpus", required=True); sub.add_argument("--project", required=True)
    from .release_cli import register_commands
    register_commands(commands, project)
    args = parser.parse_args(argv)
    if args.command == "doctor" and args.mcp and not args.project:
        parser.error("doctor --mcp requires --project and its corpus scope")
    config = None
    try:
        if args.command == "setup":
            setup(args); return 0
        config = load_installation(args.config)
        if args.command == "ocr":
            from .ocr import ocr_command
            result=ocr_command(args,config)
            emit(result)
            return 0 if result["status"]=="complete" else 1
        if args.command in {"chat", "math", "report"}:
            from .release_cli import chat_command, calculation_command
            if args.command == "report":
                from .reporting import report_command
                result = report_command(args, config)
            else: result = chat_command(args,config) if args.command == "chat" else calculation_command(args,config)
            if result is not None:
                if args.command == "chat" and not args.json and "answer" in result: print(result["answer"])
                else: emit(result)
            return 0
        if args.command == "list":
            from .installation import list_corpora, list_projects
            emit({"projects": list_projects(config, args.corpus)} if args.corpus is not None else {"corpora": list_corpora(config)})
            return 0
        if args.command in {"project", "corpus"} and args.action == "delete":
            from .deletion import deletion_command
            emit(deletion_command(args, config))
            return 0
        if args.command == "doctor":
            return doctor(config, args)
        if args.command == "harness":
            from .config_reconciliation import reconciliation_preview, apply_reconciliation, recover_reconciliation
            if args.action == "recover":
                emit(recover_reconciliation(args.manifest, args.mode))
            elif args.apply:
                preview = json.loads(Path(args.preview_file).read_text())
                if preview["preview_hash"] != args.apply or not args.recovery_directory:
                    raise ValueError("apply requires the exact saved preview hash and a new recovery directory")
                emit(apply_reconciliation(preview, args.recovery_directory))
            else:
                binding = json.loads(Path(args.binding).read_text())
                preview = reconciliation_preview(args.path, args.config or configuration_path(), binding, args.harness.split(","), project_manifest=args.project_manifest,
                    owner_uid=args.owner_uid, owner_gid=args.owner_gid)
                write_json(args.preview_file, preview)
                emit({"preview_hash": preview["preview_hash"], "scope": preview["scope"],
                      "files": [{k: v for k, v in item.items() if k != "payload_base64"} for item in preview["changes"]],
                      "preview_file": args.preview_file, "skill_conflicts": preview.get("skill_conflicts",[])})
            return 0
        if args.command == "maintenance":
            if args.action == "deletion-recover":
                from .deletion import recover_deletion
                emit(recover_deletion(config, args.manifest))
                return 0
            from .private_service import database_owner
            from .receipt_migration import migrate_proof_receipts, migrate_math_payloads, reclaim_sqlite
            if args.action == "receipt-migration":
                if args.proof_config:
                    from .proof_mcp import ProofConfig as Contract, ProofService as Service
                    function, path = migrate_proof_receipts, args.proof_config
                else:
                    from .math_session_service import MathServiceConfig as Contract, MathSessionService as Service
                    function, path = migrate_math_payloads, args.math_config
                selected = Contract.model_validate_json(Path(path).read_text())
                with database_owner(selected.database_path):
                    emit(function(Service(selected), args.session, args.expected_revision, args.directory))
            else:
                with database_owner(args.database):
                    emit(reclaim_sqlite(args.database, args.backup))
            return 0
        if args.command == "private":
            from .managed_private import configure_private_services, start_private_services
            result = configure_private_services(json.loads(Path(args.binding).read_text()), args.config or configuration_path(),
                args.directory, proof_port=args.proof_port, math_port=args.math_port)
            if args.start:
                start_private_services(result)
            emit(result)
            return 0
        if args.command == "project":
            if args.action == "promote":
                return project_promote(args, config)
            elif args.action == "graph":
                from .release_cli import graph_command
                emit(graph_command(args,config))
            elif args.action == "commit":
                from .project_commit_cli import commit_command
                result = commit_command(args, config)
                emit(result)
                return 0 if result['state'] in {'prepared', 'committed', 'already_committed'} and result.get('studio_status') != 'failed' else 1
            elif args.action == "configure":
                if args.layout:
                    if args.normalized_reasoning is not None: raise ValueError("Configure layout and vocabulary separately")
                    from .release_cli import layout_command
                    emit(layout_command(args, config)); return 0
                if args.normalized_reasoning is None: raise ValueError("Choose --layout or --normalized-reasoning")
                from .vocabulary_installation import configure_project
                emit(configure_project(config, identifier(args.corpus), identifier(args.project),
                    enabled=args.normalized_reasoning == "on", vocabulary_file=args.vocabulary, workspace=args.path))
            elif args.action == "init":
                project_init(args, config)
            elif args.action == "refresh":
                project_binding(config, args.corpus, args.project)
                refresh_project_projection(config, args.corpus, args.project, args.projection_max_tokens)
                emit({"projection_ready": True, "corpus_id": args.corpus, "project_id": args.project})
            else:
                binding = project_binding(config, args.corpus, args.project)
                from .corpus_snapshot import CorpusSnapshot
                store = CorpusSnapshot(store_path(config),corpus_id=args.corpus,project_id=args.project)
                try:
                    emit({**binding, "graph_revision": store.graph_revision(args.corpus, args.project).model_dump(mode="json"),
                          "private_sessions": private_sessions(binding),
                          "inventory": store.inspect_project(corpus_id=args.corpus, project_id=args.project)})
                finally:
                    store.close()
            return 0
        if args.command == "vocabulary":
            from .vocabulary_context import vocabulary_context, load_snapshot, seal_vocabulary, validate_snapshot, render_vocabulary
            from .vocabulary_contracts import VocabularyPolicy, VocabularySnapshot
            from .corpus_snapshot import CorpusSnapshot
            scope = dict(corpus_id=identifier(args.corpus), project_id=identifier(args.project))
            if args.action == "header":
                from .vocabulary_context import vocabulary_header
                with CorpusSnapshot(store_path(config), **scope) as store:
                    emit(vocabulary_header(store, **scope))
                return 0
            if args.action in {"seal", "validate"}:
                path = Path(args.file)
                if path.is_symlink() or path.stat().st_size > 5_000_000:
                    raise ValueError("Vocabulary must be a bounded regular JSON file")
                snapshot = seal_vocabulary(json.loads(path.read_text())) if args.action == "seal" else VocabularySnapshot.model_validate_json(path.read_bytes())
                with CorpusSnapshot(store_path(config), **scope) as store:
                    validate_snapshot(store, snapshot, **scope)
                if args.action == "seal":
                    write_json(args.output, snapshot.model_dump(mode="json"))
                    from .storage import atomic_bytes
                    markdown = Path(args.markdown)
                    if markdown.is_symlink(): raise ValueError("Refusing symlink vocabulary destination")
                    atomic_bytes(markdown, render_vocabulary(snapshot).encode())
                emit({"valid": True, "snapshot_hash": snapshot.content_hash, "entries": len(snapshot.entries)})
            else:
                binding = project_binding(config, args.corpus, args.project)
                policy = VocabularyPolicy.model_validate(binding.get("normalized_reasoning", {}))
                if args.action == "context" and not policy.enabled:
                    emit({"status": "disabled", "enabled": False}); return 0
                if args.action == "context" and not policy.snapshot_artifact_id:
                    emit(vocabulary_context(None, **scope, query=args.query, configured=policy)); return 0
                with CorpusSnapshot(store_path(config), **scope) as store:
                    if args.action == "context":
                        emit(vocabulary_context(store, **scope, query=args.query, configured=policy))
                    else:
                        if not policy.snapshot_artifact_id: raise ValueError("No vocabulary is bound")
                        snapshot = load_snapshot(store, policy, **scope, current=False)
                        entry = next((e for e in snapshot.entries if e.entry_id == args.entry), None)
                        if entry is None: raise ValueError("Vocabulary entry is unavailable")
                        emit(entry.model_dump(mode="json"))
            return 0
        if args.command == "index":
            from contextlib import closing
            from .index_recovery import check_index, rebuild_index
            from .corpus_snapshot import CorpusSnapshot
            from .storage import GraphStore
            scope = dict(corpus_id=identifier(args.corpus), project_id=identifier(args.project) if args.project else None)
            limits = projection_limits(config, args.projection_max_tokens)
            if args.action == "check":
                with CorpusSnapshot(store_path(config), **scope) as store:
                    result = check_index(store, **scope, limits=limits)
            else:
                from .models import ConflictError
                try:
                    selected = GraphStore(store_path(config))
                except ConflictError as exc:
                    if str(exc) != "another service owns this corpus": raise
                    result = index_through_owner(config, args.corpus, args.project, limits, args.attempt_id)
                else:
                    with closing(selected) as store:
                        result = rebuild_index(store, **scope, limits=limits, attempt_id=args.attempt_id)
                if result.get("index_ready"):
                    from .graph_projection import GraphProjectionResult
                    projected = GraphProjectionResult.model_validate({k: result[k] for k in GraphProjectionResult.model_fields})
                    if args.project:
                        bind_project_projection(config, args.corpus, args.project, projected)
                    else:
                        for path in sorted((Path(config.data_root) / "projects" / args.corpus).glob("*/project.json")):
                            refresh_project_projection(config, args.corpus, path.parent.name, args.projection_max_tokens)
            emit(result)
            return 0 if result.get("ready") and (args.action == "check" or result.get("index_ready")) else 1
        if args.command == "ingest":
            return ingest(args, config)
        if args.command == "export":
            from .corpus_snapshot import CorpusSnapshot
            from .graph_service import GraphService
            store = CorpusSnapshot(store_path(config),corpus_id=args.corpus,project_id=args.project)
            try:
                from .ontology_services import OntologyService
                ontology = OntologyService.from_store(store, corpus_id=args.corpus, project_id=args.project)
                GraphService(store, ontology=ontology).export_bundle_directory(args.output, corpus_id=args.corpus, project_id=args.project)
                emit({"directory": str(Path(args.output).resolve()), "revision": store.graph_revision(args.corpus, args.project).model_dump(mode="json")})
            finally:
                store.close()
            return 0
        if args.command == "workflow":
            from .custom_workflows import handle
            return handle(args, config)
    except Exception as exc:
        message = str(exc)
        hint = _langflow_connection_hint(exc, config)
        if hint:
            message = message.rstrip()
            if message and message[-1] not in ".!?":
                message += "."
            message += hint
        print(f"nima: {type(exc).__name__}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
