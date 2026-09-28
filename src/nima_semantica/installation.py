"""Installation configuration, scoped project bindings and portable resources."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import AcquisitionPolicy
from .paper_discovery import PaperDiscoveryPolicy


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


MODEL_ENDPOINTS = {"openrouter": "https://openrouter.ai/api/v1", "openai": "https://api.openai.com/v1",
    "compatible": "", "ollama": "http://127.0.0.1:11434", "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com"}


class ModelProfile(Config):
    provider: str = "openrouter"
    model: str
    base_url: str = "https://openrouter.ai/api/v1"
    container_url: str = ""
    credential: str = "NIMA_MODEL_API_KEY"
    max_tokens: int = Field(default=8192, ge=1)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            provider = value.get("provider", "openrouter")
            if provider not in MODEL_ENDPOINTS:
                raise ValueError("Unsupported provider; use compatible for an OpenAI-compatible endpoint")
            value.setdefault("base_url", MODEL_ENDPOINTS[provider])
            value.setdefault("credential", "" if provider == "ollama" else "NIMA_MODEL_API_KEY")
            if not value["base_url"]:
                raise ValueError("A compatible provider requires an explicit base_url")
        return value


class EmbeddingProfile(Config):
    provider: str = "ollama"
    model: str
    base_url: str = "http://127.0.0.1:11434"
    container_url: str = ""
    revision: str
    dimension: int = Field(ge=1)
    credential: str = "NIMA_EMBEDDING_API_KEY"


class Installation(Config):
    schema_version: int = 1
    data_root: str
    llm: ModelProfile
    embedding: EmbeddingProfile | None = None
    overrides: dict[str, ModelProfile] = Field(default_factory=dict)
    discovery: PaperDiscoveryPolicy = Field(default_factory=lambda: PaperDiscoveryPolicy(discovery_providers=("arxiv", "openalex", "crossref"), max_discoveries=8))
    acquisition: AcquisitionPolicy = Field(default_factory=lambda: AcquisitionPolicy(enabled=True, domains=("arxiv.org", "export.arxiv.org"), max_response_bytes=20_000_000))
    allow_fast_read: bool = True
    allow_lean_search: bool = False
    langflow_url: str = "http://127.0.0.1:7860"
    langflow_api_key_env: str = "NIMA_LANGFLOW_API_KEY"
    pdf_url: str = ""
    pdf_container_url: str = ""
    pdf_token_file: str = ""
    symbolic_url: str = ""
    symbolic_container_url: str = ""
    symbolic_token_file: str = ""
    lean_url: str = ""
    lean_container_url: str = ""
    lean_token_file: str = ""


def configuration_path():
    return Path(os.environ.get("NIMA_CONFIG", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nima" / "installation.json"))


def load_installation(path=None):
    return Installation.model_validate_json(Path(path or configuration_path()).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("refusing a symlink configuration destination")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".nima-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def assets():
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "skills").is_dir():
        return checkout
    installed = Path(sys.prefix) / "share" / "nima"
    if not installed.is_dir():
        raise ValueError("NIMA resource files are missing; reinstall the complete wheel")
    return installed


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or ".." in value:
        raise ValueError("IDs must contain letters, digits, dots, hyphens or underscores without '..'")
    return value


def store_path(installation):
    return Path(installation.data_root) / "corpus"


def project_path(installation, corpus, project):
    return Path(installation.data_root) / "projects" / identifier(corpus) / identifier(project)


def project_binding(installation, corpus, project):
    path = project_path(installation, corpus, project) / "project.json"
    data = json.loads(path.read_text())
    if data["corpus_id"] != corpus or data["project_id"] != project:
        raise ValueError("project manifest scope mismatch")
    return data
