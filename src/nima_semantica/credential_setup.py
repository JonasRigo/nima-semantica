"""Resolve shared installation credentials without persisting their secret values."""
from contextlib import contextmanager
from contextvars import ContextVar
import getpass
import os
from pathlib import Path
import re
import sys

from .installation import CredentialSource

_session = ContextVar("nima_credential_setup", default=None)


class CredentialError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.diagnostic = {"code": "workflow.credential_unavailable", "message": message + ". Setup is incomplete."}


@contextmanager
def credential_session(*, interactive, replacements=()):
    token = _session.set({"interactive": interactive, "replace": set(replacements), "resolved": set()})
    try:
        yield
    finally:
        _session.reset(token)


def model_credentials(config):
    profiles = [("tool model", config.llm), *[("override " + name, profile) for name, profile in config.overrides.items()]]
    if config.pdf_recognition:
        profiles.append(("PDF recognition", config.pdf_recognition.model))
    if config.embedding:
        profiles.append(("embeddings", config.embedding))
    return profiles


def credential_inventory(config):
    for _, profile in model_credentials(config):
        if profile.provider != "ollama" and profile.credential:
            credential_name(profile.credential)
    return [{"role": role, "provider": profile.provider, "model": profile.model,
             "credential": profile.credential if profile.provider != "ollama" else "",
             "shared_through": "installation"} for role, profile in model_credentials(config)]


def credential_name(value):
    if not isinstance(value, str) or value != value.strip() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_. -]{0,127}", value) or value.startswith("sk-"):
        raise CredentialError("Enter a Langflow credential name, not a key value or key-file path; select the credential source separately")
    return value


def secret_value(value, name):
    credential_name(name)
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    if not isinstance(value, str) or not value.strip():
        raise CredentialError(f"Credential {name} is empty or unavailable")
    value = value.strip()
    if any(c.isspace() for c in value) or value == name or value.startswith(("/", "~/", "./", "../", "Bearer ")):
        raise CredentialError(f"Credential {name} must contain a key value; use the key-file source to read a file")
    return value


def _source(config, name, interactive):
    selected = config.credential_sources.get(name)
    if interactive:
        default = selected.source if selected else "environment"
        choice = input(f"Source for {name} [langflow (existing credential)/environment/file/prompt (hidden key)] ({default}): ").strip() or default
        if choice not in {"langflow", "environment", "file", "prompt"}:
            raise CredentialError("Choose langflow, environment, file, or prompt")
        reference = selected.reference if selected and selected.source == choice else ""
        if choice == "langflow":
            reference = input(f"Existing Langflow credential name ({reference or name}): ").strip() or reference or name
        elif choice == "environment":
            reference = input(f"Environment variable containing the key ({reference or name}): ").strip() or reference or name
        elif choice == "file":
            reference = input(f"Path to the key file{f' ({reference})' if reference else ''}: ").strip() or reference
        selected = CredentialSource(source=choice, reference=reference)
    elif selected is None:
        selected = CredentialSource(source="environment", reference=name)
    return selected


def _source_value(selected, name, interactive):
    if selected.source == "environment":
        reference = selected.reference or name
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference):
            raise CredentialError(f"Credential {name} needs an environment-variable name, not a key or path")
        value = os.environ.get(reference)
    elif selected.source == "file":
        path = Path(selected.reference).expanduser()
        if not selected.reference or not path.is_file() or path.stat().st_size > 65536:
            raise CredentialError(f"Key file for credential {name} must be a regular file containing one key")
        try:
            value = path.read_text()
        except (OSError, UnicodeError):
            raise CredentialError(f"Cannot read key file for credential {name}") from None
    else:
        if not interactive:
            raise CredentialError(f"Credential {name} needs hidden entry in an interactive terminal, or an environment/key-file source")
        value = getpass.getpass(f"Key value for {name} (hidden; shared through this installation): ")
    value = secret_value(value, name)
    return value


