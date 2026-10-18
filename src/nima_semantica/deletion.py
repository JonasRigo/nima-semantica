"""Operator-approved, recoverable deletion of registered installation scopes.

Database owners must be stopped. Workspace content and uncertain artifacts are
retained. The durable journal supports completion after any interrupted phase.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import tomllib

from .installation import identifier, project_path, store_path, write_json
from .models import identity
from .graph_persistence import scope_key
from .storage import GraphStore, atomic_bytes


def checked(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Deletion refuses symlink paths: " + str(path))
    return Path(os.path.abspath(path))


def digest(path):
    path = checked(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Expected a regular file: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def installation_lifecycle(config, *, recovering=None):
    root = checked(config.data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = checked(root / "lifecycle.lock")
    with path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another installation lifecycle operation is active") from None
        for journal in (root / "deletions").glob("*/recovery.json"):
            saved = json.loads(checked(journal).read_text())
            if saved.get("status") != "complete" and journal.parent.name != recovering:
                raise ValueError("Complete interrupted deletion first: nima maintenance deletion-recover " + shlex.quote(str(journal)) + " --mode complete")
        yield


def _database(config):
    database = checked(store_path(config) / "graph.sqlite3")
    if not database.is_file():
        raise ValueError("Corpus is not registered")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    if db.execute("PRAGMA user_version").fetchone()[0] != 2:
        db.close()
        raise ValueError("Incompatible corpus store schema")
    return db


def _files(directory):
    result = {}
    if directory.exists():
        for path in sorted(directory.rglob("*")):
            checked(path)
            if path.is_file() and not path.name.endswith((".owner.lock", "-shm")):
                result[str(path)] = digest(path)
    return result


def _workspace_changes(binding, workspaces):
    """Remove only connections whose configuration names this exact project."""
    changes = []
    corpus, project = binding["corpus_id"], binding["project_id"]
    configs = {binding.get(k + "_config") for k in ("math", "proof")} - {None}

    def belongs(server):
        if server.get("url") and server["url"] == binding.get("mcp_url"):
            return True
        args = server.get("args", server.get("command", []))
        if isinstance(args, str):
            return False
        if any(str(value) in configs for value in args):
            return True
        return any(str(service.get("url")) in args and str(service.get("token_file")) in args
                   for service in binding.get("private_services", {}).values())

    for root in workspaces:
        root = checked(root)
        local = root / ".nima/project.json"
        if not local.exists():
            continue
        saved = json.loads(checked(local).read_text())
        if (saved.get("corpus_id"), saved.get("project_id")) != (corpus, project):
            raise ValueError("Workspace binding scope differs: " + str(root))
        for key in ("store_root", "math_config", "proof_config"):
            if saved.get(key) != binding.get(key):
                raise ValueError("Workspace binding belongs to another installation: " + str(root))
        changes.append(dict(path=str(local), before=digest(local), after=None))
        for name, table in ((".mcp.json", "mcpServers"), ("opencode.json", "mcp"), (".codex/config.toml", "mcp_servers")):
            path = root / name
            if not path.exists():
                continue
            text = checked(path).read_text()
            value = tomllib.loads(text) if name.endswith("toml") else json.loads(text)
            servers = value.get(table, {})
            removed = {key for key, server in servers.items() if key in {"nima", "nima-math", "nima-proof"} and belongs(server)}
            for key in {"nima", "nima-math", "nima-proof"} & servers.keys() - removed:
                raise ValueError("Unrecognized or edited NIMA connection: " + str(path) + ": " + key)
            if not removed:
                continue
            if name.endswith("toml"):
                blocks = re.split(r"(?m)(?=^\s*\[[^\n]+\]\s*$)", text)
                kept = []
                for block in blocks:
                    header = re.match(r"\s*\[([^\n]+)\]", block)
                    if header and set(tomllib.loads("[" + header[1] + "]\n").get(table, {})) & removed:
                        continue
                    kept.append(block)
                after = "".join(kept)
                tomllib.loads(after)
            else:
                for key in removed:
                    del servers[key]
                after = json.dumps(value, indent=2) + "\n"
            changes.append(dict(path=str(path), before=digest(path), after=after))
    return changes


def _remote(config, publications, *, allow_missing=False):
    from .workflow_installation import api
    result = []
    if not publications:
        return result
    with api(config) as client:
        for publication in publications:
            folder_id = publication["folder_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(folder_id)) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", str(item["id"])) for item in publication["flows"].values()):
                raise ValueError("Invalid published resource identifier")
            folder = client.get(f"/api/v1/projects/{folder_id}")
            if allow_missing and folder.status_code == 404:
                for item in publication["flows"].values():
                    response = client.get(f"/api/v1/flows/{item['id']}")
                    if response.status_code != 404:
                        response.raise_for_status()
                        raise ValueError("Published workflow survived outside its recorded project")
                continue
            folder.raise_for_status()
            body = folder.json()
            flows = body.get("flows", [])
            if not isinstance(flows, list):
                raise ValueError("Cannot verify all flows in the Langflow project")
            known = {str(item["id"]) for item in publication["flows"].values()}
            if {str(item["id"]) for item in flows} - known:
                raise ValueError("Langflow project contains unrecognized flows")
            for item in publication["flows"].values():
                response = client.get(f"/api/v1/flows/{item['id']}")
                if allow_missing and response.status_code == 404:
                    continue
                response.raise_for_status()
                data = response.json()
                actual = hashlib.sha256(json.dumps(data["data"], sort_keys=True).encode()).hexdigest()
                if actual != item["hash"] or str(data.get("folder_id")) != str(folder_id):
                    raise ValueError("Published workflow was edited or moved; reconcile before deletion")
                result.append(dict(path=f"/api/v1/flows/{item['id']}", payload=data))
            result.append(dict(path=f"/api/v1/projects/{folder_id}", payload=body))
    return result


def preview_deletion(config, corpus, project=None, *, workspaces=()):
    corpus = identifier(corpus)
    project = identifier(project) if project is not None else None
    root = checked(config.data_root)
    with _database(config) as db:
        if not db.execute("SELECT 1 FROM records WHERE kind='CorpusDescriptor' AND corpus_id=?", (corpus,)).fetchone():
            raise ValueError("Corpus is not registered")
        registered = root / "projects" / corpus
        checked(registered)
        ids = {row[0] for row in db.execute("SELECT DISTINCT project_id FROM records WHERE corpus_id=? AND project_id IS NOT NULL UNION SELECT project_id FROM graph_heads WHERE corpus_id=? AND project_id IS NOT NULL", (corpus, corpus))}
        if registered.exists():
            for path in registered.iterdir():
                checked(path)
                if path.is_dir():
                    ids.add(path.name)
        projects, files, publications, connections, databases = [], {}, [], [], []
        selected = [project] if project else sorted(ids)
        for pid in selected:
            directory = checked(project_path(config, corpus, identifier(pid)))
            manifest = directory / "project.json"
            if not manifest.exists():
                # Empty directories left to retain lock inodes are not registrations.
                if pid not in ids or any(p.name for p in directory.glob("*") if not p.name.endswith(".owner.lock")) or db.execute("SELECT 1 FROM records WHERE corpus_id=? AND project_id=? UNION SELECT 1 FROM graph_heads WHERE corpus_id=? AND project_id=?", (corpus, pid, corpus, pid)).fetchone():
                    raise ValueError("Missing project registration; reconcile scope before deletion: " + pid)
                if project:
                    raise ValueError("Project is not registered")
                continue
            binding = json.loads(checked(manifest).read_text())
            if (binding.get("corpus_id"), binding.get("project_id")) != (corpus, pid):
                raise ValueError("Project manifest scope mismatch")
            for kind in ("math", "proof"):
                if kind + "_config" not in binding:
                    continue
                path = checked(binding[kind + "_config"])
                if not path.is_relative_to(directory):
                    raise ValueError("Private configuration escapes managed project directory")
                private = json.loads(path.read_text())
                if (private.get("corpus_id", "papers"), private.get("project_id")) != (corpus, pid):
                    raise ValueError("Private database scope mismatch")
                database = checked(private["database_path"])
                if not database.is_relative_to(directory):
                    raise ValueError("Private database escapes managed project directory")
                databases.append(str(database))
            projects.append(pid)
            files.update(_files(directory))
            publication = directory / "publication.json"
            if publication.exists():
                publications.append(json.loads(publication.read_text()))
            roots = list(binding.get("workspaces", []))
            for supplied in workspaces:
                local = checked(Path(supplied) / ".nima/project.json")
                if local.exists():
                    scope = json.loads(local.read_text())
                    if (scope.get("corpus_id"), scope.get("project_id")) == (corpus, pid):
                        roots.append(str(Path(supplied).absolute()))
            connections.extend(_workspace_changes(binding, sorted(set(roots))))
            if not roots:
                raise ValueError("Legacy project has no recorded workspace; provide --workspace for " + pid)
        selected_folders = [publication["folder_id"] for publication in publications]
        if len(set(selected_folders)) != len(selected_folders):
            raise ValueError("Multiple project registrations share a Langflow project")
        for other in (root / "projects").glob("*/*/publication.json"):
            if str(other) in files:
                continue
            if json.loads(checked(other).read_text()).get("folder_id") in selected_folders:
                raise ValueError("Langflow project is shared by a surviving registration")
        condition, params = ("corpus_id=?", (corpus,)) if project is None else ("corpus_id=? AND project_id=?", (corpus, project))
        record_ids = [r[0] for r in db.execute("SELECT id FROM records WHERE " + condition + " ORDER BY id", params)]
        scopes = [scope_key(corpus, pid) for pid in ([None, *projects] if project is None else projects)]
        # Preserve the integrity of surviving evidence and graph references.
        deleted = set(record_ids)
        for rid, payload in db.execute("SELECT id,payload FROM records"):
            if rid not in deleted and any(token in deleted for token in re.findall(r'"([^"\\]+)"', payload)):
                raise ValueError("Surviving records reference this scope; reconcile dependencies first")
        for scope, payload in db.execute("SELECT scope,payload FROM graph_objects"):
            if scope not in scopes:
                value = json.loads(payload)
                def references_deleted(item):
                    if isinstance(item, dict):
                        if item.get("corpus_id") == corpus and (project is None or item.get("project_id") == project):
                            return True
                        return any(references_deleted(v) for v in item.values())
                    if isinstance(item, list):
                        return any(references_deleted(v) for v in item)
                    return isinstance(item, str) and item in deleted
                if references_deleted(value):
                    raise ValueError("Surviving graph references this scope; reconcile dependencies first")
        revision = db.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0]
        counts = {kind: count for kind, count in db.execute("SELECT kind,COUNT(*) FROM records WHERE " + condition + " GROUP BY kind", params)}
        # Include the complete database snapshot identity, not just its counter:
        # historical writes need not have incremented the counter.
        database_hash = identity(list(db.iterdump()))
    plan = dict(schema_version=1, data_root=str(root), corpus_id=corpus, project_id=project,
                projects=projects, record_ids=record_ids, scopes=scopes, counts=counts,
                revision=revision, database_hash=database_hash, files=files,
                databases=sorted(set(databases)), connections=connections,
                publications=publications, remote=_remote(config, publications),
                workspaces=sorted(set(str(Path(p).absolute()) for p in workspaces)),
                langflow_url=config.langflow_url,
                artifact_policy="Retain immutable artifact bytes, including shared and uncertain references; remove scoped active registrations only.")
    return dict(plan, preview_hash=identity(plan))


def _stop_hint(config, plan):
    commands = []
    compose = checked(Path(config.data_root) / "installation/compose.yaml")
    if compose.is_file():
        commands.append("docker compose -p nima-release -f " + shlex.quote(str(compose)) + " stop langflow")
    if sys.platform == "darwin":
        label = "org.nima.langflow." + hashlib.sha256(str(Path(config.data_root).resolve()).encode()).hexdigest()[:12]
        agent = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if agent.is_file():
            commands.append("launchctl bootout " + shlex.quote(f"gui/{os.getuid()}/{label}"))
    for project in plan["projects"]:
        for kind in ("math", "proof"):
            if sys.platform.startswith("linux"):
                commands.append("systemctl --user stop " + shlex.quote(f"nima-{plan['corpus_id']}-{project}-{kind}.service"))
    return "Stop the affected owners and close stdio harness sessions, then retry." + (" Managed service commands: " + "; ".join(commands) if commands else "")


@contextmanager
def _owners(config, plan):
    with ExitStack() as stack:
        # Keep stable lock inodes even after registration/database removal.
        for name in sorted(plan["databases"]):
            lock = checked(name + ".owner.lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            stream = stack.enter_context(lock.open("a+b"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Private database has an active owner. " + _stop_hint(config, plan)) from None
        try:
            store = GraphStore(store_path(config))
        except Exception as error:
            raise ValueError(str(error) + ". " + _stop_hint(config, plan)) from error
        stack.callback(store.close)
        yield store


def apply_deletion(config, plan, approved_hash):
    unsigned = {k: v for k, v in plan.items() if k != "preview_hash"}
    if identity(unsigned) != approved_hash or plan.get("preview_hash") != approved_hash:
        raise ValueError("Deletion requires the exact saved preview hash")
    if str(checked(config.data_root)) != plan["data_root"] or config.langflow_url != plan["langflow_url"]:
        raise ValueError("Deletion installation differs from preview")
    directory = checked(Path(config.data_root) / "deletions" / approved_hash)
    if (directory / "recovery.json").exists():
        return recover_deletion(config, directory / "recovery.json")
    with installation_lifecycle(config), _owners(config, plan) as store:
        current = preview_deletion(config, plan["corpus_id"], plan["project_id"], workspaces=plan["workspaces"])
        if current["preview_hash"] != approved_hash:
            raise ValueError("Deletion preview is stale; create and approve a new preview")
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        # Backup the SQLite snapshot with its API so WAL pages are included.
        backup = checked(directory / "corpus.sqlite3")
        with sqlite3.connect(backup) as destination:
            store._db.backup(destination)
        backup.chmod(0o600)
        copies = {}
        for source in sorted(set(plan["files"]) | {c["path"] for c in plan["connections"]}):
            target = directory / (str(len(copies)) + ".before")
            if source in plan["databases"]:
                with sqlite3.connect(checked(source).as_uri() + "?mode=ro", uri=True) as origin, sqlite3.connect(target) as destination:
                    origin.backup(destination)
            else:
                atomic_bytes(target, checked(source).read_bytes())
            target.chmod(0o600)
            copies[source] = dict(file=target.name, sha256=digest(target))
        journal = dict(plan=plan, backups=copies, database_backup_sha256=digest(backup), status="prepared", completed_remote=[])
        write_json(directory / "recovery.json", journal)
        return _complete(config, store, directory, journal)


def recover_deletion(config, manifest):
    manifest = checked(manifest)
    journal = json.loads(manifest.read_text())
    plan = journal["plan"]
    unsigned = {k: v for k, v in plan.items() if k != "preview_hash"}
    if identity(unsigned) != plan["preview_hash"] or manifest != checked(Path(config.data_root) / "deletions" / plan["preview_hash"] / "recovery.json"):
        raise ValueError("Recovery scope or preview identity differs")
    if plan["data_root"] != str(checked(config.data_root)) or plan["langflow_url"] != config.langflow_url:
        raise ValueError("Recovery installation differs")
    if journal["status"] == "complete":
        return dict(status="complete", recovery_manifest=str(manifest), preview_hash=plan["preview_hash"])
    with installation_lifecycle(config, recovering=plan["preview_hash"]), _owners(config, plan) as store:
        if digest(manifest.parent / "corpus.sqlite3") != journal["database_backup_sha256"]:
            raise ValueError("Recovery database backup integrity failure")
        for item in journal["backups"].values():
            if Path(item["file"]).name != item["file"] or digest(manifest.parent / item["file"]) != item["sha256"]:
                raise ValueError("Recovery backup integrity failure")
        return _complete(config, store, manifest.parent, journal)


def _complete(config, store, directory, journal):
    plan = journal["plan"]
    manifest = directory / "recovery.json"
    operation = plan["preview_hash"]
    done = store._db.execute("SELECT 1 FROM sqlite_master WHERE name='deletion_operations'").fetchone()
    committed = bool(done and store._db.execute("SELECT 1 FROM deletion_operations WHERE id=?", (operation,)).fetchone())
    if not committed and identity(list(store._db.iterdump())) != plan["database_hash"]:
        raise ValueError("Corpus changed during deletion; preserve backups and reconcile before recovery")
    for path, expected in plan["files"].items():
        if digest(path) not in ({expected, None} if committed else {expected}):
            raise ValueError("Managed project file changed during deletion: " + path)
    for change in plan["connections"]:
        after = hashlib.sha256(change["after"].encode()).hexdigest() if change["after"] is not None else None
        if digest(change["path"]) not in {change["before"], after}:
            raise ValueError("Workspace connection changed during deletion: " + change["path"])
    # Revalidate every remaining remote resource before changing any of them.
    remaining = _remote(config, plan["publications"], allow_missing=True)
    expected = {item["path"]: item["payload"] for item in plan["remote"]}
    for item in remaining:
        if item["path"] not in expected:
            raise ValueError("Unexpected remote resource during recovery")
        for key in ("id", "name", "description", "folder_id", "user_id", "workspace_id", "data"):
            if item["payload"].get(key) != expected[item["path"]].get(key):
                raise ValueError("Remote resource changed during deletion: " + item["path"])
    if remaining:
        from .workflow_installation import api
        with api(config) as client:
            for item in remaining:
                response = client.delete(item["path"])
                if response.status_code != 404:
                    response.raise_for_status()
                journal["completed_remote"].append(item["path"])
                write_json(manifest, journal)
    if not committed:
        with store.transaction():
            store._db.execute("CREATE TABLE IF NOT EXISTS deletion_operations (id TEXT PRIMARY KEY, scope TEXT NOT NULL)")
            for rid in plan["record_ids"]:
                store._db.execute("DELETE FROM edges WHERE source=? OR target=? OR receipt_id=?", (rid, rid, rid))
                store._db.execute("DELETE FROM records WHERE id=?", (rid,))
            for scope in plan["scopes"]:
                for table in ("graph_objects", "graph_versions", "graph_commits", "graph_heads"):
                    store._db.execute("DELETE FROM " + table + " WHERE scope=?", (scope,))
            if plan["project_id"] is None:
                store._db.execute("DELETE FROM generations WHERE corpus_id=?", (plan["corpus_id"],))
            store._db.execute("UPDATE state SET value=value+1 WHERE key='revision'")
            store._db.execute("INSERT INTO deletion_operations VALUES (?,?)", (operation, json.dumps([plan["corpus_id"], plan["project_id"]])))
    journal["status"] = "database_deleted"
    write_json(manifest, journal)
    for change in plan["connections"]:
        path = checked(change["path"])
        after = hashlib.sha256(change["after"].encode()).hexdigest() if change["after"] is not None else None
        if digest(path) not in {change["before"], after}:
            raise ValueError("Workspace connection changed during deletion: " + str(path))
        if change["after"] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_bytes(path, change["after"].encode())
            path.chmod(0o600)
    for path, expected_hash in plan["files"].items():
        if digest(path) not in {expected_hash, None}:
            raise ValueError("Managed file changed during deletion: " + path)
        checked(path).unlink(missing_ok=True)
    # WAL/SHM files belong to the deleted private databases, never to the shared store.
    for database in plan["databases"]:
        for suffix in ("-wal", "-shm"):
            checked(database + suffix).unlink(missing_ok=True)
    journal["status"] = "complete"
    write_json(manifest, journal)
    return dict(status="complete", corpus_id=plan["corpus_id"], project_id=plan["project_id"],
                deleted_projects=plan["projects"], recovery_manifest=str(manifest), preview_hash=operation,
                artifact_policy=plan["artifact_policy"])


def deletion_command(args, config):
    if args.apply:
        if not args.preview_file:
            raise ValueError("--apply requires --preview-file")
        plan = json.loads(checked(args.preview_file).read_text())
        if (plan["corpus_id"], plan["project_id"]) != (args.corpus, args.project):
            raise ValueError("Command scope differs from saved preview")
        return apply_deletion(config, plan, args.apply)
    plan = preview_deletion(config, args.corpus, args.project, workspaces=args.workspace)
    if args.preview_file:
        write_json(args.preview_file, plan)
    return plan
