"""Bounded symbolic execution, independent of prompts, research, and Langflow.

Generated programs are untrusted. Docker is the isolation boundary, not Python
syntax filtering. Mathematical checking runs a separate, fixed program and never
accepts a generated program's claim of verification.
"""

from __future__ import annotations

import hashlib
import json
import selectors
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from .models import NimaError, StrictModel


class CalculationTask(StrictModel):
    task: str = Field(default="Compute an antiderivative of x**2.", min_length=1, max_length=12000)
    backend: Literal["sympy", "lean"] = "sympy"
    operation: Literal["integrate", "differentiate", "series", "simplify", "solve", "calculate"] = (
        "integrate"
    )
    expression: str = Field(default="x**2", min_length=1, max_length=4000)
    variable: str = Field(default="x", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    assumptions: str = Field(default="x is real", max_length=6000)
    symbol_domain: Literal["real", "positive", "complex"] = "real"
    expansion_point: int = Field(default=0, ge=-1000, le=1000)
    expansion_order: int = Field(default=5, ge=1, le=20)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    corpus_id: str = Field(default="calculation_preview", pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str = Field(default="calculation_preview", pattern=r"^[A-Za-z0-9_.-]+$")
    context_region_ids: tuple[str, ...] = Field(default=(), max_length=8)


class SymbolicExecutionInput(StrictModel):
    source: str = Field(min_length=1, max_length=32000)
    timeout_seconds: int = Field(default=60, ge=1, le=120)


class SymbolicCheckInput(StrictModel):
    task: CalculationTask
    candidate: str = Field(min_length=1, max_length=8000)


class SymbolicWorker:
    """Run code in a disposable image resolved to its immutable local image ID.

    No image pulls or host Python fallback. The image is an administrator-owned
    setting and cannot be overridden through requests. Output and wall time are
    capped while streaming, including failure paths.
    """

    image = "nima-sympy:1.14.0-pilot"
    output_limit = 1024 * 1024

    def run(self, source: str, timeout: int = 60) -> dict:
        try:
            resolved = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", self.image],
                capture_output=True,
                timeout=10,
                check=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise NimaError("configured symbolic worker image/runtime is unavailable") from None
        if not resolved.startswith("sha256:") or len(resolved) != 71:
            raise NimaError("symbolic worker image identity is invalid")
        name = "nima-calculation-" + uuid.uuid4().hex
        command = [
            "docker",
            "run",
            "--name",
            name,
            # Explicit finally cleanup owns removal; --rm races docker rm -f
            # when an output-limited process exits while we terminate it.
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--pids-limit=64",
            "--memory=1g",
            "--memory-swap=1g",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "--ulimit",
            "nofile=64:64",
            "--ulimit",
            "fsize=1048576:1048576",
            "--entrypoint",
            "/app/.venv/bin/python",
            resolved,
            "-I",
            "-u",
            "-c",
            # Snap Docker cannot transition AppArmor profiles with NNP already
            # set. Set the irreversible flag in trusted bootstrap code before
            # compiling or executing ANY generated code, retaining docker-default.
            "import ctypes,sys\n"
            "if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0) != 0: sys.exit(125)\n"
            "import sympy\n"
            "if sympy.__version__ != '1.14.0': sys.exit(125)\n"
            "exec(compile(" + repr(source) + ", '<calculation>', 'exec'))",
        ]
        started = time.monotonic()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        outcome = "executed"
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        selector = selectors.DefaultSelector()
        for key, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            selector.register(stream, selectors.EVENT_READ, key)
        try:
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    outcome = "timeout"
                    break
                for event, _ in selector.select(timeout=0.1):
                    chunk = event.fileobj.read1(65536)
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    room = self.output_limit - sum(len(v) for v in output.values())
                    output[event.data].extend(chunk[:room])
                    if len(chunk) > room:
                        outcome = "output_limit"
                        break
                if outcome != "executed":
                    break
        finally:
            selector.close()
            if outcome == "executed":
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    outcome = "timeout"
            # Kill the exact generated name, including descendants, before the CLI.
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            if cleanup.returncode and b"No such container" not in cleanup.stderr:
                raise NimaError(
                    "symbolic worker container cleanup failed; operator intervention required"
                )
        if process.returncode in (125, 126, 127, 255) and outcome == "executed":
            raise NimaError("symbolic worker could not start")
        if outcome == "executed" and process.returncode:
            outcome = "code_failed"
        return {
            "outcome": outcome,
            "source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "image_id": resolved,
            "sympy_version": "1.14.0",
            "exit_code": process.returncode,
            "elapsed_seconds": time.monotonic() - started,
            **{k: v.decode("utf-8", errors="replace") for k, v in output.items()},
            "mathematically_verified": False,
        }

    def check(self, request: SymbolicCheckInput) -> dict:
        checker = Path(__file__).with_name("symbolic_checker.py").read_text()
        # JSON is data inside a fixed program, never concatenated executable input.
        source = "REQUEST = " + repr(request.model_dump(mode="json")) + "\n" + checker
        evidence = self.run(source, request.task.timeout_seconds)
        if evidence["outcome"] != "executed":
            return {"outcome": "inconclusive", "execution": evidence}
        try:
            checked = json.loads(evidence["stdout"])
        except ValueError:
            raise NimaError("symbolic checker returned invalid output") from None
        return {**checked, "execution": evidence, "mathematically_verified": False}
