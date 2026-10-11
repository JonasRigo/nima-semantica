"""Advertised operator limits for private mathematical evidence retrieval."""
from pydantic import BaseModel, ConfigDict, Field


class RetrievalLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=4, ge=1, le=32)
    max_results: int = Field(default=8, ge=1, le=64)
    max_hops: int = Field(default=2, ge=0, le=4)
    max_nodes: int = Field(default=64, ge=1, le=256)
    max_edges: int = Field(default=128, ge=1, le=1024)
    max_chars: int = Field(default=12000, ge=1, le=100000)
    owner_timeout_seconds: int = Field(default=1200, ge=30, le=3600,
        description="Wait for the corpus owner's full exact-index validation and embedding attempt.")
