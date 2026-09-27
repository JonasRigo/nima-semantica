"""Private mathematical attempt state. No research-graph admission path.

The ontology governs representation, not mathematical truth. Semantica derives
workflow consequences from controller-observed facts, never from prose claims.
"""
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, ConflictError, identity
from .ontology_profiles import NodeType, OntologyProfile, RelationType
from .reasoning_state import Anchor, ReasoningItem, ReasoningState
from .reasoning_kernel import Atom, Assertion, ContextPolicy, GraphSnapshot, HornRule, Predicate, infer


KINDS = ("requirement", "object", "assumption", "convention", "obligation", "plan_step", "context_need", "context_assessment", "observation", "step", "claim", "candidate")
PROFILE = OntologyProfile(name="mathematical_attempt", version="1.1.0",
    node_types=tuple(NodeType(name=k, description="Attempt-local proposed " + k,
        role="obligation" if k == "obligation" else "definition" if k == "object" else "statement") for k in KINDS),
    relation_types=(RelationType(name="depends_on", source_types=KINDS, target_types=KINDS,
        description="Source requires target; does not certify either proposition", necessary_dependency=True),
        RelationType(name="addresses",source_types=("obligation","candidate"),target_types=("requirement","obligation"),
            description="Proposed task coverage, not semantic discharge"),
        RelationType(name="contradicts",source_types=KINDS,target_types=KINDS,
            description="Explicit proposed incompatibility; conservatively blocks both endpoints until resolved")),
    required_node_types=("requirement", "object", "obligation"),
    instructions="Represent every requested mathematical target, objects, assumptions and derivation obligations. "
        "Presentation obligations cannot replace mathematical ones. All mathematical content is proposed. "
        "Each step declares premises, operation, conclusion and side conditions. Checks have exact encoded scope.")


