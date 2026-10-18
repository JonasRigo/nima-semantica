import json
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from nima_semantica.cli import main, register_corpus
from nima_semantica.deletion import preview_deletion, apply_deletion, recover_deletion
from nima_semantica.installation import Installation, ModelProfile, write_json, list_projects, list_corpora
from nima_semantica.models import Record
from nima_semantica.storage import GraphStore
from nima_semantica.private_service import database_owner


@pytest.fixture
def installation(tmp_path):
    config = Installation(data_root=str(tmp_path / "data"), llm=ModelProfile(model="fixture"))
    with closing(GraphStore(Path(config.data_root) / "corpus")) as store:
        for corpus in ("papers", "other"):
            register_corpus(store, corpus)
            for project in ("one", "two"):
                directory = Path(config.data_root) / "projects" / corpus / project
                workspace = tmp_path / (corpus + " " + project)
                workspace.mkdir()
                (workspace / "paper.tex").write_text("user paper")
                binding = dict(corpus_id=corpus, project_id=project, workspaces=[str(workspace)])
                for kind in ("math", "proof"):
                    database = directory / (kind + ".sqlite3")
                    path = directory / (kind + ".json")
                    write_json(path, dict(corpus_id=corpus, project_id=project, database_path=str(database)))
                    binding[kind + "_config"] = str(path)
                    with sqlite3.connect(database) as db:
                        db.execute("CREATE TABLE notes (text TEXT)")
                        db.execute("INSERT INTO notes VALUES ('private')")
                write_json(directory / "project.json", binding)
                write_json(workspace / ".nima/project.json", binding)
                write_json(workspace / ".mcp.json", {"mcpServers": {"other": {"command": "keep"}, "nima-math": {"args": ["--config", binding["math_config"]]}}})
                store.put(Record(kind="Note", corpus_id=corpus, project_id=project, content={"text": project}))
        artifact = store.artifact(b"shared immutable source")
        for corpus in ("papers", "other"):
            store.put(Record(kind="SharedArtifact", corpus_id=corpus, content={"artifact_id": artifact}))
    return config


def apply(config, project=None):
    plan = preview_deletion(config, "papers", project)
    return apply_deletion(config, plan, plan["preview_hash"])


def test_project_deletion_preserves_siblings_corpus_workspaces_artifacts(installation, tmp_path):
    config = installation
    result = apply(config, "one")
    assert result["status"] == "complete"
    assert [p["project_id"] for p in list_projects(config, "papers")] == ["two"]
    assert len(list_corpora(config)) == 2
    assert len(list_projects(config, "other")) == 2
    assert (tmp_path / "papers one/paper.tex").read_text() == "user paper"
    servers = json.loads((tmp_path / "papers one/.mcp.json").read_text())["mcpServers"]
    assert servers == {"other": {"command": "keep"}}
    assert not (tmp_path / "papers one/.nima/project.json").exists()
    assert list((Path(config.data_root) / "corpus/artifacts").iterdir())
    assert recover_deletion(config, result["recovery_manifest"])["status"] == "complete"
    manifest = json.loads(Path(result["recovery_manifest"]).read_text())
    for path, item in manifest["backups"].items():
        if path.endswith("math.sqlite3"):
            with sqlite3.connect(Path(result["recovery_manifest"]).parent / item["file"]) as db:
                assert db.execute("SELECT text FROM notes").fetchone() == ("private",)


def test_corpus_deletion_cascades_and_preserves_other_corpus(installation):
    result = apply(installation)
    assert result["deleted_projects"] == ["one", "two"]
    assert list_projects(installation, "papers") == []
    assert [c["corpus_id"] for c in list_corpora(installation)] == ["other"]
    with sqlite3.connect(Path(installation.data_root) / "corpus/graph.sqlite3") as db:
        assert not db.execute("SELECT 1 FROM records WHERE corpus_id='papers'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM records WHERE corpus_id='other'").fetchone()[0] > 0


