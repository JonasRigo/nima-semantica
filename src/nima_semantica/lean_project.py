"""Offline, isolated Lean artifact verification, independent of workflow records.

The caller pins trusted installations by a full tree SHA256, including libraries.
Compilation is untrusted execution. A fresh trusted program imports only the
resulting olean data, without extension initialization, and walks actual bodies.
Ordinary theorem/definition closures are replayed through the kernel into a
separate pinned-import environment. Unsupported closures fail certification.
Source correspondence is never asserted.
"""
from __future__ import annotations

import hashlib
import base64
import json
import math
import os
import platform
import re
import selectors
import signal
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


class ArtifactStore(Protocol):
    def artifact(self, data: bytes) -> str: ...


def tree_digest(root: Path) -> str:
    """Hash an installation, allowing only internal regular-file symlinks."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("pin requires a real directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() and (not path.resolve().is_relative_to(root.resolve()) or not path.is_file()):
            raise ValueError("installation symlink escapes root or is not a file")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("non-regular installation entry")
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big") + name)
        link = os.readlink(path).encode() if path.is_symlink() else b""
        digest.update(len(link).to_bytes(8, "big") + link)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PinnedDirectory:
    path: Path
    sha256: str

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("explicit SHA256 pin required")
        if tree_digest(self.path) != self.sha256:
            raise ValueError("installation pin mismatch")


@dataclass(frozen=True)
class LeanProjectRequest:
    # Ordered modules; dependencies must precede their importers. No Lake code runs.
    sources: dict[str, str]
    targets: tuple[str, ...]
    imports: tuple[str, ...]

    def validate(self) -> None:
        if not 1 <= len(self.sources) <= 32 or not 1 <= len(self.targets) <= 64:
            raise ValueError("bounded nonempty sources and targets required")
        names = (*self.sources, *self.targets, *self.imports)
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", n)
               or len(n) > 240 for n in names):
            raise ValueError("invalid Lean name")
        if not self.imports or not set(self.imports) <= self.sources.keys():
            raise ValueError("checker imports must name submitted modules")
        if len(set(self.targets)) != len(self.targets) or len(set(self.imports)) != len(self.imports):
            raise ValueError("duplicate targets/imports")
        if sum(len(s.encode()) for s in self.sources.values()) > 2 * 1024 * 1024:
            raise ValueError("source size limit")
        if any(n.split('.')[0] in {"Lean", "Init", "Std", "Checker"} for n in self.sources):
            raise ValueError("reserved module name")


@dataclass(frozen=True)
class LeanProjectResult:
    compilation_succeeded: bool
    inspection_succeeded: bool
    axioms_accepted: bool
    evidence_artifact: str
    source_artifacts: dict[str, str] = field(default_factory=dict)
    compiled_artifacts: dict[str, str] = field(default_factory=dict)
    axioms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    compiler_logs: tuple[dict, ...] = ()
    correspondence_verified: Literal[False] = False
    certification_verified: bool = False
    kernel_replay_succeeded: bool = False
    declaration_types: dict[str, str] = field(default_factory=dict)
    declaration_type_fingerprints: dict[str, str] = field(default_factory=dict)


def _checker(request: LeanProjectRequest, trusted_imports: tuple[str, ...] = ("Init",),
             library_count: int = 0) -> str:
    # Names are validated before interpolation. Never import submitted source here.
    imports = ", ".join('{ module := `' + n + ' }' for n in request.imports)
    targets = ", ".join('`' + n for n in request.targets)
    pinned_imports = ", ".join('{ module := `' + n + ' }' for n in trusted_imports)
    paths = ", ".join(json.dumps(p) for p in ["/lean/lib/lean", *(f"/library{i}" for i in range(library_count))])
    return '''import Lean.Environment
import Lean.Data.Json
open Lean
deriving instance BEq for Lean.InductiveVal
partial def normalizeLevel (params : List Name) : Level → Level
  | .zero => .zero
  | .succ level => .succ (normalizeLevel params level)
  | .max left right => .max (normalizeLevel params left) (normalizeLevel params right)
  | .imax left right => .imax (normalizeLevel params left) (normalizeLevel params right)
  | .param name => .param (Name.mkNum `_nimaUniverse (params.idxOf name))
  | .mvar id => .mvar id

partial def normalizeExpr (params : List Name) : Expr → Expr
  | .bvar index => .bvar index
  | .fvar id => .fvar id
  | .mvar id => .mvar id
  | .sort level => .sort (normalizeLevel params level)
  | .const name levels => .const name (levels.map (normalizeLevel params))
  | .app function argument => .app (normalizeExpr params function) (normalizeExpr params argument)
  | .lam _ type body binderInfo => .lam `_nimaBinder (normalizeExpr params type) (normalizeExpr params body) binderInfo
  | .forallE _ type body binderInfo => .forallE `_nimaBinder (normalizeExpr params type) (normalizeExpr params body) binderInfo
  | .letE _ type value body nondep => .letE `_nimaBinder (normalizeExpr params type) (normalizeExpr params value) (normalizeExpr params body) nondep
  | .lit literal => .lit literal
  | .mdata _ expression => normalizeExpr params expression
  | .proj typeName index structureExpr => .proj typeName index (normalizeExpr params structureExpr)

def sameConstant (a b : ConstantInfo) : Bool :=
  match a, b with
  | .axiomInfo a, .axiomInfo b => a == b
  | .defnInfo a, .defnInfo b => a == b
  | .thmInfo a, .thmInfo b => a == b
  | .opaqueInfo a, .opaqueInfo b => a == b
  | .inductInfo a, .inductInfo b => a == b
  | .ctorInfo a, .ctorInfo b => a == b
  | .recInfo a, .recInfo b => a == b
  | .quotInfo a, .quotInfo b =>
    a.toConstantVal == b.toConstantVal && (match a.kind, b.kind with
      | .type, .type | .ctor, .ctor | .lift, .lift | .ind, .ind => true
      | _, _ => false)
  | _, _ => false

partial def visit (base : Kernel.Environment) (env : Environment) (n : Name)
    (seen : IO.Ref (Array Name)) (axs : IO.Ref (Array String)) : IO Unit := do
  if (← seen.get).contains n then return
  seen.modify (·.push n)
  if let some original := base.find? n then
    if let some imported := env.find? n then
      if !sameConstant original imported then
        throw (IO.userError s!"pinned declaration mismatch: {n}")
  let some c := (base.find? n).orElse (fun _ => env.find? n)
    | throw (IO.userError "missing dependency")
  if c.isUnsafe || c.isPartial then throw (IO.userError "unsafe or partial dependency")
  if n == `sorryAx then throw (IO.userError "incomplete proof")
  if c.type.hasMVar then throw (IO.userError "incomplete type")
  if let .axiomInfo _ := c then axs.modify (·.push n.toString)
  for dep in c.type.getUsedConstants do visit base env dep seen axs
  if let some v := c.value? (allowOpaque := true) then
    if v.hasMVar then throw (IO.userError "incomplete body")
    for dep in v.getUsedConstants do visit base env dep seen axs
  if let .inductInfo v := c then
    for dep in v.ctors do visit base env dep seen axs

partial def replay (candidate : Environment) (n : Name)
    (checked : IO.Ref Kernel.Environment) (active : Array Name) : IO Unit := do
  if active.contains n then throw (IO.userError s!"cyclic dependency: {n}")
  if ((← checked.get).find? n).isSome then return
  let some c := candidate.find? n | throw (IO.userError s!"missing replay dependency: {n}")
  if c.name != n then throw (IO.userError "declaration name mismatch")
  if c.isUnsafe || c.isPartial then throw (IO.userError "unsafe or partial replay dependency")
  let decl ← match c with
    | .thmInfo v => pure (Declaration.thmDecl v)
    | .defnInfo v => pure (Declaration.defnDecl v)
    | _ => throw (IO.userError s!"unsupported replay declaration: {n}")
  for dep in c.type.getUsedConstants do replay candidate dep checked (active.push n)
  let some value := c.value? (allowOpaque := true) | throw (IO.userError "missing replay body")
  for dep in value.getUsedConstants do replay candidate dep checked (active.push n)
  match (← checked.get).addDeclCore 10000000 decl (cancelTk? := none) with
  | .ok next => checked.set next
  | .error _ => throw (IO.userError s!"kernel rejected declaration: {n}")

def main : IO Unit := do
  -- The trust root is loaded BEFORE candidate data and cannot resolve artifacts.
  let pinnedPaths : System.SearchPath := [''' + paths + ''']
  searchPathRef.set pinnedPaths
  let trusted ← importModules #[''' + pinned_imports + '''] {} (trustLevel := 0) (loadExts := false)
  let base := trusted.checked.get
  searchPathRef.set (pinnedPaths ++ (["/artifacts"] : System.SearchPath))
  let env ← importModules #[''' + imports + '''] {} (loadExts := false)
  let mut rows : Array Json := #[]
  for n in (#[''' + targets + '''] : Array Name) do
    let some (.thmInfo _) := env.find? n
      | throw (IO.userError "target is not a theorem")
    let seen ← IO.mkRef #[]
    let axs ← IO.mkRef #[]
    visit base env n seen axs
    let checked ← IO.mkRef base
    let mut replayed := false
    let mut diagnostic := ""
    let mut declarationType : Json := Json.null
    let mut canonicalDeclarationType : Json := Json.null
    try
      replay env n checked #[]
      let some checkedDecl := (← checked.get).find? n
        | throw (IO.userError "replayed target absent")
      -- Repr Expr is compiled from pinned Lean, not an environment pretty-printer.
      declarationType := toJson (reprStr checkedDecl.type)
      canonicalDeclarationType := toJson (reprStr (normalizeExpr checkedDecl.levelParams checkedDecl.type))
      replayed := true
    catch error => diagnostic := error.toString
    rows := rows.push (Json.mkObj [("target", toJson n.toString), ("axioms", toJson (← axs.get)),
      ("kernel_replay_succeeded", toJson replayed), ("diagnostic", toJson diagnostic),
      ("declaration_type", declarationType), ("canonical_declaration_type", canonicalDeclarationType)])
  IO.println (Json.compress (Json.arr rows))
'''


def _seccomp() -> bytes:
    """Linux x86-64/ARM64: permit threads, prohibit new processes (aggregate AS bound)."""
    machine = platform.machine()
    if machine == "x86_64":
        arch, clone = 0xC000003E, 56
        blocked = (57, 58, 435, 101, 311, 272, 308, 165, 166, 428, 429, 430, 431, 432, 442)
    elif machine == "aarch64":
        # Linux asm-generic/unistd.h; ARM64 has no separate fork/vfork calls.
        arch, clone = 0xC00000B7, 220
        blocked = (435, 117, 271, 97, 268, 40, 39, 428, 429, 430, 431, 432, 442)
    else:
        raise ValueError("sandbox syscall policy supports Linux x86_64 and aarch64 only")
    # BPF: validate arch; block x32, fork/vfork/clone3; clone requires CLONE_THREAD.
    ins = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
           (0x20, 0, 0, 0), (0x45, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000)]
    for syscall in blocked:
        ins += [(0x15, 0, 1, syscall), (0x06, 0, 0, 0x50000 | 38)]
    ins += [(0x15, 0, 4, clone), (0x20, 0, 0, 16), (0x45, 1, 0, 0x10000),
            (0x06, 0, 0, 0x50000 | 1), (0x06, 0, 0, 0x7FFF0000),
            (0x06, 0, 0, 0x7FFF0000)]
    return b"".join(struct.pack("HBBI", *i) for i in ins)


# Compilation writes into a bounded tmpfs because Lean replaces output files.
# This fixed transport process returns bytes, never an axiom verdict.
_COMPILER = '''import base64, ctypes, json, subprocess, sys
from pathlib import Path
cfg = json.loads(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 0, 0, 0, 0) != 0: raise RuntimeError("dumpability")
policy = Path('/src/Policy').read_bytes()
class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]
filters = (Filter * (len(policy)//8)).from_buffer_copy(policy)
program = Program(len(filters), filters)
def restrict():
    if libc.prctl(38, 1, 0, 0, 0) != 0: raise RuntimeError("no_new_privs")
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0: raise RuntimeError("seccomp")
with open('/tmp/compile.log', 'w+b') as log:
    failure = None
    try:
        p = subprocess.run(cfg['argv'], stdout=log, stderr=log, timeout=cfg['timeout'], preexec_fn=restrict)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = -1
        failure = 'timeout'
    log.seek(0)
    output = log.read(cfg['output_limit'] + 1)
    if len(output) > cfg['output_limit']:
        code = -1
        if failure is None: failure = 'output limit'
    artifacts = {}
    if code == 0:
        for suffix in ('.olean', '.olean.private', '.olean.server'):
            path = Path('/build/Result' + suffix)
            if path.is_symlink(): raise RuntimeError('artifact symlink')
            if path.exists():
                with path.open('rb') as artifact:
                    data = artifact.read(cfg['artifact_limit'] + 1)
                if len(data) > cfg['artifact_limit']: raise RuntimeError('artifact size')
                if data: artifacts[suffix] = base64.b64encode(data).decode('ascii')
    print(json.dumps({'exit_code': code, 'failure': failure, 'output': output[:cfg['output_limit']].decode(errors='replace'), 'artifacts': artifacts}))
'''


def _run(command: list[str], timeout: float, limit: int, fd: int) -> dict:
    start = time.monotonic()
    data = bytearray()
    reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True, pass_fds=(fd,)) as process:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - start >= timeout:
                    reason = "timeout"
                    break
                for key, _ in selector.select(min(.1, timeout)):
                    chunk = os.read(key.fd, min(65536, limit + 1 - len(data)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        data.extend(chunk)
                if len(data) > limit:
                    reason = "output limit"
                    break
            if reason:
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=max(.01, timeout - (time.monotonic() - start)))
            except subprocess.TimeoutExpired:
                reason = "timeout"
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return {"exit_code": process.returncode, "failure": reason,
            "output": bytes(data[:limit]).decode(errors="replace")}


class LeanProjectVerifier:
    """Trusted configuration, untrusted requests. Requires bwrap and prlimit.

    Installations must be administrator owned and immutable during verification.
    Library roots contain precompiled modules; no dependency resolver is invoked.
    AS limits bound Lean and its threads, and separately the compilation transport
    process. Writable tmpfs has a separate bound. No host directory is writable.
    """

    def __init__(self, toolchain: PinnedDirectory, *, libraries: tuple[PinnedDirectory, ...] = (),
                 trusted_imports: tuple[str, ...] = ("Init",),
                 timeout: float = 30, output_bytes: int = 1024 * 1024,
                 memory_mb: int = 4096, artifact_bytes: int = 16 * 1024 * 1024):
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("timeout must be in (0, 300]")
        if not 1024 <= output_bytes <= 16 * 1024 * 1024 or not 512 <= memory_mb <= 8192:
            raise ValueError("invalid resource bounds")
        if not 1024 <= artifact_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid artifact bound")
        self.toolchain, self.libraries = toolchain, tuple(libraries)
        self.trusted_imports = tuple(trusted_imports)
        if not 1 <= len(self.trusted_imports) <= 64 or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", n)
            or len(n) > 240 for n in self.trusted_imports):
            raise ValueError("invalid trusted import configuration")
        self.timeout, self.output_bytes = timeout, output_bytes
        self.memory_mb, self.artifact_bytes = memory_mb, artifact_bytes
        self._pins()
        if not (toolchain.path / "bin/lean").is_file():
            raise ValueError("Lean binary unavailable")
        roots = (toolchain.path / "lib/lean", *(library.path for library in self.libraries))
        for name in self.trusted_imports:
            if not any((root / (name.replace('.', '/') + '.olean')).is_file() for root in roots):
                raise ValueError("trusted import is not in a pinned installation")

    def _pins(self):
        for pin in (self.toolchain, *self.libraries):
            pin.validate()

    def _command(self, root: Path, output: Path, fd: int, args: list[str], *, compile: bool) -> list[str]:
        command = ["prlimit", f"--as={self.memory_mb * 1024 * 1024}",
                   f"--cpu={math.ceil(self.timeout)}", f"--fsize={self.artifact_bytes}",
                   "--nofile=128", "--core=0", "bwrap", "--unshare-all", "--die-with-parent",
                   "--new-session", "--cap-drop", "ALL", "--clearenv"]
        for system in ("/usr", "/lib", "/lib64"):
            if Path(system).exists():
                command += ["--ro-bind", system, system]
        command += ["--proc", "/proc", "--dev", "/dev", "--size", "16777216", "--tmpfs", "/tmp",
                    "--ro-bind", str(self.toolchain.path.resolve()), "/lean",
                    "--ro-bind", str(root), "/src", "--ro-bind", str(output), "/artifacts"]
        if compile:
            command += ["--size", str(self.artifact_bytes * 3), "--tmpfs", "/build"]
        search = ["/lean/lib/lean"]
        for i, library in enumerate(self.libraries):
            command += ["--ro-bind", str(library.path.resolve()), f"/library{i}"]
            search.append(f"/library{i}")
        search.append("/artifacts")
        command += ["--setenv", "LEAN_PATH", ":".join(search), "--chdir", "/src"]
        lean = ["/lean/bin/lean", "-j1", "-s8192", f"-M{self.memory_mb // 2}", *args]
        if compile:
            return command + ["/usr/bin/python3", "-I", "/src/Runner.py", json.dumps({"argv": lean,
                "timeout": self.timeout, "output_limit": self.output_bytes, "artifact_limit": self.artifact_bytes})]
        return command + ["--seccomp", str(fd), *lean]

    def verify(self, store: ArtifactStore, request: LeanProjectRequest) -> LeanProjectResult:
        # Snapshot mutable caller data before validation or execution.
        request = LeanProjectRequest(dict(request.sources), tuple(request.targets), tuple(request.imports))
        request.validate()
        roots = (self.toolchain.path / "lib/lean", *(library.path for library in self.libraries))
        if any((root / (name.replace('.', '/') + '.olean')).exists()
               for root in roots for name in request.sources):
            raise ValueError("submitted module shadows a pinned module")
        sources = {n: store.artifact(s.encode()) for n, s in request.sources.items()}
        artifacts, logs, axioms, diagnostics = {}, [], {}, []
        declaration_types, declaration_type_fingerprints = {}, {}
        compiled = inspected = accepted = replayed = certified = False
        checker = _checker(request, self.trusted_imports, len(self.libraries))
        checker_id = store.artifact(checker.encode())
        compiler_id = store.artifact(_COMPILER.encode())
        try:
            self._pins()
            with tempfile.TemporaryDirectory(prefix="nima-project-") as scratch:
                base = Path(scratch)
                src, out, trusted = (base / n for n in ("source", "artifacts", "trusted"))
                for path in (src, out, trusted):
                    path.mkdir()
                for name, source in request.sources.items():
                    relative = name.replace(".", "/")
                    path = src / (relative + ".lean")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source)
                (src / "Runner.py").write_text(_COMPILER)
                (src / "Policy").write_bytes(_seccomp())
                (trusted / "Checker.lean").write_text(checker)
                with tempfile.TemporaryFile() as policy:
                    policy.write(_seccomp())
                    policy.flush()
                    deadline = time.monotonic() + self.timeout
                    for name in request.sources:
                        policy.seek(0)
                        relative = name.replace(".", "/")
                        command = self._command(src, out, policy.fileno(),
                            ["-o", "/build/Result.olean", f"/src/{relative}.lean"], compile=True)
                        log = _run(command, max(.01, deadline - time.monotonic()),
                                   self.artifact_bytes * 4 + self.output_bytes * 6 + 4096, policy.fileno())
                        if log["exit_code"] != 0 or log["failure"]:
                            logs.append({**log, "output": log["output"][:self.output_bytes]})
                            raise ValueError("compilation " + (log["failure"] or "failed"))
                        transport = json.loads(log["output"])
                        failure = transport.get("failure")
                        if failure not in (None, "timeout", "output limit"):
                            raise ValueError("invalid compilation failure reason")
                        logs.append({"exit_code": transport["exit_code"], "failure": failure,
                                     "output": transport["output"][:self.output_bytes]})
                        if transport["exit_code"] != 0:
                            raise ValueError("compilation " + (failure or "failed"))
                        for suffix, encoded in transport["artifacts"].items():
                            if suffix not in (".olean", ".olean.private", ".olean.server"):
                                raise ValueError("unexpected artifact")
                            data = base64.b64decode(encoded, validate=True)
                            if len(data) > self.artifact_bytes:
                                raise ValueError("artifact size")
                            destination = out / (relative + suffix)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(data)
                    for name in request.sources:
                        if (out / (name.replace('.', '/') + '.olean')).stat().st_size == 0:
                            raise ValueError("missing compiled artifact")
                    compiled = True
                    for path in sorted(out.rglob("*")):
                        if path.is_file() and path.stat().st_size:
                            artifacts[path.relative_to(out).as_posix()] = store.artifact(path.read_bytes())
                    policy.seek(0)
                    log = _run(self._command(trusted, out, policy.fileno(), ["--run", "/src/Checker.lean"], compile=False),
                               max(.01, deadline - time.monotonic()), self.output_bytes, policy.fileno())
                    logs.append(log)
                    if log["exit_code"] != 0 or log["failure"]:
                        raise ValueError("independent inspection " + (log["failure"] or "failed"))
                    rows = json.loads(log["output"])
                    if not isinstance(rows, list) or len(rows) != len(request.targets):
                        raise ValueError("invalid checker response")
                    replay_results = []
                    for target, row in zip(request.targets, rows):
                        if not isinstance(row, dict) or set(row) != {"target", "axioms", "kernel_replay_succeeded", "diagnostic", "declaration_type", "canonical_declaration_type"} or row["target"] != target:
                            raise ValueError("invalid checker target")
                        if not isinstance(row["axioms"], list) or any(not isinstance(a, str) for a in row["axioms"]):
                            raise ValueError("invalid axiom response")
                        axioms[target] = tuple(sorted(set(row["axioms"])))
                        if type(row["kernel_replay_succeeded"]) is not bool or not isinstance(row["diagnostic"], str):
                            raise ValueError("invalid kernel replay response")
                        if row["kernel_replay_succeeded"]:
                            if not isinstance(row["declaration_type"], str) or not row["declaration_type"]:
                                raise ValueError("missing checked declaration type")
                            if not isinstance(row["canonical_declaration_type"], str) or not row["canonical_declaration_type"]:
                                raise ValueError("missing canonical declaration type")
                            declaration_types[target] = row["declaration_type"]
                            declaration_type_fingerprints[target] = hashlib.sha256(row["canonical_declaration_type"].encode()).hexdigest()
                        elif row["declaration_type"] is not None or row["canonical_declaration_type"] is not None:
                            raise ValueError("unchecked declaration type reported as checked")
                        replay_results.append(row["kernel_replay_succeeded"])
                        if row["diagnostic"]:
                            diagnostics.append(f"{target}: {row['diagnostic']}")
                    inspected = True
                    replayed = all(replay_results)
                    accepted = all(set(ax) <= {"propext", "Classical.choice", "Quot.sound"} for ax in axioms.values())
                    if not accepted:
                        diagnostics.append("unaccepted axioms")
            self._pins()
            certified = compiled and inspected and accepted and replayed
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
            accepted = False
            diagnostics.append(str(exc))
        evidence = store.artifact(json.dumps({"protocol": "lean-project-kernel-replay-v3", "runtime": "bwrap", "sources": sources,
            "artifacts": artifacts, "checker": checker_id, "toolchain": self.toolchain.sha256,
            "compiler_transport": compiler_id, "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
            "libraries": [p.sha256 for p in self.libraries], "logs": logs, "axioms": axioms,
            "trusted_imports": self.trusted_imports, "kernel_replay_succeeded": replayed,
            "declaration_types": declaration_types, "declaration_type_format": "lean-expr-repr-v1",
            "declaration_type_fingerprints": declaration_type_fingerprints,
            "declaration_type_fingerprint_format": "lean-expr-alpha-sha256-v2",
            "targets": request.targets, "imports": request.imports, "diagnostics": diagnostics,
            "compilation_succeeded": compiled, "inspection_succeeded": inspected, "axioms_accepted": accepted,
            "correspondence_verified": False, "certification_verified": certified,
            "limits": {"seconds": self.timeout, "output_bytes_per_process": self.output_bytes,
                       "memory_mb": self.memory_mb, "artifact_bytes_per_file": self.artifact_bytes}},
            sort_keys=True).encode())
        return LeanProjectResult(compiled, inspected, accepted, evidence, sources, artifacts, axioms,
                                 tuple(diagnostics), compiler_logs=tuple(logs), certification_verified=certified,
                                 kernel_replay_succeeded=replayed, declaration_types=declaration_types,
                                 declaration_type_fingerprints=declaration_type_fingerprints)