class EncodedCheck(StrictModel):
    """Small existing native checker vocabulary; arbitrary mathematics remains admissible without it."""
    operation: Literal["integrate", "differentiate", "simplify", "series", "solve"]
    expression: str = Field(min_length=1, max_length=4000)
    candidate: str = Field(min_length=1, max_length=4000)
    variable: str = Field(default="x", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    symbol_domain: Literal["real", "positive", "complex"] = "real"
    expansion_point: int = 0
    expansion_order: int = Field(default=5, ge=1, le=20)
    correspondence: str = Field(min_length=1, max_length=4000,
        description="Proposed explanation of how this encoding addresses the obligation, including limitations; not acceptance.")


class MathItem(ReasoningItem):
    kind: Literal["requirement", "object", "assumption", "convention", "obligation", "plan_step", "context_need", "context_assessment", "observation", "step", "claim", "candidate"]
    facets: dict[str,str] = Field(default_factory=dict,max_length=0,description="Retired field; omit. Use typed mathematical attributes.")
    category: Literal["mathematical", "presentation"] = "mathematical"
    domain: str = Field(default="unknown", min_length=1, max_length=2000)
    shape: str = Field(default="unknown", max_length=1000)
    units: str = Field(default="unknown", max_length=1000)
    free_indices: tuple[str, ...] = Field(default=(), max_length=32)
    summed_indices: tuple[str, ...] = Field(default=(), max_length=32)
    scope: str = Field(min_length=1, max_length=4000, description="Scope of this exact item: domain, quantifiers, applicability and limitations.")
    introduced: bool = Field(default=False, description="Only assumptions/conventions may be introduced; they need justification and make conclusions conditional.")
    justification: str = Field(default="", max_length=6000)
    side_conditions: tuple[str, ...] = Field(default=(), max_length=32,
        description="Existing obligation KEYS only, also included in depends_on. Never prose.")
    addresses: tuple[str, ...] = Field(default=(), max_length=64,
        description="For obligations: REQUIREMENT keys also in depends_on. For controller-bound candidates: OBLIGATION keys. Empty for all other kinds.")
    contradicts: tuple[str, ...] = Field(default=(), max_length=64)
    check: EncodedCheck | None = Field(default=None,description="Only obligation nodes may contain an optional exact native-check encoding.")
    correction_reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def mathematical_shape(self):
        if self.facets:
            raise ValueError("free-form preservation facets are retired; use typed objects, dependencies and obligations")
        if set(self.free_indices) & set(self.summed_indices):
            raise ValueError("an index cannot be both free and summed in the same represented expression")
        if not self.scope.strip():
            raise ValueError("every mathematical item needs explicit scope")
        if self.kind == "object" and not self.domain.strip():
            raise ValueError("object needs a domain, or explicit unknown with explanation")
        if self.kind in ("plan_step", "context_need", "context_assessment", "observation", "step", "claim", "candidate") and not self.depends_on:
            raise ValueError("derived items need premises")
        if self.kind in ("plan_step", "step") and not self.justification.strip():
            raise ValueError("step needs an operation/justification; prose remains unverified")
        if self.kind in ("requirement", "obligation", "candidate") and not self.text.strip():
            raise ValueError("empty mathematical content")
        if self.check is not None and self.kind != "obligation":
            raise ValueError("native checks bind obligations only")
        if self.introduced and self.kind not in ("assumption", "convention"):
            raise ValueError("only explicit additional assumptions or conventions may be introduced")
        return self


class MathPatch(StrictModel):
    base_revision: str
    items: tuple[MathItem, ...] = Field(min_length=1, max_length=64)
    # Kept empty at the shared storage interface; mathematical rules are pinned.
    constraints: tuple = Field(default=(), max_length=0)


PREDICATES = tuple(Predicate(name=p, argument_types=("Item",)) for p in
    ("Proposed", "Open", "Blocked", "NativeEvidence", "EvidencePresent")) + (
    Predicate(name="Depends", argument_types=("Item", "Item")),
    Predicate(name="Contradicts", argument_types=("Item", "Item")))
RULES = (
    HornRule(name="unresolved_dependency", premises=(Atom(predicate="Depends", arguments=("?x", "?y")),
        Atom(predicate="Open", arguments=("?y",))), conclusion=Atom(predicate="Open", arguments=("?x",)),
        origin="assumed", justification="Controller policy: unresolved prerequisites remain unresolved downstream."),
    HornRule(name="blocked_dependency", premises=(Atom(predicate="Depends", arguments=("?x", "?y")),
        Atom(predicate="Blocked", arguments=("?y",))), conclusion=Atom(predicate="Blocked", arguments=("?x",)),
        origin="assumed", justification="Controller policy: represented conflicts block dependents."),
    HornRule(name="recorded_native_evidence", premises=(Atom(predicate="NativeEvidence", arguments=("?x",)),),
        conclusion=Atom(predicate="EvidencePresent", arguments=("?x",)), origin="assumed",
        justification="A native receipt establishes evidence presence, not semantic discharge."),
    HornRule(name="declared_conflict_source",premises=(Atom(predicate="Contradicts",arguments=("?x","?y")),),
        conclusion=Atom(predicate="Blocked",arguments=("?x",)),origin="assumed",
        justification="Explicit incompatibility is conservatively blocking, not a proof of falsehood."),
    HornRule(name="declared_conflict_target",premises=(Atom(predicate="Contradicts",arguments=("?x","?y")),),
        conclusion=Atom(predicate="Blocked",arguments=("?y",)),origin="assumed",
        justification="Explicit incompatibility is conservatively blocking, not a proof of falsehood."),
)
POLICY_DIGEST = identity(dict(profile=PROFILE, predicates=PREDICATES, rules=RULES))


class MathReasoningState(ReasoningState):
    KIND = "MathAttemptRevision"
    profile = PROFILE
    item_model = MathItem
    patch_model = MathPatch
    task_grounded_kinds = ("requirement",)

    def __init__(self, *args, sources=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sources = dict(sources or {})
        self.root = identity(dict(base=self.root, sources=self.sources, policy=POLICY_DIGEST))

    def _source(self, source_id):
        if source_id in self.sources:
            return self.sources[source_id]
        if source_id != "task":
            receipt = self.receipts.get(source_id, **self.scope)
            if receipt and receipt.operation_id == self.attempt_id and receipt.stage == "calculation_retrieval" and receipt.status == "completed":
                from .models import canonical
                output = receipt.metadata.get("output", {})
                if output.get("source_text") != canonical(output.get("passages", [])).decode():
                    raise ValueError("Retrieval receipt source text differs from captured passages.")
                return output["source_text"]
        return super()._source(source_id)

    def _input_record(self):
        record = super()._input_record()
        return record.model_copy(update={"kind":"MathAttemptInput", "content":{
            **record.content, "sources":self.sources, "policy_digest":POLICY_DIGEST}})

    def _snapshot(self, items, revision, constraints=None):
        # This state is an audit object, not an OKF research snapshot. Validate
        # the same vocabulary directly without creating research graph objects.
        types = {n.name for n in self.profile.node_types}
        if any(v["kind"] not in types for v in items.values()):
            raise ValueError("undeclared mathematical node kind")
        missing = set(self.profile.required_node_types) - {v["kind"] for v in items.values()}
        if missing:
            raise ValueError("missing mathematical ontology types: " + ", ".join(sorted(missing)))
        edges = [{"source":k,"relation":relation,"target":target}
            for k,v in items.items() for relation,field in (("depends_on","depends_on"),("addresses","addresses"),("contradicts","contradicts"))
            for target in v[field]]
        return dict(attempt_id=self.attempt_id, revision=revision, policy_digest=POLICY_DIGEST,
                    items=items, edges=edges, authority="internal_attempt_state", publishable=False)

    def snapshot(self):
        head = self._head()
        return self._snapshot(head["items"], head["revision"])

    def _validate(self, items, constraints):
        super()._validate(items, constraints)
        for key, value in items.items():
            item = MathItem.model_validate(value)
            if any(k not in items or k==key for k in item.contradicts):
                raise ValueError("contradiction endpoints must name other represented items")
            if item.kind == "object" or (item.kind in ("assumption", "convention") and not item.introduced):
                if not item.anchors:
                    raise ValueError(f"{key}: source-defined objects, conventions and assumptions need exact anchors")
            if item.introduced and not item.justification:
                raise ValueError(f"{key}: introduced assumptions/conventions require justification and remain conditional")
            if item.kind == "obligation":
                if not item.addresses or any(items.get(k, {}).get("kind") != "requirement" for k in item.addresses):
                    raise ValueError(f"{key}: obligation.addresses must contain REQUIREMENT keys, not objects, assumptions or other obligations")
                if not set(item.addresses) <= set(item.depends_on):
                    raise ValueError(f"{key}: obligation dependencies must include addressed requirements")
                if item.category == "mathematical" and not any(items[d]["kind"] == "object" for d in item.depends_on):
                    raise ValueError(f"{key}: mathematical obligation.depends_on must include an OBJECT key as well as addressed requirements")
            elif item.kind == "candidate":
                if not item.addresses or any(items.get(k, {}).get("kind") != "obligation" for k in item.addresses):
                    raise ValueError(f"{key}: candidate.addresses must contain OBLIGATION keys, not requirement keys")
            elif item.addresses:
                raise ValueError("only obligations and candidates have addresses links")
            if any(items.get(k, {}).get("kind") != "obligation" or k not in item.depends_on for k in item.side_conditions):
                raise ValueError(f"{key}: side_conditions must name obligation dependencies, not untracked prose")
            if item.kind == "claim" and not any(items[d]["kind"] == "step" for d in item.depends_on):
                raise ValueError("derived claim must identify its producing step")
        requirements = {k for k,v in items.items() if v["kind"] == "requirement" and v["category"] == "mathematical"}
        if not requirements:
            raise ValueError("formatting-only extraction is not mathematical coverage")
        covered = {r for v in items.values() if v["kind"] == "obligation" and v["category"] == "mathematical" for r in v["addresses"]}
        if requirements - covered:
            raise ValueError("every mathematical requirement needs a mathematical obligation")

    def apply(self, patch):
        patch = MathPatch.model_validate(patch.model_dump(mode="json") if hasattr(patch, "model_dump") else patch)
        with self.store.joined_transaction():
            head = self._head()
            for item in patch.items:
                old = head["items"].get(item.key)
                try:
                    grounded = self._ground(item).model_dump(mode="json")
                except ValueError as exc:
                    raise ValueError(f"{item.key}: {exc}") from exc
                if old and old != grounded:
                    if not item.correction_reason.strip():
                        raise ValueError("correction requires an explicit reason; history and stale checks are retained")
                    if old["category"] != item.category:
                        raise ValueError("a mathematical obligation cannot be downgraded to presentation")
                    if item.kind in ("requirement", "object", "assumption", "convention") and not item.anchors:
                        raise ValueError("interpretation corrections require source anchors")
            # No delete operation; same-key revisions retain old immutable records.
            return super().apply(patch)

    def _view(self, head):
        view = super()._view(head)
        ids = {k:"n" + identity(k)[:24] for k in head["items"]}
        assertions = []
        def fact(predicate, *keys):
            assertions.append(Assertion(atom=Atom(predicate=predicate, arguments=tuple(ids[k] for k in keys)),
                origin="assumed", justification="Controller-observed attempt metadata; not a mathematical truth assertion."))
        for key, item in head["items"].items():
            fact("Proposed", key)
            for dependency in item["depends_on"]:
                fact("Depends", key, dependency)
            for other in item["contradicts"]:
                fact("Contradicts",key,other)
            if item["kind"] == "obligation" and key in view["unresolved_obligations"]:
                fact("Open", key)
        for key in view["refuted"]:
            fact("Blocked", key)
        for key in {c["target"] for c in view["checks"] if not c["stale"]}:
            fact("NativeEvidence", key)
        report = infer(GraphSnapshot(graph_id="math_attempt", context_id="private_attempt",
            policy=ContextPolicy(allow_assumptions=True), entities={v:"Item" for v in ids.values()},
            predicates=PREDICATES, assertions=tuple(assertions), rules=RULES))
        if not report.complete:
            raise ValueError("Semantica inference incomplete; no state transition may be accepted")
        reverse = {v:k for k,v in ids.items()}
        consequences = {p:sorted(reverse[a.arguments[0]] for a in report.atoms if a.predicate == p)
                        for p in ("Open", "Blocked", "EvidencePresent")}
        return {**view, "authority":"internal_attempt_state", "publishable":False,
            "policy_digest":POLICY_DIGEST, "inference":report.model_dump(mode="json"),
            "consequences":consequences, "assessments":{
                "structure":"valid" if head["items"] else "not_extracted",
                "logic":"blocked" if consequences["Blocked"] else "consistent_under_declared_rules",
                "evidence_obligations":consequences["EvidencePresent"],
                "source_correspondence":"unresolved"}}

    def finalization(self, *, revision, target, answer, allow_partial=False):
        head = self._head()
        if revision != head["revision"]:
            raise ConflictError("stale finalization revision")
        view = self._view(head)
        item = head["items"].get(target, {})
        if item.get("kind") != "candidate" or item["text"] != answer:
            raise ValueError("answer must equal the current receipt-bound candidate")
        if view["consequences"]["Blocked"]:
            raise ValueError("represented refutations block finalization")
        reachable = set()
        def walk(k):
            if k not in reachable:
                reachable.add(k)
                for d in head["items"][k]["depends_on"]:
                    walk(d)
        walk(target)
        required = {k for k,v in head["items"].items() if v["kind"] in
                    ("requirement", "object", "assumption", "convention", "obligation")}
        if required - reachable:
            raise ValueError("candidate omits represented problem dependencies: " + ", ".join(sorted(required-reachable)))
        obligations = {k for k,v in head["items"].items() if v["kind"] == "obligation"}
        if set(item["addresses"]) != obligations:
            raise ValueError("candidate must address every represented obligation")
        if not any(head["items"][k]["kind"] == "step" for k in reachable):
            raise ValueError("candidate has no represented derivation step")
        if not allow_partial and (view["unresolved_obligations"] or not view["correspondence_verified"]):
            raise ValueError("required mathematical obligations or source correspondence unresolved; continue or explicitly return_partial")
        return dict(status="partial", mathematically_verified=False, revision=revision,
            unresolved_obligations=view["unresolved_obligations"], assessments=view["assessments"],
            limitations=["Extraction fidelity and source correspondence are not independently established."] +
                ["Introduced " + v["kind"] + ": " + v["text"] for v in head["items"].values() if v["introduced"]])
