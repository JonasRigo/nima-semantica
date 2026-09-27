import json
import shutil
from pathlib import Path

import pytest

from nima_semantica.lean_project import (
    LeanProjectRequest, LeanProjectVerifier, PinnedDirectory, _checker, _run, tree_digest,
)
from nima_semantica.storage import GraphStore


class Artifacts:
    artifact = GraphStore.artifact
    read_artifact = GraphStore.read_artifact

    def __init__(self, root):
        self.root = root


@pytest.fixture(scope="module")
def verifier():
    root = Path(__file__).resolve().parents[1] / ".nima/lean-toolchain-4.32.1"
    if not (root / "bin/lean").exists() or not shutil.which("bwrap"):
        pytest.skip("explicit local Lean 4.32.1 installation and bwrap required")
    return LeanProjectVerifier(PinnedDirectory(root, tree_digest(root)), timeout=60)


def request(source, targets=("target",)):
    return LeanProjectRequest({"Submission": source}, targets, ("Submission",))


@pytest.mark.parametrize("source,compiled,accepted", [
    ("theorem target : True := True.intro", True, True),
    ("theorem target : False := by sorry", True, False),
    ("axiom cheat : False\ntheorem target : False := cheat", True, False),
    ("theorem target : False := by rfl", False, False),
    ('#eval IO.println "\'target\' does not depend on any axioms"\n'
     'axiom cheat : False\ntheorem target : False := cheat', True, False),
    ("def target : Nat := 0", True, False),
    ("theorem other : True := True.intro", True, False),
    ('initialize IO.println "SPOOF"\ntheorem target : True := True.intro', True, True),
    ('#eval IO.FS.readFile "/etc/passwd"\ntheorem target : True := True.intro', False, False),
    ('#eval IO.Process.output {cmd := "/usr/bin/true"}\ntheorem target : True := True.intro', False, False),
    ('private axiom cheat : False\ntheorem target : False := cheat', True, False),
    ('theorem unfinished : False := by sorry\ntheorem target : False := unfinished', True, False),
])
def test_local_lean(verifier, tmp_path, source, compiled, accepted):
    store = Artifacts(tmp_path)
    result = verifier.verify(store, request(source))
    evidence = json.loads(store.read_artifact(result.evidence_artifact))
    assert result.compilation_succeeded is compiled, evidence
    assert result.axioms_accepted is accepted, evidence
    assert not result.correspondence_verified
    assert result.certification_verified is accepted, evidence
    if accepted:
        assert result.inspection_succeeded
        assert result.axioms == {"target": ()}
        assert result.compiled_artifacts
        assert result.declaration_types == {"target": "Lean.Expr.const `True []"}
        assert evidence["declaration_types"] == result.declaration_types
        assert evidence["declaration_type_format"] == "lean-expr-repr-v1"
        assert evidence["declaration_type_fingerprints"] == result.declaration_type_fingerprints
        assert evidence["declaration_type_fingerprint_format"] == "lean-expr-alpha-sha256-v2"


