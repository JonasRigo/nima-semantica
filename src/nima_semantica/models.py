"""Canonical scientific records and transport-independent request types."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    def encode_model(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"Cannot serialize {type(item).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=encode_model).encode()


def identity(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Assessment(StrEnum):
    PROPOSED = "proposed"
    PLAUSIBLE = "plausible"
    ATTEMPTED = "attempted"
    NOT_ESTABLISHED = "not_established"
    CONDITIONAL = "conditionally_established"
    REFUTED = "refuted"
    VERIFIED = "verified"


class AcquisitionPolicy(StrictModel):
    enabled: bool = False
    domains: tuple[str, ...] = ()
    max_response_bytes: int = Field(default=10_000_000, gt=0)
    timeout_seconds: int = Field(default=30, gt=0, le=120)
    max_redirects: int = Field(default=3, ge=0, le=10)
    literature_exception: str | None = None


class Record(StrictModel):
    kind: str
    content: dict[str, Any]
    project_id: str | None = None
    corpus_id: str = "default"
    parents: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return identity(self)


class Locator(StrictModel):
    artifact_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page: int | None = None


class Claim(StrictModel):
    statement: str = Field(min_length=1)
    hypotheses: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    source_regions: tuple[str, ...] = ()
    status: Assessment = Assessment.PROPOSED


class ExtractedClaim(Claim):
    # Extraction is attribution, never independent verification or promotion.
    status: Literal[Assessment.PROPOSED] = Assessment.PROPOSED


class AuditOutput(StrictModel):
    claims: tuple[ExtractedClaim, ...]
    gaps: tuple[str, ...]
    definitions: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()


class VerificationResult(StrictModel):
    target_id: str
    protocol: str
    outcome: Literal["verified", "refuted", "inconclusive", "failed"]
    scope: str
    evidence_artifact: str | None = None
    diagnostics: tuple[str, ...] = ()
    # Formal syntax success does not establish correspondence to source prose.
    correspondence_verified: bool = False


class NimaError(Exception):
    """Safe structured application error; messages must not contain source text."""


class ConflictError(NimaError):
    pass


class BudgetExceeded(NimaError):
    pass


class ConfigurationError(NimaError):
    pass
