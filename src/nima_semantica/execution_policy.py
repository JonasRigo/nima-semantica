"""Operator-owned Lean phase, job, queue, transport and artifact budgets."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LeanExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    compilation_seconds: float = Field(default=1200, gt=0, le=7200, allow_inf_nan=False)
    replay_seconds: float = Field(default=1200, gt=0, le=7200, allow_inf_nan=False)
    job_seconds: float = Field(default=2460, gt=0, le=15000, allow_inf_nan=False)
    queue_seconds: float = Field(default=300, gt=0, le=7200, allow_inf_nan=False)
    transport_allowance_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    transport_seconds: float = Field(default=2790, gt=0, le=22500, allow_inf_nan=False)
    memory_mb: int = Field(default=32768, ge=512, le=65536)
    heartbeat_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    output_bytes: int = Field(default=1048576, ge=1024, le=268435456)
    artifact_bytes: int = Field(default=16777216, ge=1024, le=536870912)
    transport_artifact_bytes: int = Field(default=41943040, ge=1024, le=1073741824)

    @model_validator(mode="after")
    def coherent_deadlines(self):
        if self.job_seconds < self.compilation_seconds + self.replay_seconds:
            raise ValueError("whole job must include separate compilation and replay allowances")
        if self.transport_seconds < self.queue_seconds + self.job_seconds + self.transport_allowance_seconds:
            raise ValueError("transport must cover queue, whole job and delivery allowance")
        return self

    @classmethod
    def legacy(cls, timeout, memory_mb, output_bytes, artifact_bytes):
        return cls(compilation_seconds=timeout, replay_seconds=timeout, job_seconds=2 * timeout + 30,
            queue_seconds=30, transport_seconds=2 * timeout + 90, memory_mb=memory_mb,
            output_bytes=output_bytes, artifact_bytes=artifact_bytes)