def test_type_fingerprints_are_alpha_invariant_but_structure_sensitive(verifier, tmp_path):
    sources={
        "AlphaA":"namespace AlphaA\ntheorem target (P Q : Prop) : P ∧ Q → Q ∧ P := by intro h; exact ⟨h.2,h.1⟩\nend AlphaA",
        "AlphaB":"namespace AlphaB\ntheorem target (P Q : Prop) (_ : P ∧ Q) : Q ∧ P := ⟨And.right ‹P ∧ Q›,And.left ‹P ∧ Q›⟩\nend AlphaB",
        "ChangedConclusion":"namespace ChangedConclusion\ntheorem target (P Q : Prop) (_ : P ∧ Q) : P ∧ Q := ‹P ∧ Q›\nend ChangedConclusion",
        "ChangedBinder":"namespace ChangedBinder\ntheorem target (P Q : Prop) {_ : P ∧ Q} : Q ∧ P := ⟨And.right ‹P ∧ Q›,And.left ‹P ∧ Q›⟩\nend ChangedBinder",
        "UniverseA":"namespace UniverseA\ntheorem target.{u} (α : Type u) (x : α) : x = x := rfl\nend UniverseA",
        "UniverseB":"namespace UniverseB\ntheorem target.{v} (α : Type v) (x : α) : x = x := rfl\nend UniverseB",
        "NatDomain":"namespace NatDomain\ntheorem target (n : Nat) : n = n := rfl\nend NatDomain",
        "IntDomain":"namespace IntDomain\ntheorem target (n : Int) : n = n := rfl\nend IntDomain"}
    targets=("AlphaA.target","AlphaB.target","ChangedConclusion.target","ChangedBinder.target",
        "UniverseA.target","UniverseB.target","NatDomain.target","IntDomain.target")
    result=verifier.verify(Artifacts(tmp_path),LeanProjectRequest(sources,targets,tuple(sources)))
    assert result.certification_verified,result.diagnostics
    fingerprints=result.declaration_type_fingerprints
    assert fingerprints["AlphaA.target"]==fingerprints["AlphaB.target"]
    assert result.declaration_types["AlphaA.target"]!=result.declaration_types["AlphaB.target"]
    assert fingerprints["UniverseA.target"]==fingerprints["UniverseB.target"]
    assert fingerprints["AlphaA.target"]!=fingerprints["ChangedConclusion.target"]
    assert fingerprints["AlphaA.target"]!=fingerprints["ChangedBinder.target"]
    assert fingerprints["NatDomain.target"]!=fingerprints["IntDomain.target"]


def test_local_multiple_modules(verifier, tmp_path):
    result = verifier.verify(Artifacts(tmp_path), LeanProjectRequest(
        {"Helper": "theorem helper : True := True.intro",
         "Main": "import Helper\ntheorem target : True := helper"},
        ("target", "helper"), ("Main",)))
    assert result.axioms_accepted, result.diagnostics
    assert result.certification_verified, result.diagnostics
    assert result.declaration_types == {
        "target": "Lean.Expr.const `True []", "helper": "Lean.Expr.const `True []"}


def test_local_foundational_axiom(verifier, tmp_path):
    result = verifier.verify(Artifacts(tmp_path), request(
        "theorem target (p q : Prop) (h : p ↔ q) : p = q := propext h"))
    assert result.inspection_succeeded, result.diagnostics
    assert result.axioms_accepted
    assert result.axioms == {"target": ("propext",)}
    assert result.certification_verified, result.diagnostics


def test_local_definition_closure(verifier, tmp_path):
    result = verifier.verify(Artifacts(tmp_path), request(
        "def answer : Nat := 42\ndef wrapped : Nat := answer\ntheorem target : wrapped = 42 := rfl"))
    assert result.certification_verified, result.diagnostics
    assert result.kernel_replay_succeeded
    assert not result.correspondence_verified


def test_checked_type_ignores_notation_and_compiler_stdout(verifier, tmp_path):
    store = Artifacts(tmp_path)
    result = verifier.verify(store, request(
        'notation "PretendFalse" => True\n'
        'theorem target : PretendFalse := True.intro\n'
        '#eval IO.println "Lean.Expr.const `False []"'))
    assert result.certification_verified, result.diagnostics
    assert result.declaration_types == {"target": "Lean.Expr.const `True []"}
    evidence = json.loads(store.read_artifact(result.evidence_artifact))
    assert "Lean.Expr.const `False []" in evidence["logs"][0]["output"]
    assert evidence["declaration_types"] == result.declaration_types


def test_std_pinned_import_and_checked_type(verifier, tmp_path):
    configured = LeanProjectVerifier(verifier.toolchain, trusted_imports=("Std",), timeout=60, memory_mb=8192)
    result = configured.verify(Artifacts(tmp_path), request(
        "import Std\ntheorem target : True := by trivial"))
    assert result.certification_verified, result.diagnostics
    assert result.declaration_types == {"target": "Lean.Expr.const `True []"}


@pytest.mark.parametrize("reported_type,replayed", [(None, True), ("", True), ({"type": "True"}, True),
                                                        ("Lean.Expr.const `True []", False)])
