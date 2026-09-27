"""Typed, context-isolated mathematical graphs and bounded Horn inference.

Consistency is relative to explicit assumptions, never proof certification.
The Semantica adapter computes consequences; support reconstruction keeps
alternative derivations separate (the native aggregate premises do not).
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from importlib.metadata import version
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import NimaError, StrictModel, identity

Symbol = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")]
Argument = Annotated[str, Field(pattern=r"^\??[A-Za-z][A-Za-z0-9_]{0,79}$")]


def symbol(value: str, variable=False):
    if not re.fullmatch(r"\??[A-Za-z][A-Za-z0-9_]{0,79}" if variable else r"[A-Za-z][A-Za-z0-9_]{0,79}", value):
        raise ValueError("invalid logical symbol")
    return value


class Predicate(StrictModel):
    name: Symbol
    argument_types: tuple[Symbol, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid(self):
        symbol(self.name)
        for item in self.argument_types:
            symbol(item)
        return self


class Atom(StrictModel):
    predicate: Symbol
    arguments: tuple[Argument, ...] = Field(min_length=1, max_length=8,
        description="Declared entity identifiers or ?variables, never expressions or descriptions.")
    negative: bool = False

    @model_validator(mode="after")
    def valid(self):
        symbol(self.predicate)
        for arg in self.arguments:
            symbol(arg, variable=True)
        return self

    @property
    def key(self):
        return identity(self)

    def encoded(self):
        # Separate explicit positive/negative predicates; no negation-as-failure.
        return ("Neg_" if self.negative else "Pos_") + self.predicate + "(" + ", ".join(self.arguments) + ")"


class SourceReference(StrictModel):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    region_id: str = Field(min_length=1, max_length=200)
    quotation: str = Field(default="", max_length=20000,
        description="Exact contiguous source substring, preserving whitespace and TeX; no inserted ellipses or paraphrase.")


class Assertion(StrictModel):
    atom: Atom
    origin: Literal["proposed", "attributed", "assumed"] = "proposed"
    sources: tuple[SourceReference, ...] = ()
    justification: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def valid(self):
        if any(a.startswith("?") for a in self.atom.arguments):
            raise ValueError("assertions must be ground")
        if self.origin == "attributed" and not self.sources:
            raise ValueError("attribution requires source provenance")
        if self.origin == "assumed" and not self.justification.strip():
            raise ValueError("assumption requires explicit justification")
        return self

    @property
    def id(self):
        return identity(self)


class HornRule(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    premises: tuple[Atom, ...] = Field(min_length=1, max_length=8)
    conclusion: Atom
    origin: Literal["proposed", "assumed"] = "proposed"
    justification: str = Field(default="", max_length=5000)
    sources: tuple[SourceReference, ...] = ()

    @model_validator(mode="after")
    def safe(self):
        bound = {a for p in self.premises for a in p.arguments if a.startswith("?")}
        if any(a.startswith("?") and a not in bound for a in self.conclusion.arguments):
            raise ValueError("unsafe head variable")
        if self.origin == "assumed" and not self.justification.strip():
            raise ValueError("rule admission requires an explicit assumption justification")
        return self

    @property
    def id(self):
        return identity(self)

    def encoded(self):
        return "IF " + " AND ".join(p.encoded() for p in self.premises) + " THEN " + self.conclusion.encoded()


class ContextPolicy(StrictModel):
    allow_assumptions: bool = False
    max_facts: int = Field(default=2000, ge=1, le=10000)
    max_matches: int = Field(default=10000, ge=1, le=100000)
    max_rounds: int = Field(default=64, ge=1, le=256)
    timeout_seconds: float = Field(default=30, gt=0, le=120)


class GraphSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    graph_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    context_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    policy: ContextPolicy = Field(default_factory=ContextPolicy)
    predicates: tuple[Predicate, ...] = Field(default=(), max_length=200)
    entities: dict[Symbol, Symbol] = Field(default_factory=dict, max_length=2000,
        description="Entity identifier to type identifier, matching predicate argument_types; not entity descriptions.")
    assertions: tuple[Assertion, ...] = Field(default=(), max_length=2000)
    rules: tuple[HornRule, ...] = Field(default=(), max_length=200)
    parent_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def typed(self):
        predicates = {p.name: p for p in self.predicates}
        if len(predicates) != len(self.predicates):
            raise ValueError("duplicate predicate declaration")
        for entity, typ in self.entities.items():
            symbol(entity)
            symbol(typ)
        if len({a.id for a in self.assertions}) != len(self.assertions) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate graph item")
        groups = [(a.atom,) for a in self.assertions] + [(*r.premises, r.conclusion) for r in self.rules]
        for group in groups:
            variables = {}
            for atom in group:
                pred = predicates.get(atom.predicate)
                if pred is None or len(pred.argument_types) != len(atom.arguments):
                    raise ValueError("undeclared predicate or invalid arity")
                for arg, typ in zip(atom.arguments, pred.argument_types):
                    if arg.startswith("?"):
                        if variables.setdefault(arg, typ) != typ:
                            raise ValueError("incompatible variable types")
                    elif self.entities.get(arg) != typ:
                        raise ValueError("undeclared entity or incompatible domain")
        if not self.policy.allow_assumptions and any(x.origin == "assumed" for x in (*self.assertions, *self.rules)):
            raise ValueError("context policy forbids assumptions")
        return self


class Support(StrictModel):
    conclusion: str
    rule_id: str
    premises: tuple[str, ...]


class Diagnostic(StrictModel):
    code: str
    message: str
    affected: tuple[str, ...] = ()


class InferenceReport(StrictModel):
    backend: str
    complete: bool
    atoms: tuple[Atom, ...]
    supports: tuple[Support, ...]
    seed_supports: dict[str, tuple[str, ...]]
    blocked: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    rounds: int
    # Graph consistency and assumption-based deduction never constitute proof.
    assessment: Literal["not_established", "consistent_under_assumptions"]


def _matches(rule, atoms, limit, deadline):
    index = defaultdict(list)
    bound_index = defaultdict(list)
    for atom in atoms.values():
        if time.monotonic() > deadline:
            raise NimaError("rule join budget exceeded")
        index[(atom.predicate, atom.negative)].append(atom)
        for position, value in enumerate(atom.arguments):
            bound_index[(atom.predicate, atom.negative, position, value)].append(atom)
    partial = [({}, ())]
    work = 0
    for premise in rule.premises:
        joined = []
        for bindings, supports in partial:
            # A grounded/bound argument is an exact index lookup, not a scan
            # through every fact of this predicate for every partial binding.
            # Still validate every argument, preserving repeated-variable and
            # explicit-negation semantics and alternative derivation supports.
            choices = [index[(premise.predicate, premise.negative)]]
            for position, pattern in enumerate(premise.arguments):
                value = bindings.get(pattern) if pattern.startswith("?") else pattern
                if value is not None:
                    choices.append(bound_index[(premise.predicate, premise.negative, position, value)])
            for atom in min(choices, key=len):
                work += 1
                if work > limit or time.monotonic() > deadline:
                    raise NimaError("rule join budget exceeded")
                candidate = dict(bindings)
                for pattern, value in zip(premise.arguments, atom.arguments):
                    if pattern.startswith("?"):
                        if candidate.setdefault(pattern, value) != value:
                            break
                    elif pattern != value:
                        break
                else:
                    joined.append((candidate, (*supports, atom.key)))
        partial = joined
    for bindings, premises in partial:
        conclusion = Atom(predicate=rule.conclusion.predicate, negative=rule.conclusion.negative,
                          arguments=tuple(bindings.get(a, a) for a in rule.conclusion.arguments))
        yield conclusion, Support(conclusion=conclusion.key, rule_id=rule.id, premises=premises)


def infer(snapshot: GraphSnapshot) -> InferenceReport:
    """Compute bounded closure and explicit conflicts within one immutable context."""
    from semantica.reasoning import Reasoner

    policy = snapshot.policy
    deadline = time.monotonic() + policy.timeout_seconds
    atoms = {a.atom.key: a.atom for a in snapshot.assertions if a.origin == "assumed"}
    seeds = defaultdict(list)
    for assertion in snapshot.assertions:
        if assertion.origin == "assumed":
            seeds[assertion.atom.key].append(assertion.id)
    rules = [r for r in snapshot.rules if r.origin == "assumed"]
    supports = {}
    diagnostics = []
    complete = False
    rounds = 0
    try:
        if len(atoms) > policy.max_facts:
            raise NimaError("initial fact budget exceeded")
        for rounds in range(1, policy.max_rounds + 1):
            expected = {}
            for rule in rules:
                for atom, support in _matches(rule, atoms, policy.max_matches, deadline):
                    supports[identity(support)] = support
                    expected[atom.key] = atom
                    if len(supports) > policy.max_matches:
                        raise NimaError("derivation support budget exceeded")
            additions = {k: v for k, v in expected.items() if k not in atoms}
            if len(atoms) + len(additions) > policy.max_facts:
                raise NimaError("closure fact budget exceeded")
            if not additions:
                complete = True
                break
            # Bound typed joins before invoking Semantica. Submit the actual
            # instantiated rule premises, not synthetic status assertions.
            # Keep alternative supports separately because the native API merges
            # their premises when multiple routes conclude the same fact.
            backend_rules = sorted({"IF " + " AND ".join(atoms[p].encoded() for p in s.premises)
                + " THEN " + additions[s.conclusion].encoded()
                for s in supports.values() if s.conclusion in additions})
            reasoner = Reasoner(config={"max_iterations": 1})
            results = reasoner.infer_with_results([a.encoded() for a in atoms.values()], backend_rules)
            actual = {r.conclusion for r in results}
            if actual != {a.encoded() for a in additions.values()}:
                raise NimaError("Semantica adapter conformance failure")
            atoms.update(additions)
            if time.monotonic() > deadline:
                raise NimaError("inference deadline exceeded")
    except NimaError as exc:
        diagnostics.append(Diagnostic(code="inference_incomplete", message=str(exc)))
    if not complete and not diagnostics:
        diagnostics.append(Diagnostic(code="inference_incomplete", message="round limit reached"))
    blocked = set()
    for atom in atoms.values():
        opposite = atom.model_copy(update={"negative": not atom.negative})
        if opposite.key in atoms and not atom.negative:
            blocked.update((atom.key, opposite.key))
            diagnostics.append(Diagnostic(code="explicit_contradiction", message="Both a statement and its explicit negation follow in this context.",
                                          affected=(atom.key, opposite.key)))
    # Conservative taint: a conflicted derivation is excluded downstream.
    # Independent untainted supports retain their conclusions via fixed point.
    clean = set(seeds) - blocked
    changed = True
    while changed:
        before = set(clean)
        for support in supports.values():
            if support.conclusion not in blocked and all(p in clean for p in support.premises):
                clean.add(support.conclusion)
        changed = before != clean
    blocked.update(set(atoms) - clean)
    provisional = tuple(x.id for x in (*snapshot.assertions, *snapshot.rules) if x.origin != "assumed")
    if provisional:
        diagnostics.append(Diagnostic(code="provisional_knowledge", message="Unadmitted source/model assertions and rules are excluded from trusted inference.", affected=provisional))
    unused = tuple(r.id for r in rules if not any(s.rule_id == r.id for s in supports.values()))
    if unused:
        diagnostics.append(Diagnostic(code="unmet_rule_premises", message="No complete applicable premise binding exists for these rules in the supplied context.", affected=unused))
    if not complete:
        blocked.update(atoms)
    return InferenceReport(backend="semantica-" + version("semantica") + "/horn-adapter-v1", complete=complete,
        atoms=tuple(atoms[k] for k in sorted(atoms)), supports=tuple(supports[k] for k in sorted(supports)),
        seed_supports={k: tuple(v) for k, v in seeds.items()}, blocked=tuple(sorted(blocked)), diagnostics=tuple(diagnostics), rounds=rounds,
        assessment="consistent_under_assumptions" if complete and atoms and not blocked else "not_established")
