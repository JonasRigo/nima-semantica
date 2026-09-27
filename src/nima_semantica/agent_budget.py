"""Shared, fail-closed accounting for nested Langflow agent capabilities.

This is an execution guard, not a planner or another orchestration engine.
One instance belongs to one public-tool invocation and must be shared by every
child agent. Failed and cancelled attempts are retained, never erased.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any, Awaitable, Callable, Literal

from pydantic import Field, StrictInt

from .models import StrictModel, identity
from .okf_contracts import GraphRevision


class AgentLimits(StrictModel):
    max_depth: StrictInt = Field(default=3, ge=1, le=8)
    model_calls: StrictInt = Field(default=24, ge=1, le=256)
    tool_calls: StrictInt = Field(default=64, ge=1, le=1024)
    tokens: StrictInt = Field(default=100_000, ge=1, le=2_000_000)
    seconds: StrictInt = Field(default=900, ge=1, le=7200)


class AgentAttempt(StrictModel):
    attempt_id: str
    parent_id: str | None
    depth: int
    capability: str
    kind: Literal["model", "tool"]
    input_hash: str
    state: Literal["running", "completed", "failed", "interrupted"]
    reserved_tokens: int = 0
    accounted_tokens: int = 0
    usage_reported: bool = False
    result: Any = None
    error: str | None = None


class BudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class Capability:
    name: str
    invoke: Callable[[dict], Awaitable[Any]]
    effect: Literal["read", "isolated_check", "agent"] = "read"


class AgentBudget:
    def __init__(self, *, run_id: str, revision: GraphRevision, limits: AgentLimits | None = None,
                 clock=time.monotonic):
        self.run_id, self.revision = run_id, revision
        self.limits = limits or AgentLimits()
        self._clock, self._started = clock, clock()
        self._lock = RLock()
        self._attempts: dict[str, AgentAttempt] = {}
        self._tokens = 0
        self._counts = {"model": 0, "tool": 0}

    @property
    def attempts(self):
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._attempts.values())

    @property
    def remaining_seconds(self):
        return max(0.0, self.limits.seconds - (self._clock() - self._started))

    def begin(self, capability, payload, *, kind="tool", parent_id=None, reserve_tokens=0):
        with self._lock:
            if kind not in self._counts:
                raise ValueError("unknown attempt kind")
            if type(reserve_tokens) is not int or reserve_tokens < 0:
                raise ValueError("token reservation must be a nonnegative integer")
            if kind == "model" and reserve_tokens == 0:
                raise ValueError("model calls require a conservative input/output token reservation")
            parent = self._attempts.get(parent_id) if parent_id is not None else None
            if parent_id is not None and (parent is None or parent.state != "running"):
                raise ValueError("child requires a running parent attempt")
            depth = parent.depth + 1 if parent else 1
            ceiling = self.limits.model_calls if kind == "model" else self.limits.tool_calls
            if (not self.remaining_seconds or depth > self.limits.max_depth or
                    self._counts[kind] >= ceiling or self._tokens + reserve_tokens > self.limits.tokens):
                raise BudgetExceeded("shared agent execution budget exhausted")
            attempt_id = identity({"run": self.run_id, "ordinal": len(self._attempts), "capability": capability})
            attempt = AgentAttempt(attempt_id=attempt_id, parent_id=parent_id, depth=depth,
                capability=capability, kind=kind, input_hash=identity(payload), state="running",
                reserved_tokens=reserve_tokens, accounted_tokens=reserve_tokens)
            self._attempts[attempt_id] = attempt
            self._tokens += reserve_tokens
            self._counts[kind] += 1
            return attempt_id

    def finish(self, attempt_id, *, state="completed", result=None, error=None, tokens=None):
        with self._lock:
            attempt = self._attempts[attempt_id]
            if attempt.state != "running":
                raise ValueError("attempt already finished")
            if state not in ("completed", "failed", "interrupted"):
                raise ValueError("invalid terminal attempt state")
            if tokens is not None and (type(tokens) is not int or tokens < 0):
                raise ValueError("invalid reported token count")
            accounted = attempt.reserved_tokens if tokens is None else tokens
            self._tokens += accounted - attempt.accounted_tokens
            exceeded = accounted > attempt.reserved_tokens if attempt.kind == "model" else False
            if exceeded:
                state, error = "failed", "provider exceeded reserved token bound"
                # Fail closed: retain actual usage and stop all further calls.
                self._tokens = max(self._tokens, self.limits.tokens + 1)
            self._attempts[attempt_id] = attempt.model_copy(update={
                "state": state, "result": result, "error": error,
                "accounted_tokens": accounted, "usage_reported": tokens is not None,
            }, deep=True)
            if exceeded:
                raise BudgetExceeded(error)

    async def call(self, name, payload, *, capabilities: tuple[Capability, ...], parent_id=None):
        expected = {"corpus_id": self.revision.corpus_id, "project_id": self.revision.project_id,
                    "graph_revision": self.revision.model_dump(mode="json")}
        if any(key in payload and payload[key] != value for key, value in expected.items()):
            raise ValueError("capability request attempts to change invocation scope or revision")
        allowed = {cap.name: cap for cap in capabilities}
        if len(allowed) != len(capabilities):
            raise ValueError("duplicate capability names")
        cap = allowed.get(name)
        if cap is None or cap.effect not in ("read", "isolated_check", "agent"):
            raise ValueError("capability not authorized for agent execution")
        attempt_id = self.begin(name, payload, parent_id=parent_id)
        try:
            result = await asyncio.wait_for(cap.invoke(payload), timeout=self.remaining_seconds)
        except asyncio.CancelledError:
            self.finish(attempt_id, state="interrupted", error="cancelled")
            raise
        except Exception as error:
            self.finish(attempt_id, state="failed", error=type(error).__name__)
            raise
        self.finish(attempt_id, result=result)
        return result
