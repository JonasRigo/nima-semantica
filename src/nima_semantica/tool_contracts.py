"""Stable result and evidence-record contracts, independent of retired tool names."""
from typing import Any, Literal
from pydantic import Field
from .models import StrictModel


class ToolResult(StrictModel):
    schema_version: Literal[1] = 1
    operation: str
    status: Literal["complete", "partial", "failed", "unavailable"]
    data: dict[str, Any] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    receipt_ids: tuple[str, ...] = ()
    artifacts: dict[str, str] = Field(default_factory=dict)
    note: str = "Completion is operational, not mathematical certification."


class ToolResultRecord(StrictModel):
    corpus_id: str = Field(default="default", pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    operation: str = Field(min_length=1, max_length=80)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    result: ToolResult
