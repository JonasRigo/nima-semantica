"""Invocation contracts only. Model construction/configuration belongs to Langflow."""
from typing import Any, Protocol, Literal
import json
import re
from pydantic import Field, StrictInt

from .models import ConfigurationError, StrictModel


class ModelManifest(StrictModel):
    provider: str
    model: str
    revision: str
    parameters: dict[str, Any]
    dimension: int | None = None
    normalization: str | None = None


class Invocation(StrictModel):
    result: dict[str, Any]
    manifest: ModelManifest
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    error: Literal["truncated", "invalid_json"] | None = None
    raw_output: str | None = None
    finish_reason: str | None = None
    # Sanitized provider usage is retained deliberately for audit/reproduction;
    # callers must not pass an entire provider response here.
    provider_usage: dict[str, Any] | None = None


def strict_json_object(content):
    """Reject ambiguous duplicate keys and non-JSON numeric constants."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-JSON numeric constant: " + value)
    result = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError("expected JSON object")
    return result


def model_json_object(content):
    """Decode one complete model envelope; never extract or repair JSON.

    Transport input remains strict_json_object. Only model responses may carry
    a single enclosing Markdown code fence. Nested fences inside JSON strings
    remain data, and all strict duplicate-key/numeric checks still apply.
    """
    if isinstance(content, str):
        match = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n(.*)\r?\n```\s*", content, re.DOTALL)
        if match:
            content = match.group(1)
    return strict_json_object(content)


def completion_envelope(content, manifest, usage, metadata, *, provider_usage=None):
    """Preserve unusable completions as evidence, never repair them into results."""
    reason = metadata.get("done_reason") or metadata.get("finish_reason")
    error = "truncated" if reason in ("length", "max_tokens", "max_output_tokens") else None
    result = {}
    if error is None:
        try:
            result = model_json_object(content)
        except (ValueError, TypeError):
            error = "invalid_json"
            result = {}
    return Invocation(result=result, manifest=manifest, input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"], error=error, raw_output=content,
        finish_reason=reason, provider_usage=provider_usage).model_dump(mode="json")


class ResearchProvider(Protocol):
    def invoke(self, profile: str, component: str, payload: dict, output_schema: dict, max_output_tokens: int) -> Invocation: ...
    def embed(self, profile: str, texts: list[str]) -> tuple[list[list[float]], ModelManifest]: ...
    def embed_query(self, profile: str, text: str) -> tuple[list[list[float]], ModelManifest]: ...


def validate_manifest(manifest: ModelManifest):
    if not manifest.model or not manifest.provider or not manifest.revision:
        raise ConfigurationError("Langflow model identity is incomplete")
    forbidden = ("secret", "api_key", "password", "token", "authorization")
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                # Token-limit/count fields are reproducibility data, not credentials.
                if key not in ("max_tokens", "max_output_tokens", "input_tokens", "output_tokens") and any(word in key.lower() for word in forbidden):
                    raise ConfigurationError("credential-bearing model manifest rejected")
                inspect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item)
    inspect(manifest.parameters)
