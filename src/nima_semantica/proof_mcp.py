"""Harness-operated private proof sessions, independent of project graph admission."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .math_graph_session import PrivateGraphSessionStore, _json


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProofConfig(Contract):
    database_path: str
    corpus_id: str = "papers"
    project_id: str
    store_root: str | None = None
    allow_retrieval: bool = False
    allow_execution: bool = False
    projection_id: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=120)


class Open(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=24000)
    mode: Literal["draft", "conduct"] = "draft"
    assumptions: list[str] = Field(default_factory=list)


class Session(Contract):
    session_id: str


class Mutation(Session):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class RecordNode(Mutation):
    kind: Literal["definition", "assumption", "strategy", "lemma", "step", "obligation", "counterexample"]
    statement: str = Field(min_length=1, max_length=24000)
    depends_on: list[str] = Field(default_factory=list)
    supersedes: str | None = None


class Resolve(Mutation):
    obligation_id: str
    support_nodes: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=8000)


class Attach(Mutation):
    receipt_id: str
    purpose: str = Field(min_length=1, max_length=4000)


class Retrieve(Mutation):
    query: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=2000)


class Experiment(Mutation):
    source: str = Field(min_length=1, max_length=30000)
    purpose: str = Field(min_length=1, max_length=4000)
    depends_on: list[str] = Field(default_factory=list)


class Submit(Mutation):
    conclusion_node: str
    argument: str = Field(min_length=1, max_length=64000)


class Close(Mutation):
    outcome: Literal["completed", "partial"]
    summary: str = Field(min_length=1, max_length=8000)


class Inspect(Session):
    identifier: str


TOOLS = {
    "open": (Open, "Create or replay a target-bound private proof session."),
    "status": (Session, "Read the current revision and session binding."),
    "frontier": (Session, "Inspect open obligations and superseded dependencies."),
    "inspect": (Inspect, "Inspect an exact private node or action receipt."),
    "export": (Session, "Export private proof state and receipts; does not publish to the project graph."),
    "record_node": (RecordNode, "Record a provisional definition, assumption, strategy, lemma, step, obligation or counterexample."),
    "resolve_obligation": (Resolve, "Record a harness assessment with exact support dependencies; this is uncertified."),
    "attach_receipt": (Attach, "Import an authentic same-scope specialist-tool receipt, retaining its actual status."),
    "retrieve_context": (Retrieve, "Retrieve exact evidence from the configured corpus/project projection."),
    "run_experiment": (Experiment, "Execute exploratory Python in the configured isolated worker; execution is not a proof."),
    "submit": (Submit, "Select the current argument and check represented obligations; scientific status remains uncertified."),
    "close": (Close, "Close completed after a current ready submission, or partial with unresolved work."),
    "cancel": (Mutation, "Cancel a session; late external results remain audit evidence only."),
}


class ProofService(PrivateGraphSessionStore):
    """Use the existing private SQLite substrate with proof-specific transitions."""

    def __init__(self, config: ProofConfig, *, worker=None):
        super().__init__(config.database_path)
        self.config = config
        self.worker = worker
        self.scope = _json({"corpus_id": config.corpus_id, "project_id": config.project_id})
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proof_sessions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, creation TEXT NOT NULL,
                    revision INTEGER NOT NULL, lifecycle TEXT NOT NULL, active TEXT,
                    state TEXT NOT NULL, UNIQUE(scope, creation));
                CREATE TABLE IF NOT EXISTS proof_calls (
                    session TEXT NOT NULL, request TEXT NOT NULL, operation TEXT NOT NULL,
                    arguments TEXT NOT NULL, result TEXT, PRIMARY KEY(session, request));
                CREATE TABLE IF NOT EXISTS proof_receipts (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    def _load(self, db, sid):
        row = db.execute("SELECT * FROM proof_sessions WHERE id=? AND scope=?", (sid, self.scope)).fetchone()
        if row is None:
            raise ValueError("unknown or foreign proof session")
        return row, json.loads(row["state"])

    def _receipt(self, db, sid, operation, args, status, result):
        rid = "proof-receipt-" + uuid.uuid4().hex
        payload = dict(receipt_id=rid, operation=operation, arguments=args, status=status,
                       result=result, scientific_status="uncertified")
        db.execute("INSERT INTO proof_receipts VALUES (?,?,?)", (rid, sid, _json(payload)))
        return rid

    def invoke(self, operation, arguments):
        args = TOOLS[operation][0].model_validate(arguments).model_dump(mode="json")
        if operation == "open":
            return self.open(args)
        if operation in {"status", "frontier", "inspect", "export"}:
            return self.read(operation, args)
        return self.mutate(operation, args)

    def open(self, args):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM proof_sessions WHERE scope=? AND creation=?", (self.scope, args["request_id"])).fetchone()
            if row:
                if json.loads(row["state"])["binding"] != args:
                    raise ValueError("divergent session creation replay")
                return {"session_id": row["id"], "revision": row["revision"], "lifecycle": row["lifecycle"]}
            sid = "proof-" + uuid.uuid4().hex
            state = {"binding": args, "nodes": {}, "resolutions": {}, "superseded": [], "submission": None}
            state["nodes"]["target"] = {"kind": "target", "statement": args["target"], "depends_on": []}
            for n, assumption in enumerate(args["assumptions"]):
                if not assumption.strip():
                    raise ValueError("empty assumption")
                state["nodes"][f"assumption-{n}"] = {"kind": "assumption", "statement": assumption, "depends_on": []}
            db.execute("INSERT INTO proof_sessions VALUES (?,?,?,0,'open',NULL,?)", (sid, self.scope, args["request_id"], _json(state)))
            rid = self._receipt(db, sid, "open", args, "complete", {"revision": 0})
            return {"session_id": sid, "revision": 0, "lifecycle": "open", "receipt_id": rid}

    @staticmethod
    def frontier(state):
        nodes, superseded = state["nodes"], set(state["superseded"])
        def closure(identifier, seen=None):
            seen = set() if seen is None else seen
            if identifier in seen:
                return seen
            seen.add(identifier)
            for parent in nodes[identifier]["depends_on"]:
                closure(parent, seen)
            return seen
        stale = [key for key in nodes if closure(key) & superseded]
        unresolved = [key for key, node in nodes.items() if node["kind"] == "obligation" and key not in superseded
                      and (key not in state["resolutions"] or any(s in stale for s in state["resolutions"][key]["support_nodes"]))]
        return {"open_obligations": unresolved, "stale_nodes": stale, "superseded": sorted(superseded)}

    def read(self, operation, args):
        with self._connect() as db:
            row, state = self._load(db, args["session_id"])
            result = {"session_id": row["id"], "revision": row["revision"], "lifecycle": row["lifecycle"],
                      "scientific_status": "uncertified", "binding": state["binding"], "scope": json.loads(self.scope)}
            if operation == "frontier":
                result.update(self.frontier(state))
            elif operation == "inspect":
                identifier = args["identifier"]
                if identifier in state["nodes"]:
                    result["node"] = state["nodes"][identifier]
                else:
                    found = db.execute("SELECT payload FROM proof_receipts WHERE session=? AND id=?", (row["id"], identifier)).fetchone()
                    if not found:
                        raise ValueError("unknown or foreign node/receipt")
                    result["receipt"] = json.loads(found[0])
            elif operation == "export":
                result["graph"] = state
                result["receipts"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM proof_receipts WHERE session=? ORDER BY rowid", (row["id"],))]
            return result

    def mutate(self, operation, args):
        sid, request = args["session_id"], args["request_id"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row, state = self._load(db, sid)
            old = db.execute("SELECT * FROM proof_calls WHERE session=? AND request=?", (sid, request)).fetchone()
            if old and old["operation"] == operation and old["arguments"] == _json(args):
                return json.loads(old["result"]) if old["result"] else {"status": "running", "revision": row["revision"]}
            reason = ("divergent request replay" if old else "stale revision" if args["expected_revision"] != row["revision"]
                      else "session is terminal" if row["lifecycle"] != "open" else "another action is running" if row["active"] and operation != "cancel" else None)
            if reason:
                result = {"status": "rejected", "diagnostic": reason, "revision": row["revision"]}
                result["receipt_id"] = self._receipt(db, sid, operation, args, "rejected", dict(result))
                return result
            db.execute("INSERT INTO proof_calls VALUES (?,?,?,?,NULL)", (sid, request, operation, _json(args)))
            if operation != "cancel":
                db.execute("UPDATE proof_sessions SET active=? WHERE id=?", (request, sid))
            revision = row["revision"]
        # Worker/retrieval I/O happens outside the SQLite write transaction.
        try:
            result, lifecycle = self._apply(state, operation, args)
            status = "complete"
        except Exception as exc:
            result, lifecycle, status = {"diagnostic": str(exc)[:1000], "error": type(exc).__name__}, "open", "rejected"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current, _ = self._load(db, sid)
            if operation != "cancel" and (current["revision"] != revision or current["active"] != request or current["lifecycle"] != "open"):
                status, result = "interrupted", {"late_result": result}
            elif status == "complete":
                db.execute("UPDATE proof_sessions SET revision=?, lifecycle=?, active=NULL, state=? WHERE id=?", (revision + 1, lifecycle, _json(state), sid))
            elif current["active"] == request:
                db.execute("UPDATE proof_sessions SET active=NULL WHERE id=?", (sid,))
            final_revision = db.execute("SELECT revision FROM proof_sessions WHERE id=?", (sid,)).fetchone()[0]
            receipt = self._receipt(db, sid, operation, args, status, result)
            packet = {"status": status, "session_id": sid, "revision": final_revision, "receipt_id": receipt,
                      "scientific_status": "uncertified", "result": result}
            db.execute("UPDATE proof_calls SET result=? WHERE session=? AND request=?", (_json(packet), sid, request))
            return packet

    def _apply(self, state, operation, args):
        nodes = state["nodes"]
        def parents(ids):
            if len(ids) != len(set(ids)) or any(i not in nodes for i in ids):
                raise ValueError("dependencies must be distinct existing same-session nodes")
        def add(node):
            identifier = "n-" + hashlib.sha256(_json({"node": node, "request": args["request_id"]}).encode()).hexdigest()[:24]
            nodes[identifier] = node
            state["submission"] = None
            return identifier
        result = {}
        if operation == "record_node":
            parents(args["depends_on"])
            if args["supersedes"]:
                parents([args["supersedes"]])
                if nodes[args["supersedes"]]["kind"] == "target":
                    raise ValueError("session target is immutable")
                state["superseded"].append(args["supersedes"])
            result["node_id"] = add({k: args[k] for k in ("kind", "statement", "depends_on")})
        elif operation == "resolve_obligation":
            parents([args["obligation_id"], *args["support_nodes"]])
            if nodes[args["obligation_id"]]["kind"] != "obligation":
                raise ValueError("selected node is not an obligation")
            # A support path through the obligation would be circular justification.
            frontier = set(args["support_nodes"])
            visited = set()
            while frontier:
                item = frontier.pop()
                if item == args["obligation_id"]:
                    raise ValueError("circular obligation support")
                if item not in visited:
                    visited.add(item); frontier.update(nodes[item]["depends_on"])
            if visited & set(self.frontier(state)["stale_nodes"]):
                raise ValueError("support depends on superseded nodes")
            state["resolutions"][args["obligation_id"]] = {"support_nodes": args["support_nodes"], "rationale": args["rationale"], "authority": "harness_assessment_uncertified"}
            state["submission"] = None
            result = state["resolutions"][args["obligation_id"]]
        elif operation == "attach_receipt":
            from .execution_receipts import ExecutionReceiptService
            with self._research_store() as store:
                receipt = ExecutionReceiptService(store).get(args["receipt_id"], corpus_id=self.config.corpus_id, project_id=self.config.project_id)
                if receipt is None or receipt.project_id not in (None, self.config.project_id):
                    raise ValueError("unknown or foreign specialist receipt")
                result["node_id"] = add({"kind": "check", "statement": args["purpose"], "depends_on": [], "receipt": receipt.model_dump(mode="json")})
        elif operation == "retrieve_context":
            if not self.config.allow_retrieval or not self.config.projection_id:
                raise ValueError("retrieval not configured and authorized")
            from types import SimpleNamespace
            from .math_retrieval import retrieve_math_context, MathRetrievalPolicy, RetrieveMathContext
            context = SimpleNamespace(corpus_id=self.config.corpus_id, project_id=self.config.project_id,
                                      retrieval=MathRetrievalPolicy(enabled=True, projection_id=self.config.projection_id))
            with self._research_store() as store:
                found = retrieve_math_context(store, context, RetrieveMathContext(query=args["query"], purpose=args["purpose"]))
            result["evidence"] = [{"node_id": add({"kind": "evidence", "statement": passage["text"], "depends_on": [], "source": passage}), **passage} for passage in found["passages"]]
        elif operation == "run_experiment":
            parents(args["depends_on"])
            if not self.config.allow_execution:
                raise ValueError("execution not authorized")
            from .symbolic_transport import configured_symbolic_worker
            execution = (self.worker or configured_symbolic_worker()).run(args["source"], self.config.timeout_seconds)
            result["node_id"] = add({"kind": "check", "statement": args["purpose"], "depends_on": args["depends_on"], "source": args["source"], "execution": execution})
            result["execution"] = execution
        elif operation == "submit":
            parents([args["conclusion_node"]])
            if nodes[args["conclusion_node"]]["kind"] not in {"strategy", "lemma", "step", "counterexample"}:
                raise ValueError("select a recorded strategy or argument, not an input or check")
            frontier = self.frontier(state)
            ready = args["conclusion_node"] not in frontier["stale_nodes"] and (state["binding"]["mode"] == "draft" or not frontier["open_obligations"])
            state["submission"] = {"conclusion_node": args["conclusion_node"], "argument": args["argument"], "ready": ready, **frontier}
            result = state["submission"]
        elif operation == "close":
            if args["outcome"] == "completed" and not (state["submission"] or {}).get("ready"):
                raise ValueError("completed closure requires a current ready submission")
            state["closure"] = {"outcome": args["outcome"], "summary": args["summary"]}
            return state["closure"], args["outcome"]
        elif operation == "cancel":
            return {"cancelled": True}, "cancelled"
        else:
            raise ValueError("unknown action")
        return result, "open"

    def _research_store(self):
        from contextlib import contextmanager
        from .storage import GraphStore
        @contextmanager
        def opened():
            if not self.config.store_root or not (Path(self.config.store_root) / "graph.sqlite3").is_file():
                raise ValueError("configured research store is unavailable")
            store = GraphStore(Path(self.config.store_root))
            try:
                yield store
            finally:
                store.close()
        return opened()

    def recover_interrupted(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM proof_sessions WHERE scope=? AND active IS NOT NULL", (self.scope,)).fetchall():
                call = db.execute("SELECT * FROM proof_calls WHERE session=? AND request=?", (row["id"], row["active"])).fetchone()
                result = {"status": "interrupted", "revision": row["revision"], "diagnostic": "process interrupted; inspect before a new action"}
                result["receipt_id"] = self._receipt(db, row["id"], call["operation"], json.loads(call["arguments"]), "interrupted", dict(result))
                db.execute("UPDATE proof_calls SET result=? WHERE session=? AND request=?", (_json(result), row["id"], row["active"]))
                db.execute("UPDATE proof_sessions SET active=NULL WHERE id=?", (row["id"],))


def create_server(service):
    from mcp.server.fastmcp import FastMCP
    import anyio
    server = FastMCP("NIMA private proof graph")
    def register(operation, schema, description):
        async def call(request):
            return await anyio.to_thread.run_sync(lambda: service.invoke(operation, request.model_dump(mode="json")))
        call.__name__ = "nima_proof_" + operation
        call.__annotations__ = {"request": schema, "return": dict}
        call.__signature__ = inspect.Signature([inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=schema)], return_annotation=dict)
        server.tool(name=call.__name__, description=description)(call)
    for operation, (schema, description) in TOOLS.items():
        register(operation, schema, description)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    service = ProofService(ProofConfig.model_validate_json(Path(args.config).read_text()))
    from .math_mcp import exclusive_owner
    with exclusive_owner(service):
        create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