def runtime_credential(config, name):
    """Resolve explicit terminal sources without reading saved secrets from Langflow."""
    selected = getattr(config, "credential_sources", {}).get(name)
    if selected and selected.source in {"file", "environment"}:
        return _source_value(selected, name, False)
    return os.environ.get(name)


def ensure_credentials(client, config):
    state = _session.get()
    interactive = state["interactive"] if state else sys.stdin.isatty()
    groups = {}
    for role, profile in model_credentials(config):
        if profile.provider == "ollama" or not profile.credential:
            if profile.provider in {"openrouter", "openai", "anthropic", "gemini"}:
                raise CredentialError(f"{role} model {profile.model} requires a named installation credential")
            continue
        credential_name(profile.credential)
        groups.setdefault(profile.credential, []).append((role, profile))
    if not groups:
        return {}
    response = client.get("/api/v1/variables/")
    response.raise_for_status()
    rows = response.json()
    existing = {row["name"]: row for row in rows}
    report = {}
    for name, profiles in groups.items():
        roles = ", ".join(f"{role}: {profile.model}" for role, profile in profiles)
        print(f"{roles} → Langflow credential {name} (shared through the installation)", file=sys.stderr)
        row = existing.get(name)
        key = (str(client.base_url) if hasattr(client, "base_url") else "langflow", name)
        action = "replace" if state and name in state["replace"] and key not in state["resolved"] else "keep"
        if interactive and row and not (state and key in state["resolved"]):
            action = input(f"Existing {name}: Keep / Replace / Choose another credential (keep): ").strip().lower() or "keep"
            if action in {"choose", "another", "choose another credential"}:
                other = credential_name(input("Existing Langflow credential name to use for these models: ").strip())
                row = existing.get(other)
                if row is None:
                    raise CredentialError(f"Langflow credential {other} does not exist")
                for _, profile in profiles:
                    profile.credential = other
                name = other
                action = "keep"
        if action not in {"keep", "replace"}:
            raise CredentialError("Choose Keep, Replace, or Choose another credential")
        selected = None
        if row is None or action == "replace":
            selected = _source(config, name, interactive)
            if selected.source == "langflow":
                other = credential_name(selected.reference or name)
                row = existing.get(other)
                if row is None:
                    raise CredentialError(f"Langflow credential {other} does not exist")
                for _, profile in profiles:
                    profile.credential = other
                name, action = other, "keep"
            config.credential_sources[name] = selected
        if row is not None and action == "keep":
            if row.get("type") != "Credential" or row.get("has_value") is False or row.get("is_valid") is False:
                raise CredentialError(f"Credential {name} is empty or unusable; explicitly Replace it or Choose another credential")
            # Missing metadata is unknown. The saved-workflow probe must supply
            # positive runtime evidence; merely listing this row is insufficient.
            presence = "present" if row.get("has_value") is True else "unknown"
        else:
            value = _source_value(selected, name, interactive)
            payload = {"name": name, "value": value, "type": "Credential", "default_fields": []}
            if row is None:
                saved = client.post("/api/v1/variables/", json=payload)
            else:
                payload["id"] = str(row["id"])
                saved = client.patch("/api/v1/variables/" + str(row["id"]), json=payload)
            saved.raise_for_status()
            saved_row = saved.json()
            if saved_row.get("name") != name or saved_row.get("type") != "Credential" or saved_row.get("has_value") is False:
                raise CredentialError(f"Saved credential name/value verification failed for {name}; setup is incomplete")
            presence = "present" if saved_row.get("has_value") is True else "unknown"
        report[name] = {"presence": presence, "ready": False, "verification": "published_workflow_required"}
        print(f"Credential {name}: {presence}; published workflow verification required.", file=sys.stderr)
        if state:
            state["resolved"].add(key)
            state["resolved"].add((key[0], name))
            state["replace"].discard(name)
    return report