def test_graph_history_is_deleted_only_in_exact_scope(installation):
    from nima_semantica.okf_contracts import OKFDelta, OKFNode
    from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
    from nima_semantica.graph_persistence import scope_key
    with closing(GraphStore(Path(installation.data_root) / "corpus")) as store:
        for project in (None, "one", "two"):
            node = OKFNode(node_id="claim", node_type="claim", properties={"title": "Claim", "text": "fixture"}, corpus_id="papers", project_id=project)
            delta = OKFDelta(delta_id="fixture", corpus_id="papers", project_id=project, base_revision=store.graph_revision("papers", project), ontology_profile="literature_evidence", upsert_nodes=(node,), reason="Fixture")
            commit_okf_delta(store, delta, OKFCommitApproval.for_delta(delta, approval_id="fixture", approved_by="test", rationale="Fixture"))
    apply(installation, "one")
    with sqlite3.connect(Path(installation.data_root) / "corpus/graph.sqlite3") as db:
        for table in ("graph_objects", "graph_versions", "graph_commits", "graph_heads"):
            assert db.execute("SELECT COUNT(*) FROM " + table + " WHERE scope=?", (scope_key("papers", "one"),)).fetchone()[0] == 0
            for project in (None, "two"):
                assert db.execute("SELECT COUNT(*) FROM " + table + " WHERE scope=?", (scope_key("papers", project),)).fetchone()[0] > 0


def test_private_backup_includes_uncheckpointed_wal(installation):
    database = Path(installation.data_root) / "projects/papers/one/math.sqlite3"
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO notes VALUES ('WAL evidence')")
        writer.commit()
        result = apply(installation, "one")
        manifest = Path(result["recovery_manifest"])
        item = json.loads(manifest.read_text())["backups"][str(database)]
        with sqlite3.connect(manifest.parent / item["file"]) as backup:
            assert backup.execute("SELECT text FROM notes ORDER BY rowid").fetchall() == [("private",), ("WAL evidence",)]


def test_stale_preview_and_wrong_hash_do_nothing(installation):
    plan = preview_deletion(installation, "papers", "one")
    with pytest.raises(ValueError, match="hash"):
        apply_deletion(installation, plan, "wrong")
    with __import__('contextlib').closing(GraphStore(Path(installation.data_root) / "corpus")) as store:
        store.put(Record(kind="Note", corpus_id="other", content={"new": True}))
    with pytest.raises(ValueError, match="stale"):
        apply_deletion(installation, plan, plan["preview_hash"])
    assert len(list_projects(installation, "papers")) == 2


@pytest.mark.parametrize("owner", ["corpus", "private"])
def test_active_owners_refused_without_mutation(installation, owner):
    from contextlib import closing
    plan = preview_deletion(installation, "papers", "one")
    guard = closing(GraphStore(Path(installation.data_root) / "corpus")) if owner == "corpus" else database_owner(plan["databases"][0])
    with guard, pytest.raises(ValueError, match="owner|owns"):
        apply_deletion(installation, plan, plan["preview_hash"])
    assert len(list_projects(installation, "papers")) == 2
    assert not list((Path(installation.data_root) / "deletions").glob("*/recovery.json"))


def test_interruption_after_database_commit_can_resume(installation, monkeypatch):
    import nima_semantica.deletion as deletion
    original = deletion.write_json
    raised = False
    def interrupt(path, value):
        nonlocal raised
        if value.get("status") == "database_deleted" and not raised:
            raised = True
            raise OSError("simulated power loss")
        return original(path, value)
    plan = preview_deletion(installation, "papers", "one")
    monkeypatch.setattr(deletion, "write_json", interrupt)
    with pytest.raises(OSError, match="power loss"):
        apply_deletion(installation, plan, plan["preview_hash"])
    journal = Path(installation.data_root) / "deletions" / plan["preview_hash"] / "recovery.json"
    with pytest.raises(ValueError, match="interrupted"):
        with deletion.installation_lifecycle(installation):
            pass
    assert recover_deletion(installation, journal)["status"] == "complete"
    assert len(list_projects(installation, "papers")) == 1


def test_symlink_and_edited_connection_refused(installation, tmp_path):
    path = tmp_path / "papers one/.mcp.json"
    write_json(path, {"mcpServers": {"nima-math": {"args": ["foreign"]}}})
    with pytest.raises(ValueError, match="edited"):
        preview_deletion(installation, "papers", "one")
    path.unlink()
    path.symlink_to(tmp_path / "papers two/.mcp.json")
    with pytest.raises(ValueError, match="symlink"):
        preview_deletion(installation, "papers", "one")


def test_missing_registration_and_surviving_reference_refused(installation):
    directory = Path(installation.data_root) / "projects/papers/one"
    original = (directory / "project.json").read_bytes()
    (directory / "project.json").unlink()
    with pytest.raises(ValueError, match="registration"):
        preview_deletion(installation, "papers")
    (directory / "project.json").write_bytes(original)
    with __import__('contextlib').closing(GraphStore(Path(installation.data_root) / "corpus")) as store:
        rid = store.records("Note", corpus_id="papers", project_id="one")[0][0]
        store.put(Record(kind="Dependent", corpus_id="other", content={"source": rid}))
    with pytest.raises(ValueError, match="reference"):
        preview_deletion(installation, "papers", "one")