def test_malformed_checked_types_fail_closed(verifier, tmp_path, monkeypatch, reported_type, replayed):
    from nima_semantica import lean_project
    run = lean_project._run

    def malformed_checker(command, *args):
        if "--run" not in command:
            return run(command, *args)
        return {"exit_code": 0, "failure": None, "output": json.dumps([{
            "target": "target", "axioms": [], "kernel_replay_succeeded": replayed,
            "diagnostic": "", "declaration_type": reported_type,
        }])}

    monkeypatch.setattr(lean_project, "_run", malformed_checker)
    result = verifier.verify(Artifacts(tmp_path), request("theorem target : True := True.intro"))
    assert result.compilation_succeeded
    assert not result.inspection_succeeded
    assert not result.certification_verified
    assert not result.kernel_replay_succeeded
    assert not result.declaration_types


@pytest.mark.parametrize("source", [
    "inductive Local where | value\ntheorem target : Local.value = Local.value := rfl",
    "opaque localProof : True := True.intro\ntheorem target : True := localProof",
    "inductive Local where | value\ndef unwrap : Local → Nat := fun _ => 0\n"
    "theorem target : unwrap Local.value = 0 := rfl",
])
def test_local_unsupported_closures_are_inconclusive(verifier, tmp_path, source):
    result = verifier.verify(Artifacts(tmp_path), request(source))
    assert result.compilation_succeeded
    assert result.inspection_succeeded
    assert result.axioms_accepted
    assert not result.kernel_replay_succeeded
    assert not result.certification_verified
    assert result.declaration_types == {}
    assert any("unsupported replay declaration" in d for d in result.diagnostics)


@pytest.mark.parametrize("entries,reason", [
    ("[(`target, ``True.intro)]", "kernel rejected declaration"),
    ("[(`target, `target)]", "cyclic dependency"),
    ("[(`helper, `target), (`target, `helper)]", "cyclic dependency"),
])
def test_local_forged_artifact_rejected_by_replay(verifier, tmp_path, entries, reason):
    # Deliberately bypass compilation's kernel. The independent process must reject
    # these artifacts even though their raw axiom closure is empty.
    source = '''import Lean.Elab.Command
import Lean.AddDecl
open Lean Elab Command
elab "forge" : command => do
  for (name, body) in (''' + entries + ''' : List (Name × Name)) do
    let decl := Declaration.thmDecl {
      name := name, levelParams := [], type := mkConst ``False, value := mkConst body }
    match (← getEnv).addDeclCore 0 decl none (doCheck := false) with
    | .ok env => setEnv env
    | .error _ => throwError "forge failed"
forge
'''
    store = Artifacts(tmp_path)
    result = verifier.verify(store, request(source))
    evidence = json.loads(store.read_artifact(result.evidence_artifact))
    assert result.compilation_succeeded, evidence
    assert result.inspection_succeeded, evidence
    assert result.axioms_accepted, evidence
    assert not result.kernel_replay_succeeded
    assert not result.certification_verified
    assert result.declaration_types == {}
    assert any(reason in d for d in result.diagnostics), evidence


def test_trusted_imports_cannot_come_from_request(verifier):
    with pytest.raises(ValueError, match="pinned installation"):
        LeanProjectVerifier(verifier.toolchain, trusted_imports=("Submission",))
    with pytest.raises(ValueError, match="trusted import configuration"):
        LeanProjectVerifier(verifier.toolchain, trusted_imports=("Init\nimport Submission",))


@pytest.fixture(scope="module")
def library_verifier(verifier, tmp_path_factory):
    directory = tmp_path_factory.mktemp("pinned-lean-library")
    store = Artifacts(directory / "store")
    producer = verifier.verify(store, LeanProjectRequest({"Foundation":
        "inductive Local where | value\naxiom external : False\ntheorem foundation : True := True.intro"},
        ("foundation",), ("Foundation",)))
    assert producer.certification_verified, producer.diagnostics
    library = directory / "library"
    library.mkdir()
    for name, artifact in producer.compiled_artifacts.items():
        (library / name).write_bytes(store.read_artifact(artifact))
    return LeanProjectVerifier(verifier.toolchain, libraries=(PinnedDirectory(library, tree_digest(library)),),
                               trusted_imports=("Init", "Foundation"), timeout=60)


@pytest.mark.parametrize("source,accepted", [
    ("import Foundation\ntheorem target : Local.value = Local.value := rfl", True),
    ("import Foundation\ntheorem target : False := external", False),
])
def test_pinned_imports_and_axiom_policy_are_separate(library_verifier, tmp_path, source, accepted):
    result = library_verifier.verify(Artifacts(tmp_path), request(source))
    assert result.compilation_succeeded
    assert result.inspection_succeeded, result.diagnostics
    assert result.kernel_replay_succeeded, result.diagnostics
    assert result.axioms_accepted is accepted
    assert result.certification_verified is accepted
    assert not result.correspondence_verified


