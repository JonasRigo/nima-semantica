"""Independent verifiers for a deliberately bounded first claim language.

Expressions use an integer polynomial AST, never Python eval or generated shell.
Lean propositions are rendered by this module, not supplied as executable source.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4
from typing import Literal

from pydantic import StrictInt, model_validator

from .models import NimaError, StrictModel, VerificationResult, canonical, identity


class Expression(StrictModel):
    op: Literal["constant", "variable", "add", "sub", "mul"]
    value: StrictInt | None = None
    name: str | None = None
    left: Expression | None = None
    right: Expression | None = None

    @model_validator(mode="after")
    def well_formed(self):
        if self.op == "constant":
            if self.value is None or abs(self.value) > 10**9 or self.name is not None or self.left or self.right:
                raise ValueError("invalid constant")
        elif self.op == "variable":
            if not self.name or not re.fullmatch(r"[a-z][a-z0-9]{0,15}", self.name) or self.value is not None or self.left or self.right:
                raise ValueError("invalid variable")
        elif self.left is None or self.right is None or self.value is not None or self.name is not None:
            raise ValueError("invalid operator")
        return self


class PolynomialClaim(StrictModel):
    variables: tuple[str, ...]
    left: Expression
    right: Expression
    relation: Literal["eq", "le", "lt"] = "eq"

    @model_validator(mode="after")
    def validate_variables(self):
        if len(self.variables) > 8 or len(set(self.variables)) != len(self.variables):
            raise ValueError("invalid quantified variables")
        def walk(e, depth=0):
            if depth > 12:
                raise ValueError("expression too deep")
            if e.op == "variable" and e.name not in self.variables:
                raise ValueError("free variable")
            if e.left:
                walk(e.left, depth + 1)
                walk(e.right, depth + 1)
        walk(self.left)
        walk(self.right)
        for variable in self.variables:
            if not re.fullmatch(r"[a-z][a-z0-9]{0,15}", variable):
                raise ValueError("invalid quantified name")
        return self


def evaluate(expression, values):
    if expression.op == "constant":
        return expression.value
    if expression.op == "variable":
        return values[expression.name]
    left, right = evaluate(expression.left, values), evaluate(expression.right, values)
    return {"add": lambda: left + right, "sub": lambda: left - right, "mul": lambda: left * right}[expression.op]()


def holds(claim, values):
    left, right = evaluate(claim.left, values), evaluate(claim.right, values)
    return {"eq": left == right, "le": left <= right, "lt": left < right}[claim.relation]


def exact_counterexample(store, claim: PolynomialClaim, witness: dict[str, int]):
    if set(witness) != set(claim.variables) or any(type(v) is not int or abs(v) > 10**9 for v in witness.values()):
        raise NimaError("invalid exact witness")
    refutes = not holds(claim, witness)
    evidence = store.artifact(canonical({"schema": 1, "claim": claim.model_dump(), "witness": witness,
                                         "left": evaluate(claim.left, witness), "right": evaluate(claim.right, witness), "refutes": refutes}))
    return VerificationResult(target_id=identity(claim), protocol="integer-polynomial-exact-v1", outcome="refuted" if refutes else "inconclusive",
                              scope="encoded universally quantified integer polynomial claim", evidence_artifact=evidence)


def lean_expression(expression):
    if expression.op == "constant":
        return f"({expression.value} : Int)"
    if expression.op == "variable":
        return expression.name
    operator = {"add": "+", "sub": "-", "mul": "*"}[expression.op]
    return f"({lean_expression(expression.left)} {operator} {lean_expression(expression.right)})"


def lean_source(claim: PolynomialClaim, tactic: str):
    # Closed tactic vocabulary: no user IO, imports, declarations, or bypasses.
    if tactic not in ("omega", "simp", "rfl", "decide"):
        raise NimaError("unsupported Lean tactic")
    binders = " ".join(f"({v} : Int)" for v in claim.variables)
    relation = {"eq": "=", "le": "≤", "lt": "<"}[claim.relation]
    return f"import Std\ntheorem nima_target {binders} : {lean_expression(claim.left)} {relation} {lean_expression(claim.right)} := by\n  {tactic}\n#print axioms nima_target\n"


class LeanVerifier:
    def __init__(self, image: str | None = None, lean_toolchain: Path | None = None, *, timeout=30, runtime="docker"):
        if runtime not in ("docker", "bwrap"):
            raise ValueError("unsupported verifier runtime")
        if runtime == "docker" and (image is None or not re.fullmatch(r"sha256:[a-f0-9]{64}", image)):
            raise ValueError("verifier image must be pinned by local image digest")
        self.image, self.toolchain, self.timeout, self.runtime = image, Path(lean_toolchain).resolve() if lean_toolchain else None, timeout, runtime
        if self.toolchain and not (self.toolchain / "bin" / "lean").is_file():
            raise ValueError("Lean toolchain unavailable")
        if runtime == "bwrap" and self.toolchain is None:
            raise ValueError("Bubblewrap requires an explicit Lean toolchain")
        self.toolchain_hash = self._toolchain_hash() if self.toolchain else None

    def _toolchain_hash(self):
        digest = hashlib.sha256()
        for path in sorted(self.toolchain.rglob("*")):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(self.toolchain):
                raise NimaError("toolchain symlink escapes declared directory")
            digest.update(str(path.relative_to(self.toolchain)).encode() + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def verify(self, store, claim, tactic):
        if self.toolchain and self._toolchain_hash() != self.toolchain_hash:
            raise NimaError("verifier toolchain changed after configuration")
        source = lean_source(claim, tactic)
        source_artifact = store.artifact(source.encode())
        scratch = store.root / "verifier-scratch"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="nima-lean-", dir=scratch) as directory:
            path = Path(directory)
            path.chmod(0o755)
            (path / "Main.lean").write_text(source)
            container_name = "nima-verifier-" + uuid4().hex
            command = [self.runtime, "run", "--name", container_name, "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--cpus=1", "--user=65534:65534",
                       "--mount", f"type=bind,src={path},dst=/work,readonly"]
            if self.toolchain:
                command.extend(["--mount", f"type=bind,src={self.toolchain},dst=/lean,readonly"])
            lean_limits = ["-j1", "-s8192", "-M512"]
            command.extend(["--entrypoint", "/lean/bin/lean", self.image, *lean_limits, "/work/Main.lean"])
            if self.runtime == "bwrap":
                command = ["prlimit", "--as=4294967296", f"--cpu={self.timeout}", "--fsize=10485760",
                    "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                    "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
                    "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--clearenv",
                    "--ro-bind", str(self.toolchain), "/lean", "--ro-bind", str(path), "/work", "/lean/bin/lean", *lean_limits, "/work/Main.lean"]
            try:
                completed = subprocess.run(command, capture_output=True, timeout=self.timeout, check=False)
                output = completed.stdout.decode(errors="replace")
                # No generated commands can spoof this output: the renderer fixes all source structure.
                axioms = re.search(r"'nima_target' depends on axioms: \[(.*?)\]", output, re.S)
                no_axioms = "'nima_target' does not depend on any axioms" in output
                allowed = {"propext", "Classical.choice", "Quot.sound"}
                actual = {a.strip() for a in axioms.group(1).split(",") if a.strip()} if axioms else set()
                valid = completed.returncode == 0 and (no_axioms or axioms is not None) and actual <= allowed
                if self.toolchain and self._toolchain_hash() != self.toolchain_hash:
                    valid = False
                evidence = store.artifact(canonical({"source_artifact": source_artifact, "image": self.image,
                    "runtime": self.runtime, "toolchain_hash": self.toolchain_hash, "lean_limits": lean_limits,
                    "output": output, "stderr": completed.stderr.decode(errors="replace"), "exit_code": completed.returncode, "axioms": sorted(actual), "protocol": "lean-polynomial-v1"}))
                return VerificationResult(target_id=identity(claim), protocol="lean-polynomial-v1", outcome="verified" if valid else "failed",
                    scope="encoded integer polynomial proposition; source correspondence unresolved", evidence_artifact=evidence)
            except subprocess.TimeoutExpired:
                if self.runtime == "docker":
                    subprocess.run([self.runtime, "rm", "--force", container_name], capture_output=True, timeout=10, check=False)
                return VerificationResult(target_id=identity(claim), protocol="lean-polynomial-v1", outcome="failed", scope="encoded claim", diagnostics=("timeout",))
