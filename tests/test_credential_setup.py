"""Credential source selection, positive presence evidence and explicit replacement."""
import json

import httpx
import pytest

from nima_semantica import credential_setup as credentials
from nima_semantica.installation import CredentialSource, Installation, ModelProfile, PdfRecognitionSettings


def configured(tmp_path):
    return Installation(data_root=str(tmp_path), llm=ModelProfile(model="main", credential="MAIN_KEY"),
        pdf_recognition=PdfRecognitionSettings(model=ModelProfile(model="vision", credential="MAIN_KEY")))


def client(rows=(), *, saved_name=None):
    entries = list(rows)
    writes = []
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=entries)
        value = json.loads(request.content)
        if request.method == "PATCH":
            assert value["id"] == request.url.path.rsplit("/", 1)[1]
        writes.append((request.method, request.url.path, value))
        row = {"name": saved_name or value["name"], "type": "Credential", "id": "saved", "has_value": True}
        entries[:] = [row]
        return httpx.Response(200, json=row)
    return httpx.Client(base_url="http://langflow.test", transport=httpx.MockTransport(handle)), writes


def test_key_file_contents_are_saved_but_never_serialized_or_printed(tmp_path, capsys):
    config = configured(tmp_path)
    path = tmp_path / "provider.key"
    path.write_text("fixture-secret-from-file\n")
    config.credential_sources["MAIN_KEY"] = CredentialSource(source="file", reference=str(path))
    api, writes = client()
    with api, credentials.credential_session(interactive=False):
        report = credentials.ensure_credentials(api, config)
    assert writes[0][2]["value"] == "fixture-secret-from-file"
    assert len(writes) == 1  # Main/PDF share one installation credential.
    assert report["MAIN_KEY"]["ready"] is False
    assert "fixture-secret" not in config.model_dump_json() + capsys.readouterr().err
    assert "MAIN_KEY" in config.model_dump_json()


@pytest.mark.parametrize("metadata", [{}, {"has_value": None}, {"has_value": "true"}])
def test_missing_or_nonboolean_metadata_requires_runtime_verification(tmp_path, metadata, capsys):
    api, writes = client([{"name": "MAIN_KEY", "type": "Credential", **metadata}])
    with api, credentials.credential_session(interactive=False):
        report = credentials.ensure_credentials(api, configured(tmp_path))
    assert writes == []
    assert report["MAIN_KEY"] == {"presence": "unknown", "ready": False, "verification": "published_workflow_required"}
    assert "unknown" in capsys.readouterr().err


@pytest.mark.parametrize("row", [
    {"has_value": False, "type": "Credential"},
    {"has_value": True, "type": "Generic"},
    {"has_value": True, "type": "Credential", "is_valid": False},
])
def test_empty_or_unusable_saved_entry_is_not_silently_overwritten(tmp_path, monkeypatch, row):
    monkeypatch.setenv("MAIN_KEY", "new-valid-fixture-key")
    api, writes = client([{"name": "MAIN_KEY", "id": "old", **row}])
    with api, credentials.credential_session(interactive=False), pytest.raises(credentials.CredentialError, match="Replace"):
        credentials.ensure_credentials(api, configured(tmp_path))
    assert writes == []


