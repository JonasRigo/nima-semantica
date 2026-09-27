"""Attempt-local, proposal-only reasoning state shared by calculation and Lean.

Ontology validity and exact grounding are NOT semantic correspondence or proof.
Only controller-installed check adapters can attach check results. Model patches
cannot remove constraints, delete history or assign acceptance status.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import Field, model_validator

from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, Record, StrictModel, VerificationResult, identity
from .okf_contracts import GraphIdentifier, OKFEdge, OKFNode, OKFReference, OKFSnapshot
from .ontology_profiles import NodeType, OntologyProfile, RelationType
from .ontology_services import OntologyService

KINDS = ("definition", "assumption", "obligation", "claim")
PROFILE = OntologyProfile(name="reasoning_state", version="1.1.0",
    node_types=tuple(NodeType(name=k, description=f"Proposed {k}; not mathematical acceptance",
        role=k if k in ("definition", "obligation") else "statement") for k in KINDS),
    relation_types=(RelationType(name="depends_on", source_types=KINDS, target_types=KINDS,
        description="Source depends on target in this attempt", necessary_dependency=True),
        RelationType(name="preserves", source_types=KINDS, target_types=KINDS,
            description="Target must preserve the source's named declared facet")))


class Anchor(StrictModel):
    source_id: GraphIdentifier
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    quotation: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def paired_offsets(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("supply both offsets or neither")
        return self


class ReasoningItem(StrictModel):
    key: GraphIdentifier
    kind: Literal["definition", "assumption", "obligation", "claim"]
    text: str = Field(min_length=1, max_length=32000)
    facets: dict[str, str] = Field(default_factory=dict, max_length=32)
    depends_on: tuple[GraphIdentifier, ...] = Field(default=(), max_length=64)
    anchors: tuple[Anchor, ...] = Field(default=(), max_length=32)


class Preservation(StrictModel):
    """Equality of declared facets only; not inference from arbitrary prose/code."""
    key: GraphIdentifier
    source: GraphIdentifier
    target: GraphIdentifier
    facet: str = Field(min_length=1, max_length=128)


class ReasoningPatch(StrictModel):
    base_revision: str
    items: tuple[ReasoningItem, ...] = Field(default=(), max_length=64)
    constraints: tuple[Preservation, ...] = Field(default=(), max_length=64)


class ReasoningState:
    KIND = "ReasoningStateRevision"
    profile = PROFILE
    item_model = ReasoningItem
    patch_model = ReasoningPatch
    task_grounded_kinds = ("definition", "obligation")

    def __init__(self, store, *, corpus_id, project_id, attempt_id, task,
                 allow_writes=False, validators=None):
        self.store, self.task = store, task
        self.scope = dict(corpus_id=corpus_id, project_id=project_id)
        self.attempt_id = attempt_id
        self.allow_writes = allow_writes
        # Operator-owned adapters, never supplied by the model-facing patch.
        self.validators: dict[str, Callable] = dict(validators or {})
        self.receipts = ExecutionReceiptService(store)
        self.root = identity(dict(attempt=attempt_id, task=task, profile=self.profile.digest, **self.scope))

    def _head(self):
        rows = [r.content for _, r in self.store.records(self.KIND, corpus_id=self.scope["corpus_id"])
                if r.project_id == self.scope["project_id"] and r.content["attempt_id"] == self.attempt_id]
        if not rows:
            return dict(attempt_id=self.attempt_id, root=self.root, revision=self.root,
                        sequence=0, items={}, versions={}, constraints={}, checks=[])
        head = max(rows, key=lambda r: r["sequence"])
        if head["root"] != self.root:
            raise ConflictError("reasoning attempt is bound to another task or ontology")
        return head

    def _write(self, head, *, items=None, constraints=None, checks=None, versions=None):
        if not self.allow_writes:
            raise PermissionError("reasoning audit writes are disabled")
        payload = dict(attempt_id=self.attempt_id, root=self.root,
            sequence=head["sequence"]+1, parent_revision=head["revision"],
            items=head["items"] if items is None else items,
            versions=head["versions"] if versions is None else versions,
            constraints=head["constraints"] if constraints is None else constraints,
            checks=head["checks"] if checks is None else checks)
        payload["revision"] = identity(payload)
        self.store.put(self._input_record())
        self.store.put(Record(kind=self.KIND, **self.scope, content=payload,
            parents=(head["revision"],)))
        return self._view(payload)

    def _input_record(self):
        return Record(kind="ReasoningInput", **self.scope,
                      content=dict(attempt_id=self.attempt_id, task=self.task))

    def _source(self, source_id):
        if source_id == "task":
            return self.task
        receipt = self.receipts.get(source_id, **self.scope)
        if receipt is None or receipt.operation_id != self.attempt_id:
            raise ValueError("source receipt is absent or outside this attempt")
        output = receipt.metadata.get("output", {})
        if receipt.status != "completed" or output.get("exit_code") != 0 or output.get("outcome") != "executed":
            raise ValueError("source must be a successful captured execution")
        return output["stdout"]

    def _validate(self, items, constraints):
        objects = {key: self.item_model.model_validate(v) for key, v in items.items()}
        if len(items) > 128 or len(constraints) > 128:
            raise ValueError("reasoning state size exceeded")
        for key, item in objects.items():
            if key != item.key or len(set(item.depends_on)) != len(item.depends_on):
                raise ValueError("invalid item identity or duplicate dependency")
            if not set(item.depends_on) <= objects.keys():
                raise ValueError("missing dependency")
            if item.kind in self.task_grounded_kinds and not any(a.source_id == "task" for a in item.anchors):
                raise ValueError("problem definitions and obligations need exact task anchors")
            for anchor in item.anchors:
                source = self._source(anchor.source_id)
                if anchor.end > len(source) or source[anchor.start:anchor.end] != anchor.quotation:
                    raise ValueError("source quotation or offsets differ")
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError("cyclic reasoning dependencies")
            if key in visited:
                return
            visiting.add(key)
            for dep in objects[key].depends_on:
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in objects:
            visit(key)
        for value in constraints.values():
            rule = Preservation.model_validate(value)
            if rule.source not in objects or rule.target not in objects:
                raise ValueError("constraint endpoint missing")
        # Existing OKF and ontology validators own graph shape/vocabulary checks.
        self._snapshot(items, "validation", constraints)

    def _ground(self, item):
        anchors = []
        for anchor in item.anchors:
            if anchor.start is None:
                source = self._source(anchor.source_id)
                start = source.find(anchor.quotation)
                if start < 0:
                    raise ValueError("quotation not found verbatim in source")
                if source.find(anchor.quotation, start + 1) >= 0:
                    raise ValueError("quotation is ambiguous; supply exact offsets or a longer unique quotation")
                anchor = anchor.model_copy(update={"start":start,"end":start+len(anchor.quotation)})
            anchors.append(anchor)
        return item.model_copy(update={"anchors":tuple(anchors)})

    def apply(self, patch):
        patch = self.patch_model.model_validate(patch.model_dump(mode="json") if hasattr(patch, "model_dump") else patch)
        with self.store.joined_transaction():
            head = self._head()
            if patch.base_revision != head["revision"]:
                raise ConflictError("stale reasoning revision")
            if len({i.key for i in patch.items}) != len(patch.items) or len({c.key for c in patch.constraints}) != len(patch.constraints):
                raise ValueError("duplicate patch identity")
            items, constraints = dict(head["items"]), dict(head["constraints"])
            versions = dict(head["versions"])
            for item in patch.items:
                try:
                    item = self._ground(item)
                except ValueError as exc:
                    raise ValueError(f"{item.key}: {exc}") from exc
                old = items.get(item.key)
                if old and old["kind"] != item.kind:
                    raise ValueError("item kind cannot change")
                items[item.key] = item.model_dump(mode="json")
                if old != items[item.key]:
                    versions[item.key] = head["sequence"] + 1
            for rule in patch.constraints:
                value = rule.model_dump(mode="json")
                if rule.key in constraints and constraints[rule.key] != value:
                    raise ValueError("existing preservation constraint cannot be changed")
                constraints[rule.key] = value
            self._validate(items, constraints)
            return self._write(head, items=items, constraints=constraints, versions=versions)

    @staticmethod
    def _fingerprint(key, items, versions):
        cache = {}
        def visit(key):
            if key not in cache:
                item = items[key]
                cache[key] = identity(dict(item=item, version=versions[key],
                    dependencies={d:visit(d) for d in item["depends_on"]}))
            return cache[key]
        return visit(key)

    def attach_check(self, *, base_revision, target, receipt_id, validator):
        """Controller-only adapter boundary. Not an agent tool or proof-by-execution."""
        if not self.allow_writes:
            raise PermissionError("reasoning audit writes are disabled")
        with self.store.joined_transaction():
            head = self._head()
            if head["revision"] != base_revision:
                raise ConflictError("stale check revision")
            if validator not in self.validators or target not in head["items"]:
                raise ValueError("unknown controller validator or target")
            receipt = self.receipts.get(receipt_id, **self.scope)
            fingerprint = self._fingerprint(target, head["items"], head["versions"])
            if (receipt is None or receipt.operation_id != self.attempt_id or receipt.status != "completed"
                    or receipt.metadata.get("reasoning_target") != fingerprint):
                raise ValueError("check receipt is not bound to this target and its current dependencies")
            result = VerificationResult.model_validate(self.validators[validator](
                self.item_model.model_validate(head["items"][target]), receipt))
            if result.target_id != fingerprint or result.protocol != validator:
                raise ValueError("validator returned another target or protocol")
            check = dict(target=target, fingerprint=fingerprint, receipt_id=receipt_id,
                         validator=validator, result=result.model_dump(mode="json"))
            return self._write(head, checks=[*head["checks"], check])

    def _view(self, head):
        violations = []
        for rule in head["constraints"].values():
            left = head["items"][rule["source"]]["facets"].get(rule["facet"])
            right = head["items"][rule["target"]]["facets"].get(rule["facet"])
            if left is None or right is None or left != right:
                violations.append(dict(constraint=rule["key"], code="declared_facet_mismatch",
                                       source=rule["source"], target=rule["target"], facet=rule["facet"]))
        checks = [{**c, "stale": c["fingerprint"] != self._fingerprint(c["target"], head["items"], head["versions"])}
                  for c in head["checks"]]
        # An encoded check does not discharge a natural-language obligation unless
        # a controller adapter separately establishes that correspondence.
        checked = {c["target"] for c in checks if not c["stale"] and c["result"]["outcome"] == "verified"
                   and c["result"]["correspondence_verified"]}
        refuted = {c["target"] for c in checks if not c["stale"] and c["result"]["outcome"] == "refuted"}
        checked -= refuted
        unresolved = [k for k, v in head["items"].items() if v["kind"] == "obligation" and k not in checked]
        return dict(revision=head["revision"], items=head["items"], constraints=head["constraints"],
            checks=checks, violations=violations, unresolved_obligations=unresolved,
            refuted=sorted(refuted), authority="proposal_only", correspondence_verified=False,
            ontology=self.profile.model_dump(mode="json"))

    def view(self):
        return self._view(self._head())

    def fingerprint(self, key):
        head = self._head()
        return self._fingerprint(key, head["items"], head["versions"])

    def finalization(self, *, revision, target, answer):
        head = self._head()
        if revision != head["revision"]:
            raise ConflictError("stale answer reasoning revision")
        view = self._view(head)
        if target not in head["items"] or head["items"][target]["text"] != answer:
            raise ValueError("final answer differs from its graph claim")
        if head["items"][target]["kind"] != "claim":
            raise ValueError("final target must be a claim")
        if view["violations"] or view["refuted"]:
            raise ValueError("represented constraints or refutations block finalization")
        if not any(v["kind"] == "obligation" for v in head["items"].values()):
            raise ValueError("extract at least one task-grounded obligation")
        reachable = set()
        def collect(key):
            if key in reachable:
                return
            reachable.add(key)
            for dep in head["items"][key]["depends_on"]:
                collect(dep)
        collect(target)
        if any(k not in reachable for k, v in head["items"].items()
               if v["kind"] in ("definition", "obligation")):
            raise ValueError("final claim omits represented task dependencies")
        # Unresolved claims can be returned only as explicitly partial results.
        return dict(status="partial", mathematically_verified=False,
                    unresolved_obligations=view["unresolved_obligations"], revision=revision)

    def _snapshot(self, items, revision, constraints=None):
        def reference(source_id):
            if source_id == "task":
                record = self._input_record()
                target_id, content = record.id, record.content
            else:
                record = next((r for _, r in self.store.records("ExecutionReceipt", corpus_id=self.scope["corpus_id"])
                    if r.project_id == self.scope["project_id"] and r.content["receipt_id"] == source_id), None)
                if record is None:
                    raise ValueError("missing evidence record")
                target_id, content = record.id, record.content
            return OKFReference(reference_kind="record", target_id=target_id,
                                **self.scope, content_hash=identity(content))
        nodes = tuple(OKFNode(node_id=identity((self.root,k)), node_type=v["kind"], **self.scope,
            properties=v, ontology_profile=self.profile.digest,
            provenance=tuple(reference(s)
                for s in sorted({a["source_id"] for a in v["anchors"]}))) for k, v in items.items())
        refs = {n.properties["key"]: n.ref for n in nodes}
        edges = tuple(OKFEdge(edge_id=identity((self.root, k, d)), relation="depends_on", **self.scope,
            source_id=refs[k], target_id=refs[d], ontology_profile=self.profile.digest)
            for k, v in items.items() for d in v["depends_on"])
        edges += tuple(OKFEdge(edge_id=identity((self.root,"preserves",k)),relation="preserves",**self.scope,
            source_id=refs[r["source"]],target_id=refs[r["target"]],properties=r,ontology_profile=self.profile.digest)
            for k,r in (constraints or {}).items())
        snapshot = OKFSnapshot(snapshot_id=identity((self.root, revision)), **self.scope,
            graph_revision=self.store.graph_revision(**self.scope), nodes=nodes, edges=edges,
            ontology_profile=self.profile.digest, metadata={"attempt_id":self.attempt_id,
                "reasoning_revision":revision,"authority":"proposal_only"})
        if not OntologyService().validate_snapshot(snapshot, profile=self.profile).valid:
            raise ValueError("reasoning graph violates ontology")
        return snapshot

    def snapshot(self):
        head = self._head()
        return self._snapshot(head["items"], head["revision"], head["constraints"])