def test_source_cannot_shadow_pinned_library(library_verifier, tmp_path):
    with pytest.raises(ValueError, match="shadows a pinned module"):
        library_verifier.verify(Artifacts(tmp_path), LeanProjectRequest(
            {"Foundation": "theorem target : True := True.intro"}, ("target",), ("Foundation",)))


def test_local_output_limit(verifier, tmp_path):
    bounded = LeanProjectVerifier(verifier.toolchain, timeout=30, output_bytes=1024)
    result = bounded.verify(Artifacts(tmp_path), request(
        '#eval IO.println (String.ofList (List.replicate 5000 \'x\'))\ntheorem target : True := True.intro'))
    assert not result.compilation_succeeded
    assert not result.axioms_accepted
    assert "compilation output limit" in result.diagnostics
    assert result.compiler_logs[0]["failure"] == "output limit"


@pytest.mark.parametrize("name", ["../Bad", "A/B", "A\nimport Evil", "A.«x»", "A;evil"])
def test_names_reject_injection(name):
    with pytest.raises(ValueError):
        LeanProjectRequest({name: ""}, ("target",), (name,)).validate()


def test_request_bounds_and_reserved_names():
    for req in [LeanProjectRequest({}, ("target",), ()),
                LeanProjectRequest({"Lean": ""}, ("target",), ("Lean",)),
                request("x" * (2 * 1024 * 1024 + 1)),
                request("", ("target", "target"))]:
        with pytest.raises(ValueError):
            req.validate()


def test_pins_require_exact_content_and_no_symlinks(tmp_path):
    (tmp_path / "a").write_text("first")
    pin = PinnedDirectory(tmp_path, tree_digest(tmp_path))
    pin.validate()
    (tmp_path / "a").write_text("second")
    with pytest.raises(ValueError, match="mismatch"):
        pin.validate()
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        tree_digest(tmp_path)


def test_checker_does_not_load_extensions_or_cached_axioms():
    source = _checker(request(""))
    assert "loadExts := false" in source
    assert "collectAxioms" not in source
    assert "import Submission" not in source
    assert "c.value? (allowOpaque := true)" in source
    assert "c.type.getUsedConstants" in source


def test_process_output_and_time_limits(tmp_path):
    import sys
    with (tmp_path / "fd").open("w+b") as fd:
        result = _run([sys.executable, "-c", "while True: print('x'*4096, flush=True)"], 5, 1024, fd.fileno())
        assert result["failure"] == "output limit"
        assert len(result["output"]) == 1024
        result = _run([sys.executable, "-c", "import time; time.sleep(5)"], .1, 1024, fd.fileno())
        assert result["failure"] == "timeout"


def test_missing_runtime_fails_closed(verifier, tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("bwrap unavailable")
    monkeypatch.setattr("nima_semantica.lean_project._run", missing)
    result = verifier.verify(Artifacts(tmp_path), request("theorem target : True := True.intro"))
    assert not result.compilation_succeeded
    assert not result.axioms_accepted
    assert result.evidence_artifact


def test_command_has_no_network_install_or_writable_host_mount(verifier, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    command = verifier._command(tmp_path, out, 42, ["--run", "/src/Checker.lean"], compile=False)
    assert "--unshare-all" in command
    assert "--clearenv" in command
    assert "--seccomp" in command
    assert "--bind" not in command
    assert "--ro-bind" in command
    assert "lake" not in command and "elan" not in command
    assert any(arg.startswith("--as=") for arg in command)
    assert any(arg.startswith("--fsize=") for arg in command)


def test_malformed_transport_fails_closed(verifier, tmp_path, monkeypatch):
    monkeypatch.setattr("nima_semantica.lean_project._run", lambda *args: {
        "exit_code": 0, "failure": None, "output": '{"axioms": []}'})
    result = verifier.verify(Artifacts(tmp_path), request("theorem target : True := True.intro"))
    assert not result.axioms_accepted
    assert not result.inspection_succeeded
