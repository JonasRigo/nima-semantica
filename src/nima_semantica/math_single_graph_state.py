"""Small private calculation graph for one continuing mathematical agent.

Graph status is provenance status, never a proof-of-physics certificate.
"""
from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from typing import Any

from .calculation_values import equivalent, path_value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        default=str, separators=(",", ":")).encode()).hexdigest()


def _proper_rational(value: str) -> bool:
    if len(value) > 80:
        return False
    try:
        fraction = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return False
    return 0 < abs(fraction) < 1


def _leaves(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("calculation output keys must be nonempty strings")
            path = f"{prefix}.{key}" if prefix else key
            yield from _leaves(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, f"{prefix}.{index}" if prefix else str(index))
    else:
        yield prefix or "$", value


class SingleCalculationGraph:
    """Append-only nodes; later steps may supersede but never erase attempts."""

    def __init__(self, task: str, task_facts: tuple[Any, ...], required_paths: tuple[str, ...], indexed_sets=()):
        self.required_paths = required_paths
        self.indexed_sets = [item.model_dump(mode="json") for item in indexed_sets]
        self.nodes: dict[str, dict] = {"task": {"id": "task", "kind": "task", "value": task,
            "status": "task_given", "depends_on": [], "origin": {"kind": "harness"}}}
        self.order = ["task"]
        self.obligations: dict[str, dict] = {}
        self.receipts: dict[str, dict] = {}
        self.compiled_plans: dict[str, dict] = {}
        self._serial = 0
        for fact in task_facts:
            payload = fact.model_dump(mode="json") if hasattr(fact, "model_dump") else dict(fact)
            identifier = "fact:" + payload["fact_id"]
            self.nodes[identifier] = {"id": identifier, "kind": "task_fact", "value": payload,
                "status": "task_given", "depends_on": ["task"], "origin": {"kind": "harness"}}
            self.order.append(identifier)
        for spec in self.indexed_sets:
            identifier = "index:" + spec["set_id"]
            self.nodes[identifier] = {"id": identifier, "kind": "indexed_set", "value": spec,
                "status": "task_given", "depends_on": ["fact:" + spec["fact_id"]],
                "origin": {"kind": "harness"}}
            self.order.append(identifier)

    def _next(self) -> str:
        self._serial += 1
        return f"n{self._serial}"

    def _parents(self, identifiers):
        if not isinstance(identifiers, (list, tuple)) or any(not isinstance(item, str) for item in identifiers):
            raise ValueError("dependencies must be a list of graph node IDs")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate dependency")
        if any(identifier not in self.nodes for identifier in identifiers):
            raise ValueError("unknown or cross-graph dependency")
        return list(identifiers)

    def _precise_parents(self, identifiers):
        parents = self._parents(identifiers)
        if any(self.nodes[identifier].get("atomic_nodes") for identifier in parents):
            raise ValueError("use an atomic child node, not a proposal bundle, as a dependency")
        return parents

    def _put(self, kind, value, status, parents, origin, *, identifier=None, **extra):
        identifier = identifier or self._next()
        if identifier in self.nodes:
            raise ValueError("graph node already exists")
        node = {"id": identifier, "kind": kind, "value": value, "status": status,
            "depends_on": self._parents(parents), "origin": origin, **extra}
        self.nodes[identifier] = node
        self.order.append(identifier)
        return node

    def record_execution(self, source: str, output: dict, receipt_id: str, parents=(), purpose="",
            *, evidence_eligible=True):
        parents = self._precise_parents(parents)
        if receipt_id in self.receipts:
            raise ValueError("execution receipt already recorded in this graph")
        source_sha256 = hashlib.sha256(source.encode()).hexdigest()
        status = "observed" if output.get("outcome") == "executed" and output.get("exit_code") == 0 else "failed"
        raw = output.get("stdout", "") if status == "observed" else ""
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            value = None
        if value is None and raw.strip() != "null":
            status = "exploratory" if status == "observed" else status
        if not evidence_eligible and status == "observed":
            status = "exploratory"
        if status == "observed":
            leaves = list(_leaves(value))
            if len(leaves) > 128:
                raise ValueError("calculation output has too many atomic observations")
        self.receipts[receipt_id] = {"kind": "execution", "source": source, "output": output,
            "source_sha256": source_sha256}
        experiment = self._put("experiment", {"purpose": str(purpose)[:500],
            "source_sha256": source_sha256}, "executed" if status in {"observed", "exploratory"} else "failed",
            parents or ["task"], {"kind": "execution", "receipt_id": receipt_id},
            identifier="experiment:" + receipt_id)
        if status == "observed":
            nodes = [self._put("observation", item, status, [experiment["id"]],
                {"kind": "execution", "receipt_id": receipt_id, "output_path": path,
                    "source_sha256": source_sha256}) for path, item in leaves]
        else:
            nodes = [self._put("execution_attempt", {"outcome": output.get("outcome"),
                "exit_code": output.get("exit_code"), "stderr": str(output.get("stderr", ""))[-2000:]},
                status, [experiment["id"]], {"kind": "execution", "receipt_id": receipt_id,
                    "source_sha256": source_sha256})]
        return nodes

    def stage_compiled_plan(self, plan: dict, receipt_id: str, purpose=""):
        """Record every proposed operation before its controlled source is executed."""
        if receipt_id in self.compiled_plans or receipt_id in self.receipts:
            raise ValueError("calculation plan or receipt already recorded in this graph")
        experiment = self._put("experiment", {"purpose": str(purpose)[:500],
            "source_sha256": plan["source_sha256"], "compiler": "atomic_sympy_v1"},
            "proposed", ["task"], {"kind": "controller_compiled", "receipt_id": receipt_id},
            identifier="experiment:" + receipt_id)
        local_nodes = {}
        for step in plan["steps"]:
            parents = list(dict.fromkeys([experiment["id"],
                *[local_nodes[arg]["id"] for arg in step["args"]], *step["provenance"]]))
            local_nodes[step["id"]] = self._put("calculation_plan_step", step,
                "proposed", parents, {"kind": "controller_compiled",
                    "receipt_id": receipt_id, "source_sha256": plan["source_sha256"],
                    "operation_id": step["id"]}, meaning=step["meaning"])
        self.compiled_plans[receipt_id] = {"plan": plan,
            "experiment_id": experiment["id"], "nodes": local_nodes}
        return local_nodes

    def record_compiled_execution(self, plan: dict, output: dict, receipt_id: str, purpose=""):
        """Attach exact observations to an already registered executable graph."""
        if receipt_id in self.receipts:
            raise ValueError("execution receipt already recorded in this graph")
        if receipt_id not in self.compiled_plans:
            self.stage_compiled_plan(plan, receipt_id, purpose)
        staged = self.compiled_plans[receipt_id]
        if staged["plan"] != plan:
            raise ValueError("compiled source differs from staged calculation graph")
        def exploratory():
            self.receipts[receipt_id] = {"kind": "compiled_execution", "source": plan["source"],
                "output": output, "source_sha256": plan["source_sha256"]}
            status = "failed" if output.get("outcome") != "executed" or output.get("exit_code") != 0 else "exploratory"
            return [self._put("execution_attempt", {"outcome": output.get("outcome"),
                "exit_code": output.get("exit_code"), "stderr": str(output.get("stderr", ""))[-2000:]},
                status, [staged["experiment_id"]], {"kind": "controller_compiled",
                    "receipt_id": receipt_id, "source_sha256": plan["source_sha256"]})]
        if output.get("outcome") != "executed" or output.get("exit_code") != 0:
            return exploratory()
        try:
            payload = json.loads(output.get("stdout", ""))
        except (TypeError, ValueError):
            return exploratory()
        expected = {step["id"] for step in plan["steps"]}
        values = payload.get("steps") if isinstance(payload, dict) else None
        outputs = payload.get("outputs") if isinstance(payload, dict) else None
        if (not isinstance(values, dict) or set(values) != expected or
                not isinstance(outputs, dict) or set(outputs) != set(plan["outputs"]) or
                any(not isinstance(value, str) or len(value) > 32000 for value in values.values()) or
                any(outputs[path] != values[step_id] for path, step_id in plan["outputs"].items())):
            return exploratory()
        reused = plan.get("reuse_identities", {})
        # Only the controller constructs this identity table from executed local
        # definitions. Re-execution must reproduce every exact observation.
        if any(identifier not in values or node_id not in self.nodes or
                values[identifier] != self.nodes[node_id]["value"]
                for identifier, node_id in reused.items()):
            return exploratory()
        self.receipts[receipt_id] = {"kind": "compiled_execution", "source": plan["source"],
            "output": output, "source_sha256": plan["source_sha256"]}
        local_nodes = {}
        task_dependent, structural_scalar = {}, {}
        indexed_lineage = set()
        by_id = {step["id"]: step for step in plan["steps"]}
        pending = [step["id"] for step in plan["steps"] if step.get("index_scope") or step.get("index_sum")]
        while pending:
            current = pending.pop()
            if current not in indexed_lineage:
                indexed_lineage.add(current)
                pending.extend(by_id[current]["args"])
        indexed_descendants = set()
        for step in plan["steps"]:
            if step.get("index_scope") or step.get("index_sum") or any(arg in indexed_descendants for arg in step["args"]):
                indexed_descendants.add(step["id"])
        indexed_lineage.update(indexed_descendants)
        for step in plan["steps"]:
            identifier, op = step["id"], step["op"]
            args = step["args"]
            parents = list(dict.fromkeys([staged["nodes"][identifier]["id"],
                *[local_nodes[arg]["id"] for arg in args], *step["provenance"]]))
            input_step = op in {"symbol", "integer", "rational", "imaginary_unit"}
            structural = op == "imaginary_unit" or (op == "integer" and step.get("value") in (0, 1))
            task_dependent[identifier] = (op == "symbol" and bool(step["provenance"])) or any(
                task_dependent[arg] for arg in args)
            structural_scalar[identifier] = structural or (op in {
                "add", "subtract", "multiply", "divide", "power", "negative", "simplify", "sum"}
                and bool(args) and all(structural_scalar[arg] for arg in args))
            if identifier in reused:
                local_nodes[identifier] = self.nodes[reused[identifier]]
                continue
            physical_application = step.get("application") == "physical_rule"
            inferred_application = (
                op == "multiply" and len(args) == 2 and any(
                    structural_scalar[arg] and values[arg] not in {"1", "-1"} and
                    task_dependent[args[1 - index]]
                    for index, arg in enumerate(args))
            )
            reciprocal_application = (op == "divide" and len(args) == 2 and
                task_dependent[args[0]] and structural_scalar[args[1]] and
                _proper_rational(values[args[1]]))
            duplicate_derived = (op in {"add", "sum"} and len(set(args)) != len(args) and
                any(args.count(arg) > 1 and task_dependent[arg] and
                    local_nodes[arg]["operation"] not in {"symbol", "integer", "rational", "imaginary_unit"}
                    for arg in set(args)))
            application_reason = ("declared_physical_rule" if physical_application else
                "structural_scalar_applied_to_task_quantity" if inferred_application else
                "structural_reciprocal_applied_to_task_quantity" if reciprocal_application else
                "duplicate_derived_contribution" if duplicate_derived else None)
            if plan.get("typed_applicability") and not physical_application and identifier not in indexed_lineage:
                # Arithmetic patterns alone do not assert a domain premise.
                # Explicit physical_rule and indexed cardinality checks remain.
                application_reason = None
            task_symbol = (op == "symbol" and bool(step["provenance"]) and
                all(self.nodes[parent]["status"] == "task_given" for parent in step["provenance"]) and
                any(re.search(r"\b" + re.escape(step["value"]) + r"\b",
                    json.dumps(self.nodes[parent]["value"])) for parent in step["provenance"]))
            physical_input_premise = bool(step.get("declared_physical_input_premise"))
            raw_definition = step.get("raw_definition")
            provisional = input_step and (physical_input_premise or not (structural or task_symbol or raw_definition))
            node = self._put("calculation_input" if input_step else "calculation_operation",
                values[identifier], "provisional" if provisional else "observed", parents,
                {"kind": "controller_compiled", "receipt_id": receipt_id,
                    "source_sha256": plan["source_sha256"], "operation_id": identifier,
                    "proposal_step_id": step.get("proposal_step_id", identifier),
                    "declared_physical_input_premise": physical_input_premise,
                    "index_scope": step.get("index_scope"), "index_sum": step.get("index_sum"),
                    "index_count_of": step.get("index_count_of", [])},
                operation=op, meaning=step["meaning"], raw_definition=raw_definition,
                application=step.get("application", "algebraic"),
                application_reason=application_reason,
                declared_value=step.get("value") if input_step else None)
            local_nodes[identifier] = node
            if plan.get("typed_applicability") and re.search(r"\b(?:zoo|nan|oo)\b", values[identifier]):
                self.obligations[node["id"]] = {"root_id": node["id"], "status": "open",
                    "reason": "nonfinite calculation result requires inspection of its operands"}
            elif provisional:
                self.obligations[node["id"]] = {"root_id": node["id"], "status": "open",
                    "reason": ("declared physical input premise requires independent support" if physical_input_premise
                        else "introduced calculation input requires provenance or independent support")}
            elif application_reason:
                self.obligations[node["id"]] = {"root_id": node["id"], "status": "open",
                    "reason": "physical applicability of this operation requires independent support",
                    "application_reason": application_reason}
        staged["observed_nodes"] = {key: node["id"] for key, node in local_nodes.items()}
        result_nodes = {}
        for path, step_id in plan["outputs"].items():
            parent = local_nodes[step_id]
            missing_substitutions = []
            for fact in (node["value"] for node in self.nodes.values() if node["kind"] == "task_fact"):
                if path not in fact.get("required_paths", ()):
                    continue
                for source, target in (fact.get("output_substitutions") or {}).items():
                    if re.search(r"\b" + re.escape(source) + r"\b", values[step_id]):
                        missing_substitutions.append({"fact_id": fact["fact_id"],
                            "source": source, "target": target})
            result_nodes[path] = self._put("calculation_output", values[step_id],
                "provisional" if missing_substitutions else "observed",
                [parent["id"]], {"kind": "controller_compiled", "receipt_id": receipt_id,
                    "source_sha256": plan["source_sha256"], "output_path": path},
                missing_substitutions=missing_substitutions)
            if missing_substitutions:
                node = result_nodes[path]
                self.obligations[node["id"]] = {"root_id": node["id"], "status": "open",
                    "reason": "harness-declared output substitution is incomplete",
                    "missing_substitutions": missing_substitutions}
        return {"steps": local_nodes, "outputs": result_nodes}

    def record_source(self, region_id: str, text: str, revision: str | None = None):
        text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        for node in self.nodes.values():
            if (node["kind"] == "source_text" and node["origin"]["region_id"] == region_id
                    and node["origin"]["revision"] == revision):
                if node["origin"]["text_sha256"] != text_sha256:
                    raise ValueError("source region changed within the same revision")
                return node
        return self._put("source_text", text, "observed_text", ["task"],
            {"kind": "source_region", "region_id": region_id, "revision": revision,
                "text_sha256": text_sha256})

    def add_step(self, *, kind: str, statement: str, value: Any, depends_on,
            supersedes: str | None = None):
        if kind not in {"assumption", "hypothesis", "exploration", "definition", "derivation", "claim"}:
            raise ValueError("unsupported graph step kind")
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 1200:
            raise ValueError("one concise nonempty statement required")
        parents = self._precise_parents(depends_on)
        premise_kind = kind in {"assumption", "hypothesis", "exploration"}
        if not premise_kind and not parents:
            raise ValueError("non-assumption step requires exact dependencies")
        if supersedes is not None and supersedes not in self.nodes:
            raise ValueError("unknown superseded node")
        if premise_kind and isinstance(value, (dict, list)):
            raise ValueError("record one scalar hypothesis or exploration per step; separate independent premises")
        leaves = list(_leaves(value)) if isinstance(value, (dict, list)) else []
        if isinstance(value, (dict, list)) and not leaves:
            raise ValueError("proposal bundle must contain at least one atomic value")
        if len(leaves) > 128:
            raise ValueError("mathematical step has too many atomic values")
        # A model-authored derivation is still a proposal. Naming task facts as
        # parents establishes context, not the truth of the asserted operation.
        node = self._put(kind, value, "provisional", parents or ["task"],
            {"kind": "agent_proposal"},
            statement=statement, supersedes=supersedes)
        if isinstance(value, (dict, list)):
            node["atomic_nodes"] = {}
            for path, item in leaves:
                child = self._put("step_value", item, "provisional", [node["id"]],
                    {"kind": "agent_proposal", "proposal_id": node["id"],
                        "output_path": path}, statement=f"Proposed value for {path}")
                node["atomic_nodes"][path] = child["id"]
                self.obligations[child["id"]] = {"root_id": child["id"], "status": "open",
                    "reason": "agent-proposed output requires independent matching evidence"}
        else:
            self.obligations[node["id"]] = {"root_id": node["id"], "status": "open",
                "reason": "agent-proposed step requires independent matching evidence"}
        return node

    def _roots(self, identifier: str, seen=None):
        seen = set() if seen is None else seen
        if identifier in seen:
            return set()
        seen.add(identifier)
        node = self.nodes[identifier]
        roots = {identifier} if identifier in self.obligations else set()
        for parent in node["depends_on"]:
            roots.update(self._roots(parent, seen))
        return roots

    def _open_roots(self, identifier: str):
        return {root for root in self._roots(identifier)
            if self.obligations[root]["status"] == "open"}

    def _has_evidence(self, identifier: str, seen=None):
        seen = set() if seen is None else seen
        if identifier in seen:
            return False
        seen.add(identifier)
        node = self.nodes[identifier]
        if node["kind"] in {"observation", "source_text", "calculation_operation", "calculation_output"}:
            return True
        return any(self._has_evidence(parent, seen) for parent in node["depends_on"])

    def _has_qualified_evidence(self, identifier: str):
        if self._has_evidence(identifier):
            return True
        return any(self.obligations[root]["status"] == "agent_supported_not_verified"
            for root in self._roots(identifier))

    def substantiate(self, root_id: str, evidence_id: str, rationale: str):
        if root_id not in self.obligations:
            raise ValueError("unknown or non-provisional proposal")
        if self.nodes[root_id].get("application_reason"):
            raise ValueError("operation applicability requires a task/source-grounded claim")
        if self.obligations[root_id]["status"] != "open":
            raise ValueError("proposal has already been substantiated")
        if evidence_id not in self.nodes:
            raise ValueError("unknown or cross-graph evidence node")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1200:
            raise ValueError("concise applicability rationale required")
        if root_id == evidence_id or root_id in self._roots(evidence_id):
            raise ValueError("downstream consequences cannot substantiate their own premise")
        evidence = self.nodes[evidence_id]
        if evidence["status"] in {"failed", "exploratory", "provisional", "conditional"}:
            raise ValueError("evidence is failed or still conditional")
        if not self._has_evidence(evidence_id) or self._open_roots(evidence_id):
            raise ValueError("independent source or calculation evidence required")
        if not equivalent(self.nodes[root_id]["value"], evidence["value"]):
            raise ValueError("evidence value differs from the hypothesis; record a matching interpreted step")
        support = self._put("substantiation", self.nodes[root_id]["value"],
            "agent_supported_not_verified", [evidence_id], {"kind": "agent_review",
                "target_hypothesis_id": root_id}, statement=rationale)
        self.obligations[root_id] = {"root_id": root_id,
            "status": "agent_supported_not_verified", "support_node_id": support["id"],
            "evidence_id": evidence_id,
            "reason": "independent graph lineage claimed; mathematical applicability is not certified"}
        return support

    def substantiate_application(self, root_id: str, evidence_id: str, rationale: str,
            method_source_id: str | None = None, method_quote: str | None = None):
        """Record a source/task-grounded applicability claim, never a physics certificate."""
        root = self.nodes.get(root_id)
        obligation = self.obligations.get(root_id)
        if root is None or root["kind"] != "calculation_operation" or not root.get("application_reason"):
            raise ValueError("unknown or non-applicability operation")
        if obligation["status"] != "open":
            raise ValueError("operation applicability already supported")
        evidence = self.nodes.get(evidence_id)
        if evidence is None or evidence["kind"] not in {"task", "task_fact", "source_text", "calculation_output"}:
            raise ValueError("operation applicability needs an exact task fact, source passage or independent calculation output")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1200:
            raise ValueError("concise applicability rationale required")
        # A matching handle authenticates a citation, not its applicability. A
        # distinct operator derivation is inspectable support, never a certificate.
        derivation_qualified = False
        if evidence["kind"] == "calculation_output":
            from .math_calculation_assistance import ApplicationSupportError, application_support_diagnostics
            diagnostics = application_support_diagnostics(self, root_id, evidence_id,
                method_source_id, method_quote)
            if diagnostics["issues"]:
                message = ("independent derivation needs an exact method-source quote"
                    if "exact_method_source_quote_required" in diagnostics["issues"] else
                    "independent operator derivation must match the operation, cite the method on its own lineage and have no open premises")
                raise ApplicationSupportError(message + "; " + "; ".join(diagnostics["issues"]), diagnostics)
            source = self.nodes.get(method_source_id)
            if (source is None or source["kind"] != "source_text" or
                    not isinstance(method_quote, str) or len(method_quote.strip()) < 12 or
                    method_quote not in source["value"]):
                raise ValueError("independent derivation needs an exact method-source quote")
            ancestors = set()
            frontier = [evidence_id]
            while frontier:
                current = frontier.pop()
                if current in ancestors:
                    continue
                ancestors.add(current)
                frontier.extend(self.nodes[current]["depends_on"])
            operators = {"matrix", "kronecker", "trace", "transpose", "adjoint"}
            has_operator = any(self.nodes[item]["kind"] == "calculation_operation"
                and self.nodes[item].get("operation") in operators for item in ancestors)
            has_task_symbol = any(self.nodes[item]["kind"] == "calculation_input"
                and self.nodes[item].get("operation") == "symbol"
                and any(self.nodes[parent]["kind"] in {"task", "task_fact"}
                    for parent in self.nodes[item]["depends_on"]) for item in ancestors)
            derivation_qualified = (root_id not in ancestors
                and source["id"] in ancestors and has_operator and has_task_symbol
                and evidence["status"] == "observed"
                and evidence["origin"].get("receipt_id") != root["origin"].get("receipt_id")
                and not self._open_roots(evidence_id)
                and equivalent(root["value"], evidence["value"]))
            if not derivation_qualified:
                raise ValueError("independent operator derivation must match the operation, cite the method on its own lineage and have no open premises")
        fact = evidence["value"] if evidence["kind"] == "task_fact" else {}
        downstream_paths = {node["origin"]["output_path"] for node in self.nodes.values()
            if node["kind"] == "calculation_output" and root_id in self._roots(node["id"])}
        qualified = derivation_qualified or (fact.get("kind") in {"definition", "normalization"}
            and bool(fact.get("expression"))
            and bool(downstream_paths.intersection(fact.get("required_paths", ())))
            and equivalent(root["value"], fact["expression"]))
        support = self._put("application_support", root["value"],
            "agent_supported_not_verified" if qualified else "agent_cited_not_verified",
            [evidence_id, *([method_source_id] if derivation_qualified else [])],
            {"kind": "agent_review", "target_operation_id": root_id},
            statement=rationale)
        self.obligations[root_id] = {"root_id": root_id,
            "status": "agent_supported_not_verified" if qualified else "open",
            "support_node_id": support["id"], "evidence_id": evidence_id,
            "reason": ("independent operator derivation and exact method quote claimed; physical applicability is not certified"
                if derivation_qualified else "exact harness formula matches operation value; physical meaning is not certified"
                if qualified else "citation recorded, but exact applicability remains unverified")}
        return support

    def submit(self, answer: Any, support: dict[str, str]):
        if set(support) != set(self.required_paths):
            raise ValueError("every required output path needs one graph support node")
        bindings, roots, unsupported = {}, set(), []
        for path in self.required_paths:
            identifier = support[path]
            node = self.nodes.get(identifier)
            if node is None or node["status"] in {"failed", "exploratory"}:
                raise ValueError("answer support is unknown or failed: " + path)
            if node.get("atomic_nodes"):
                child_id = node["atomic_nodes"].get(path)
                if child_id is None:
                    raise ValueError("bundle has no atomic value for output path: " + path)
                identifier, node = child_id, self.nodes[child_id]
            desired = path_value(answer, path)
            if not equivalent(desired, node["value"]):
                raise ValueError("answer differs from exact graph value: " + path)
            bindings[path] = identifier
            roots.update(self._roots(identifier))
            if node["status"] != "task_given" and not self._has_qualified_evidence(identifier):
                unsupported.append(identifier)
        unresolved = sorted(root for root in roots if self.obligations[root]["status"] == "open")
        return {"answer": answer, "support_nodes": bindings,
            "premise_root_ids": sorted(roots), "unresolved_root_ids": unresolved,
            "missing_evidence_node_ids": sorted(set(unsupported)),
            "ready": not unresolved and not unsupported,
            "scientific_status": "provisional" if unresolved or unsupported else
                "agent_supported_not_verified" if roots else "evidence_linked_not_verified",
            "mathematically_verified": False}

    def frontier(self):
        return {"recent_nodes": [self.nodes[identifier] for identifier in self.order[-12:]],
            "node_count": len(self.nodes), "open_obligations": [item for item in self.obligations.values()
                if item["status"] == "open"],
            "required_paths": list(self.required_paths),
            "indexed_sets": self.indexed_sets,
            "available_receipt_ids": list(self.receipts)}

    def export(self):
        return {"nodes": [self.nodes[identifier] for identifier in self.order],
            "indexed_sets": self.indexed_sets,
            "obligations": list(self.obligations.values()),
            "receipt_index": {identifier: {key: value for key, value in receipt.items()
                if key in {"kind", "source_sha256"}} for identifier, receipt in self.receipts.items()},
            "digest": _digest([self.nodes[identifier] for identifier in self.order])}