def test_keep_does_not_replace_saved_key_with_environment_value(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_KEY", "different-fixture-key")
    api, writes = client([{"name": "MAIN_KEY", "type": "Credential", "id": "old", "has_value": True}])
    with api, credentials.credential_session(interactive=False):
        report = credentials.ensure_credentials(api, configured(tmp_path))
    assert writes == [] and report["MAIN_KEY"]["presence"] == "present"
    assert report["MAIN_KEY"]["ready"] is False  # Metadata alone is never readiness.


def test_explicit_replace_updates_existing_id_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_KEY", "replacement-fixture-key")
    config = configured(tmp_path)
    api, writes = client([{"name": "MAIN_KEY", "type": "Credential", "id": "old", "has_value": False}])
    with api, credentials.credential_session(interactive=False, replacements=["MAIN_KEY"]):
        credentials.ensure_credentials(api, config)
        credentials.ensure_credentials(api, config)
    assert len(writes) == 1 and writes[0][:2] == ("PATCH", "/api/v1/variables/old")
    assert writes[0][2]["value"] == "replacement-fixture-key"


def test_choose_another_updates_both_models_to_selected_existing_name(tmp_path, monkeypatch):
    config = configured(tmp_path)
    api, writes = client([{"name": name, "type": "Credential", "id": name, "has_value": True} for name in ("MAIN_KEY", "OTHER_KEY")])
    answers = iter(["choose", "OTHER_KEY"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    with api, credentials.credential_session(interactive=True):
        report = credentials.ensure_credentials(api, config)
        credentials.ensure_credentials(api, config)  # Rebinding must not prompt twice.
    assert config.llm.credential == config.pdf_recognition.model.credential == "OTHER_KEY"
    assert set(report) == {"OTHER_KEY"} and not writes


def test_hidden_entry_is_not_saved_in_configuration(tmp_path, monkeypatch):
    config = configured(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "prompt")
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "fixture-hidden-key")
    api, writes = client()
    with api, credentials.credential_session(interactive=True):
        credentials.ensure_credentials(api, config)
    assert writes[0][2]["value"] == "fixture-hidden-key"
    assert config.credential_sources["MAIN_KEY"].source == "prompt"
    assert "fixture-hidden-key" not in config.model_dump_json()


def test_existing_langflow_source_rebinds_missing_name_without_writing(tmp_path, monkeypatch):
    config = configured(tmp_path)
    api, writes = client([{"name": "Existing key", "type": "Credential", "has_value": True}])
    answers = iter(["langflow", "Existing key"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    with api, credentials.credential_session(interactive=True):
        report = credentials.ensure_credentials(api, config)
    assert config.llm.credential == config.pdf_recognition.model.credential == "Existing key"
    assert set(report) == {"Existing key"} and not writes


@pytest.mark.parametrize("value", [None, "", "  ", "/tmp/provider.key", "MAIN_KEY"])
def test_absent_empty_or_path_value_does_not_reach_langflow(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MAIN_KEY", raising=False)
    else:
        monkeypatch.setenv("MAIN_KEY", value)
    api, writes = client()
    with api, credentials.credential_session(interactive=False), pytest.raises(credentials.CredentialError):
        credentials.ensure_credentials(api, configured(tmp_path))
    assert writes == []


def test_saved_credential_name_mismatch_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_KEY", "fixture-secret")
    api, _ = client(saved_name="UNEXPECTED_KEY")
    with api, credentials.credential_session(interactive=False), pytest.raises(credentials.CredentialError, match="name/value verification"):
        credentials.ensure_credentials(api, configured(tmp_path))


def test_local_models_need_no_cloud_credentials_including_pdf(tmp_path):
    model = ModelProfile(provider="ollama", model="local")
    config = Installation(data_root=str(tmp_path), llm=model, pdf_recognition=PdfRecognitionSettings(model=model))
    api = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("No credential API is needed")))
    with api, credentials.credential_session(interactive=False):
        assert credentials.ensure_credentials(api, config) == {}
    assert all(row["credential"] == "" for row in credentials.credential_inventory(config))


def test_native_embedding_reads_selected_file_contents(tmp_path, monkeypatch):
    from nima_semantica.installation import EmbeddingProfile
    from nima_semantica.setup_services import embedding_provider
    config = configured(tmp_path)
    config.embedding = EmbeddingProfile(provider="compatible", model="embed", revision="pin", dimension=2, credential="EMBED_KEY")
    path = tmp_path / "embedding.key"
    path.write_text("fixture-file-embedding-key\n")
    config.credential_sources["EMBED_KEY"] = CredentialSource(source="file", reference=str(path))
    original = httpx.Client
    def handle(request):
        assert request.headers["authorization"] == "Bearer fixture-file-embedding-key"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)))
    provider, _ = embedding_provider(config)
    assert provider.embed_query("configured", "query")[0] == [[1.0, 0.0]]
