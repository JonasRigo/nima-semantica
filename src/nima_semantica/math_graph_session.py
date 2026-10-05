"""Prototype durable, private calculation-graph session for an external agent loop.

This is not a project/corpus graph and does not grant scientific authority.
The caller must supply a trusted, fixed binding; model-authored tool arguments
contain only an action, never a project, run, or session selector.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .math_graph_program import compile_graph_program
from .math_indexed_count import IndexedSet
from .math_retrieval import MathRetrievalPolicy, installation_retrieval_policy, RetrieveMathContext, retrieve_math_context
from .math_single_graph_state import SingleCalculationGraph
from .math_task_contract import MathTaskFact


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class GraphSessionBinding:
    project_id: str
    run_id: str
    session_id: str
    task: str
    required_paths: tuple[str, ...]
    task_facts: tuple[MathTaskFact, ...] = ()
    indexed_sets: tuple[IndexedSet, ...] = ()
    corpus_id: str = "papers"
    retrieval_projection_id: str | None = None
    allow_retrieval: bool = False
    allow_execution: bool = False
    timeout_seconds: int = 60

    def __post_init__(self):
        if not all(isinstance(item, str) and item.strip() for item in (
            self.project_id, self.run_id, self.session_id, self.task, *self.required_paths
        )):
            raise ValueError("nonempty project, run, session, task and output paths required")
        if not self.required_paths or len(set(self.required_paths)) != len(self.required_paths):
            raise ValueError("distinct required output paths required")
        if not isinstance(self.corpus_id, str) or not self.corpus_id.strip():
            raise ValueError("nonempty corpus ID required")
        if self.timeout_seconds < 1 or self.timeout_seconds > 300:
            raise ValueError("worker timeout must be between 1 and 300 seconds")

    def digest(self) -> str:
        payload = {"project_id": self.project_id, "run_id": self.run_id,
            "session_id": self.session_id, "task": self.task,
            "required_paths": self.required_paths,
            "task_facts": [fact.model_dump(mode="json") for fact in self.task_facts],
            "indexed_sets": [item.model_dump(mode="json") for item in self.indexed_sets],
            "corpus_id": self.corpus_id, "retrieval_projection_id": self.retrieval_projection_id,
            "allow_retrieval": self.allow_retrieval, "allow_execution": self.allow_execution,
            "timeout_seconds": self.timeout_seconds}
        return hashlib.sha256(_json(payload).encode()).hexdigest()


class PrivateGraphSessionStore:
    """SQLite-backed action boundary; intentionally separate from GraphStore."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.parent.is_dir():
            raise ValueError("session database parent does not exist")
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, binding_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT NOT NULL, revision_before INTEGER NOT NULL,
                    revision_after INTEGER NOT NULL
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _state(graph: SingleCalculationGraph) -> dict:
        return {"nodes": graph.nodes, "order": graph.order,
            "obligations": graph.obligations, "receipts": graph.receipts,
            "compiled_plans": graph.compiled_plans, "serial": graph._serial}

    @staticmethod
    def _restore(binding: GraphSessionBinding, payload: dict) -> SingleCalculationGraph:
        graph = SingleCalculationGraph(binding.task, binding.task_facts,
            binding.required_paths, binding.indexed_sets)
        graph.nodes = payload["nodes"]
        graph.order = payload["order"]
        graph.obligations = payload["obligations"]
        graph.receipts = payload["receipts"]
        graph.compiled_plans = payload["compiled_plans"]
        graph._serial = payload["serial"]
        return graph

    def act(self, binding: GraphSessionBinding, name: str, arguments: dict | None = None,
            *, expected_revision: int | None = None, research_store=None, worker=None) -> dict:
        """Apply one action and keep even rejected action attempts in the local audit."""
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise ValueError("action arguments must be an object")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE session_id=?",
                (binding.session_id,)).fetchone()
            if row is None:
                graph = SingleCalculationGraph(binding.task, binding.task_facts,
                    binding.required_paths, binding.indexed_sets)
                db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (binding.session_id, binding.project_id, binding.run_id,
                    binding.digest(), 0, _json(self._state(graph))))
                revision = 0
            else:
                if (row["project_id"] != binding.project_id or row["run_id"] != binding.run_id
                        or row["binding_hash"] != binding.digest()):
                    raise ValueError("session binding or scope mismatch")
                revision = row["revision"]
                graph = self._restore(binding, json.loads(row["state_json"]))
            if expected_revision is not None and expected_revision != revision:
                raise ValueError("stale session revision")
            try:
                if name == "inspect_action":
                    recorded = db.execute("SELECT * FROM actions WHERE session_id=? AND action_id=?",
                        (binding.session_id, arguments["action_id"])).fetchone()
                    if recorded is None:
                        raise ValueError("unknown or cross-session action receipt")
                    result, changes_state = {"action": dict(recorded)}, False
                else:
                    result, changes_state = self._dispatch(graph, binding, name, arguments,
                        research_store=research_store, worker=worker)
                status = result.get("status", "completed")
            except (ValueError, KeyError, TypeError) as exc:
                result, changes_state, status = {
                    "error": type(exc).__name__, "diagnostic": str(exc)[:500]
                }, False, "rejected"
            next_revision = revision + int(changes_state)
            if changes_state:
                db.execute("UPDATE sessions SET revision=?, state_json=? WHERE session_id=?",
                    (next_revision, _json(self._state(graph)), binding.session_id))
            cursor = db.execute("""INSERT INTO actions
                (session_id, name, arguments_json, status, result_json,
                    revision_before, revision_after) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (binding.session_id, name, _json(arguments), status, _json(result),
                    revision, next_revision))
            return {"status": status, "action_id": cursor.lastrowid,
                "revision": next_revision, "result": result}

    @staticmethod
    def _dispatch(graph: SingleCalculationGraph, binding: GraphSessionBinding, name: str,
            args: dict, *, research_store=None, worker=None) -> tuple[dict, bool]:
        if name == "frontier":
            return graph.frontier(), False
        if name == "export":
            return graph.export(), False
        if name == "inspect_node":
            node = graph.nodes.get(args["node_id"])
            if node is None:
                raise ValueError("unknown or cross-session node")
            return {"node": node, "parents": [graph.nodes[p] for p in node["depends_on"]]}, False
        if name == "inspect_receipt":
            receipt = graph.receipts.get(args["receipt_id"])
            if receipt is None:
                raise ValueError("unknown or cross-session receipt")
            return {"receipt_id": args["receipt_id"], "receipt": receipt}, False
        if name == "retrieve_context":
            if not binding.allow_retrieval or research_store is None:
                raise ValueError("retrieval is not authorized or configured")
            from types import SimpleNamespace
            action = RetrieveMathContext.model_validate(args)
            context = SimpleNamespace(project_id=binding.project_id,
                corpus_id=binding.corpus_id, retrieval=installation_retrieval_policy(projection_id=binding.retrieval_projection_id))
            found = retrieve_math_context(research_store, context, action)
            nodes = [graph.record_source(passage["region_id"], passage["text"],
                passage.get("evidence", {}).get("source_revision"))
                for passage in found["passages"]]
            return {"source_nodes": [{"id": node["id"], "region_id": node["origin"]["region_id"],
                "text": node["value"]} for node in nodes],
                "retrieval_context": found, "projection_revision": found["projection_revision"],
                "truncated": found["truncated"]}, True
        if name in {"run_experiment", "run_calculation_graph"}:
            if not binding.allow_execution:
                raise ValueError("isolated execution is not authorized")
            if worker is None:
                from .symbolic_transport import configured_symbolic_worker
                worker = configured_symbolic_worker()
            receipt_id = "session-" + uuid.uuid4().hex
            purpose = args.get("purpose", "")
            if name == "run_experiment":
                source = args["source"]
                if not isinstance(source, str) or not source.strip() or len(source) > 30000:
                    raise ValueError("nonempty bounded Python source required")
                parents = graph._parents(args.get("depends_on", []))
                try:
                    execution = worker.run(source, binding.timeout_seconds)
                except Exception as exc:
                    execution = {"outcome": "worker_error", "exit_code": None,
                        "stderr": type(exc).__name__, "stdout": ""}
                nodes = graph.record_execution(source, execution, receipt_id, parents,
                    purpose, evidence_eligible=False)
                return {"status": "completed" if execution.get("outcome") == "executed"
                    and execution.get("exit_code") == 0 else "failed", "receipt_id": receipt_id,
                    "node_ids": [node["id"] for node in nodes],
                    "execution_outcome": execution.get("outcome")}, True
            plan = compile_graph_program(graph, args["steps"], args["outputs"],
                binding.indexed_sets, diagnostics=bool(args.get("_typed_applicability")),
                step_limit=args.get("_expanded_step_limit", 128))
            plan["reuse_identities"] = args.get("_reuse_identities", {})
            if args.get("_typed_applicability"):
                plan["typed_applicability"] = True
            graph.stage_compiled_plan(plan, receipt_id, purpose)
            try:
                execution = worker.run(plan["source"], binding.timeout_seconds)
            except Exception as exc:
                execution = {"outcome": "worker_error", "exit_code": None,
                    "stderr": type(exc).__name__, "stdout": ""}
            recorded = graph.record_compiled_execution(plan, execution, receipt_id, purpose)
            from .math_calculation_assistance import execution_diagnostics
            diagnostics = execution_diagnostics(plan, execution)
            if isinstance(recorded, list):
                return {"status": "failed" if execution.get("outcome") != "executed" else "exploratory",
                    "receipt_id": receipt_id, "node_ids": [node["id"] for node in recorded],
                    "execution_outcome": execution.get("outcome"), "calculation_diagnostics": diagnostics}, True
            return {"receipt_id": receipt_id,
                "step_nodes": {key: node["id"] for key, node in recorded["steps"].items()},
                "output_nodes": {key: {"id": node["id"], "value": node["value"]}
                    for key, node in recorded["outputs"].items()},
                "controller_repairs": plan.get("controller_repairs", []),
                "calculation_diagnostics": diagnostics,
                "open_obligations": graph.frontier()["open_obligations"]}, True
        if name == "record_step":
            node = graph.add_step(kind=args["kind"], statement=args["statement"],
                value=args["value"], depends_on=args["depends_on"],
                supersedes=args.get("supersedes"))
            return {"node": node, "frontier": graph.frontier()}, True
        if name == "substantiate":
            node = graph.substantiate(args["hypothesis_id"], args["evidence_id"],
                args["rationale"])
            return {"node": node, "obligation": graph.obligations[args["hypothesis_id"]]}, True
        if name == "substantiate_application":
            node = graph.substantiate_application(args["operation_id"],
                args["evidence_id"], args["rationale"], args.get("method_source_id"),
                args.get("method_quote"))
            return {"node": node, "obligation": graph.obligations[args["operation_id"]]}, True
        if name == "submit":
            proposal = graph.submit(args["answer"], args["support_nodes"])
            return {"proposal": proposal, "admitted": proposal["ready"],
                "mathematically_verified": False}, False
        raise ValueError("unknown graph action")

    def latest_submission(self, binding: GraphSessionBinding) -> dict | None:
        """Return the latest controller submission decision, never agent final prose."""
        for row in reversed(self.audit(binding)):
            if row["name"] == "submit":
                return {"status": row["status"], "action_id": row["action_id"],
                    "revision": row["revision_after"], "result": json.loads(row["result_json"])}
        return None

    def seed_graph(self, binding: GraphSessionBinding, graph: SingleCalculationGraph) -> None:
        """Trusted offline import for exact frozen-state replay, not a model tool."""
        if graph.nodes["task"]["value"] != binding.task or graph.required_paths != binding.required_paths:
            raise ValueError("seed graph differs from bound task or output paths")
        for fact in binding.task_facts:
            identifier = "fact:" + fact.fact_id
            if graph.nodes.get(identifier, {}).get("value") != fact.model_dump(mode="json"):
                raise ValueError("seed graph differs from bound task facts")
        if graph.indexed_sets != [item.model_dump(mode="json") for item in binding.indexed_sets]:
            raise ValueError("seed graph differs from bound index sets")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sessions WHERE session_id=?", (binding.session_id,)).fetchone():
                raise ValueError("session already exists")
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (binding.session_id, binding.project_id, binding.run_id,
                binding.digest(), 0, _json(self._state(graph))))

    def audit(self, binding: GraphSessionBinding) -> list[dict]:
        with self._connect() as db:
            row = db.execute("SELECT project_id, run_id, binding_hash FROM sessions WHERE session_id=?",
                (binding.session_id,)).fetchone()
            if row is None or (row["project_id"], row["run_id"], row["binding_hash"]) != (
                    binding.project_id, binding.run_id, binding.digest()):
                raise ValueError("session binding or scope mismatch")
            return [dict(row) for row in db.execute(
                "SELECT * FROM actions WHERE session_id=? ORDER BY action_id", (binding.session_id,))]