def test_cli_preview_apply_and_removed_project_list(installation, tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, installation.model_dump())
    preview = tmp_path / "preview.json"
    command = ["--config", str(config), "project", "delete", "one", "--corpus", "papers", "--preview-file", str(preview)]
    assert main(command) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(list_projects(installation, "papers")) == 2
    assert main(command + ["--apply", plan["preview_hash"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    with pytest.raises(SystemExit) as error:
        main(["project", "list"])
    assert error.value.code == 2


def test_remote_cleanup_rejects_edits_and_resumes_partial_failure(installation, monkeypatch):
    import hashlib
    import nima_semantica.workflow_installation as workflows
    folder = "owned-folder"
    data = {"nodes": [], "edges": []}
    state = {"flows": {"answer": {"id": "owned-flow", "hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}}, "folder_id": folder}
    write_json(Path(installation.data_root) / "projects/papers/one/publication.json", state)
    remote = {
        "/api/v1/projects/owned-folder": {"id": folder, "flows": [{"id": "owned-flow"}]},
        "/api/v1/flows/owned-flow": {"id": "owned-flow", "folder_id": folder, "data": data},
    }
    fail = False
    def handler(request):
        nonlocal fail
        path = request.url.path
        if request.method == "DELETE":
            if path.endswith("owned-folder") and fail:
                fail = False
                return httpx.Response(503)
            remote.pop(path, None)
            if path.endswith("owned-flow"):
                remote["/api/v1/projects/owned-folder"]["flows"] = []
            return httpx.Response(204)
        return httpx.Response(200, json=remote[path]) if path in remote else httpx.Response(404)
    monkeypatch.setattr(workflows, "api", lambda _: httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handler)))
    remote["/api/v1/flows/owned-flow"]["data"] = {"edited": True}
    with pytest.raises(ValueError, match="edited"):
        preview_deletion(installation, "papers", "one")
    remote["/api/v1/flows/owned-flow"]["data"] = data
    plan = preview_deletion(installation, "papers", "one")
    fail = True
    with pytest.raises(httpx.HTTPStatusError):
        apply_deletion(installation, plan, plan["preview_hash"])
    assert len(list_projects(installation, "papers")) == 2
    manifest = Path(installation.data_root) / "deletions" / plan["preview_hash"] / "recovery.json"
    assert recover_deletion(installation, manifest)["status"] == "complete"
    assert remote == {}


def test_unknown_remote_flow_refused(installation, monkeypatch):
    import nima_semantica.workflow_installation as workflows
    state = {"folder_id": "folder", "flows": {}}
    write_json(Path(installation.data_root) / "projects/papers/one/publication.json", state)
    monkeypatch.setattr(workflows, "api", lambda _: httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"flows": [{"id": "unknown"}]}))))
    with pytest.raises(ValueError, match="unrecognized"):
        preview_deletion(installation, "papers", "one")


def test_legacy_workspace_must_be_supplied(installation, tmp_path):
    manifest = Path(installation.data_root) / "projects/papers/one/project.json"
    binding = json.loads(manifest.read_text())
    del binding["workspaces"]
    write_json(manifest, binding)
    with pytest.raises(ValueError, match="--workspace"):
        preview_deletion(installation, "papers", "one")
    assert preview_deletion(installation, "papers", "one", workspaces=[str(tmp_path / "papers one")])["connections"]


def test_damaged_backup_and_workspace_drift_refuse_recovery(installation, monkeypatch, tmp_path):
    import nima_semantica.deletion as deletion
    original = deletion.write_json
    def interrupt(path, value):
        if value.get("status") == "database_deleted":
            raise OSError("interrupted")
        return original(path, value)
    monkeypatch.setattr(deletion, "write_json", interrupt)
    plan = preview_deletion(installation, "papers", "one")
    with pytest.raises(OSError):
        apply_deletion(installation, plan, plan["preview_hash"])
    journal = Path(installation.data_root) / "deletions" / plan["preview_hash"] / "recovery.json"
    monkeypatch.setattr(deletion, "write_json", original)
    connection = tmp_path / "papers one/.mcp.json"
    connection.write_text("edited")
    with pytest.raises(ValueError, match="changed"):
        recover_deletion(installation, journal)
    assert connection.read_text() == "edited"
    (journal.parent / "corpus.sqlite3").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="integrity"):
        recover_deletion(installation, journal)
