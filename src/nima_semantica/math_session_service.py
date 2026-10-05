"""Versioned harness service over the existing private calculation graph.

The local process owner supplies scope/capabilities. Tool arguments cannot widen
them. Attempt start/outcome rows are append-only; external work holds no DB lock.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .math_graph_session import GraphSessionBinding, PrivateGraphSessionStore, _json
from .math_single_graph_state import SingleCalculationGraph
from .math_task_contract import MathTaskFact
from .math_indexed_count import IndexedSet


class MathServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    database_path: str
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    corpus_id: str = "papers"
    retrieval_projection_id: str | None = None
    allow_retrieval: bool = False
    allow_execution: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @model_validator(mode="after")
    def require_pinned_retrieval(self):
        if self.allow_retrieval and not self.retrieval_projection_id:
            raise ValueError("enabled retrieval requires an operator-pinned projection ID")
        return self


class MathSessionService(PrivateGraphSessionStore):
    """Candidate service. Historical prototype callers retain their own adapter."""

    def __init__(self, config: MathServiceConfig, *, research_store=None, worker=None):
        config = MathServiceConfig.model_validate(config.model_dump())
        super().__init__(config.database_path)
        self.config, self.research_store, self.worker = config, research_store, worker
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS math_lifecycle (
                    session_id TEXT PRIMARY KEY, binding_json TEXT NOT NULL,
                    scope_json TEXT NOT NULL, state TEXT NOT NULL, active TEXT
                );
                CREATE TABLE IF NOT EXISTS math_attempts (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    name TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS math_outcomes (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS math_late_results (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    result_json TEXT NOT NULL, detached_state_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS math_creations (
                    scope_json TEXT NOT NULL, request_id TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, session_id TEXT NOT NULL,
                    PRIMARY KEY(scope_json, request_id)
                );
            """)

    def _scope(self):
        return _json(self.config.model_dump(exclude={"database_path"}))

    @staticmethod
    def _dispatch(graph, binding, name, args, *, research_store=None, worker=None):
        if any(key.startswith("_") for key in args):
            raise ValueError("controller-owned execution fields cannot be supplied")
        if name != "retrieve_context":
            # Normalize only a lossless symbol wrapper; never discard assumptions
            # or infer a physical contribution. The durable attempt keeps raw args.
            repairs = []
            reconstruction = None
            comparison_bindings = None
            selection = None
            supplemental = None
            if name == "substantiate_application" and args.get("method_span") is not None:
                from .math_calculation_assistance import source_span
                args = dict(args)
                selection = source_span(graph, args.get("method_source_id"), args.pop("method_span"))
                if args.get("method_quote") is not None and args["method_quote"] != selection["quote"]:
                    raise ValueError("method_quote conflicts with selected exact source span")
                args["method_quote"] = selection["quote"]
            if name == "submit":
                from .math_response_views import assemble_answer, submission_support
                support, supplemental = submission_support(graph, args["support_nodes"])
                args = {**args, "support_nodes": support}
                if "answer" not in args:
                    args["answer"] = assemble_answer(graph, support)
            if name == "run_calculation_graph":
                args = dict(args)
                comparison_bindings = args.pop("comparison_bindings", None)
                receipt_id = args.pop("reconstructs_receipt", None)
                if comparison_bindings is not None and receipt_id is None:
                    raise ValueError("comparison bindings require reconstructs_receipt")
                if receipt_id is not None:
                    from .math_evidence_handoff import reconstruction_context
                    reconstruction = reconstruction_context(graph, receipt_id)
                from .math_calculation_assistance import expand_reuse, EXPANDED_STEP_LIMIT
                args, reuse_records = expand_reuse(graph, args)
                repairs.extend(reuse_records)
                from .math_interface_helpers import expand_aggregations, order_dependencies
                args, aggregation_repairs = expand_aggregations(args, binding.indexed_sets, step_limit=EXPANDED_STEP_LIMIT)
                repairs.extend(aggregation_repairs)
                args, ordering = order_dependencies(args, graph.nodes)
                repairs.extend(ordering)
                for step in args.get("steps", []):
                    value = step.get("value")
                    if step.get("op") == "symbol" and isinstance(value, dict) and set(value) == {"name"}:
                        step["value"] = value["name"]
                        repairs.append({"step_id": step.get("id"), "normalization": "symbol name wrapper to string"})
                from .math_interface_helpers import bind_input_origins
                args, bindings = bind_input_origins(graph, args)
                repairs.extend(bindings)
                args["_typed_applicability"] = True
                args["_expanded_step_limit"] = EXPANDED_STEP_LIMIT
            original_source = None
            if name == "run_experiment":
                from .math_calculation_assistance import instrument_exploration
                args = dict(args)
                original_source = args["source"]
                args["source"] = instrument_exploration(original_source)
            try:
                result, changed = PrivateGraphSessionStore._dispatch(graph, binding, name, args,
                    research_store=research_store, worker=worker)
            except ValueError as exc:
                from .math_calculation_assistance import ApplicationSupportError
                if isinstance(exc, ApplicationSupportError) and selection is not None:
                    exc.support_diagnostics["method_selection"] = selection
                raise
            if original_source is not None and result.get("receipt_id"):
                receipt = graph.receipts[result["receipt_id"]]
                receipt.update(original_source=original_source, observations_instrumented=True)
            if selection is not None:
                result["method_selection"] = selection
            if supplemental:
                result["supplemental_support"] = {"nodes": supplemental,
                    "authority": "Additional local references retained for inspection, not answer fields or independently qualified claims."}
            if name == "inspect_node" and args.get("source_span") is not None:
                from .math_calculation_assistance import source_span
                result["source_selection"] = source_span(graph, args["node_id"], args["source_span"])
            if name == "inspect_node" and graph.nodes[args["node_id"]]["kind"] == "source_text":
                text = graph.nodes[args["node_id"]]["value"]
                offset = 0
                spans = []
                for line in text.splitlines(keepends=True):
                    spans.append({"start": offset, "end": offset + len(line)})
                    offset += len(line)
                result["source_line_spans"] = spans
            if repairs:
                result["interface_normalizations"] = repairs
            if reconstruction is not None:
                from .math_calculation_assistance import compare_reconstruction
                result["reconstruction_comparison"] = compare_reconstruction(graph, result, receipt_id, comparison_bindings)
                result["reconstruction"] = reconstruction
                # An audit edge is not a proof dependency or an equivalence assertion.
                compiled = graph.compiled_plans.get(result.get("receipt_id"))
                if compiled:
                    graph.nodes[compiled["experiment_id"]]["reconstruction_of"] = reconstruction
                    for item in result.get("output_nodes", {}).values():
                        graph.nodes[item["id"]]["reconstruction_of"] = reconstruction
            if name == "inspect_receipt":
                from .math_evidence_handoff import receipt_handoff
                from .math_calculation_assistance import observations
                result["compilation_handoff"] = receipt_handoff(graph, args["receipt_id"])
                result["captured_observations"] = observations(graph.receipts[args["receipt_id"]])
            if name == "submit" and not result.get("admitted"):
                roots = result.get("proposal", {}).get("unresolved_root_ids", [])
                result["repair_guidance"] = {
                    "unresolved": [{"node": graph.nodes.get(identifier),
                        "obligation": graph.obligations.get(identifier)} for identifier in roots],
                    "instruction": "Resolve these premises from exact applicable evidence or an independent derivation that does not assume them. Repeating a claim via record_step or checking its consequences does not substantiate it. If evidence is unavailable, close partial explicitly."}
            return result, changed
        if not binding.allow_retrieval or research_store is None:
            raise ValueError("retrieval is not authorized or configured")
        from types import SimpleNamespace
        from .math_retrieval import RetrieveMathContext, MathRetrievalPolicy, installation_retrieval_policy, retrieve_math_context
        found = retrieve_math_context(research_store, SimpleNamespace(
            project_id=binding.project_id, corpus_id=binding.corpus_id,
            retrieval=installation_retrieval_policy(projection_id=binding.retrieval_projection_id)),
            RetrieveMathContext.model_validate(args))
        nodes = []
        for passage in found["passages"]:
            node = graph.record_source(passage["region_id"], passage["text"],
                passage.get("evidence", {}).get("source_revision"))
            # Preserve the full exact-reader reference, locator and pinned projection.
            provenance = {"evidence": passage.get("evidence"), "locator": passage.get("locator"), "projection_id": found.get("projection_id"),
                "projection_revision": found["projection_revision"], "source_revision": found.get("source_revision")}
            if "retrieval_provenance" in node and node["retrieval_provenance"] != provenance:
                raise ValueError("source provenance changed within pinned session")
            node["retrieval_provenance"] = provenance
            nodes.append({"id": node["id"], "region_id": passage["region_id"], "text": passage["text"]})
        return {"source_nodes": nodes, "retrieval_receipt": found, "projection_revision": found["projection_revision"],
            "retrieval_context": found, "truncated": found["truncated"]}, True

    def _load(self, db, session_id):
        life = db.execute("SELECT * FROM math_lifecycle WHERE session_id=?", (session_id,)).fetchone()
        if life is None or life["scope_json"] != self._scope():
            raise ValueError("unknown session or scope/capability mismatch")
        data = json.loads(life["binding_json"])
        data["task_facts"] = tuple(MathTaskFact.model_validate(x) for x in data["task_facts"])
        data["indexed_sets"] = tuple(IndexedSet.model_validate(x) for x in data["indexed_sets"])
        data["required_paths"] = tuple(data["required_paths"])
        binding = GraphSessionBinding(**data)
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None or row["binding_hash"] != binding.digest():
            raise ValueError("corrupt session binding")
        return binding, row, life

    def open(self, task: str, required_paths: list[str], task_facts=(), indexed_sets=(),
             session_id: str | None = None, request_id: str | None = None):
        identifier = session_id or uuid.uuid4().hex
        scope = self.config.model_dump(exclude={"database_path"})
        binding = GraphSessionBinding(**scope, session_id=identifier, task=task,
            required_paths=tuple(required_paths), task_facts=tuple(task_facts), indexed_sets=tuple(indexed_sets))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            creation = _json({"task": task, "required_paths": required_paths,
                "task_facts": [x.model_dump(mode="json") for x in binding.task_facts],
                "indexed_sets": [x.model_dump(mode="json") for x in binding.indexed_sets]})
            if request_id is not None and session_id is None:
                prior = db.execute("SELECT * FROM math_creations WHERE scope_json=? AND request_id=?", (self._scope(), request_id)).fetchone()
                if prior:
                    if prior["arguments_json"] != creation:
                        raise ValueError("divergent session creation replay")
                    return self.read(prior["session_id"], "status")
            if session_id is not None:
                existing, _, _ = self._load(db, identifier)
                if existing.digest() != binding.digest():
                    raise ValueError("resume requires the exact original task binding")
            else:
                graph = SingleCalculationGraph(task, binding.task_facts, binding.required_paths, binding.indexed_sets)
                db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (identifier, binding.project_id, binding.run_id, binding.digest(), 0, _json(self._state(graph))))
                payload = asdict(binding)
                payload["task_facts"] = [x.model_dump(mode="json") for x in binding.task_facts]
                payload["indexed_sets"] = [x.model_dump(mode="json") for x in binding.indexed_sets]
                db.execute("INSERT INTO math_lifecycle VALUES (?, ?, ?, 'open', NULL)",
                    (identifier, _json(payload), self._scope()))
                if request_id is not None:
                    db.execute("INSERT INTO math_creations VALUES (?, ?, ?, ?)", (self._scope(), request_id, creation, identifier))
        return self.read(identifier, "status")

    @staticmethod
    def _response(session_id, revision, status, result, request_id=None):
        return dict(contract_version="math-session-v1", session_id=session_id,
            revision=revision, status=status, request_id=request_id, result=result)

    def read(self, session_id, name="frontier", arguments=None):
        args = arguments or {}
        with self._connect() as db:
            db.execute("BEGIN")
            binding, row, life = self._load(db, session_id)
            graph = self._restore(binding, json.loads(row["state_json"]))
            if name == "status":
                result = dict(state=life["state"], active_request=life["active"],
                    binding_hash=binding.digest(), task=binding.task,
                    required_paths=binding.required_paths, frontier=graph.frontier(),
                    task_facts=[fact.model_dump(mode="json") for fact in binding.task_facts],
                    indexed_sets=[spec.model_dump(mode="json") for spec in binding.indexed_sets])
            elif name == "inspect_action":
                attempt = db.execute("SELECT * FROM math_attempts WHERE session_id=? AND request_id=?",
                    (session_id, args["request_id"])).fetchone()
                if attempt is None:
                    raise ValueError("unknown or cross-session action")
                outcome = db.execute("SELECT response_json FROM math_outcomes WHERE session_id=? AND request_id=?",
                    (session_id, args["request_id"])).fetchone()
                result = {"attempt": dict(attempt), "outcome": json.loads(outcome[0]) if outcome else None}
                late = db.execute("SELECT result_json, detached_state_json FROM math_late_results WHERE session_id=? AND request_id=?", (session_id, args["request_id"])).fetchone()
                if late:
                    result["unadmitted_late_result"] = {"result": json.loads(late[0]), "detached_state": json.loads(late[1])}
            elif name in {"frontier", "export", "inspect_node", "inspect_receipt"}:
                result, _ = self._dispatch(graph, binding, name, args)
            else:
                raise ValueError("unknown inspection")
            if name in {"status", "frontier"}:
                result.update(self._repair_view(db, session_id, graph, support_nodes=args.get("support_nodes")))
            return self._response(session_id, row["revision"], "completed", result)

    @staticmethod
    def _repair_view(db, session_id, graph, pending=None, support_nodes=None):
        from .math_repair_feedback import repair_feedback
        attempts = [(row[0], json.loads(row[1])) for row in db.execute(
            "SELECT request_id, response_json FROM math_outcomes WHERE session_id=? ORDER BY rowid", (session_id,))]
        if pending:
            attempts.append(pending)
        return repair_feedback(graph, attempts, support_nodes=support_nodes)

    def _save_outcome(self, db, sid, rid, response):
        db.execute("INSERT INTO math_outcomes VALUES (?, ?, ?)", (sid, rid, _json(response)))

    def mutate(self, session_id: str, request_id: str, expected_revision: int,
               name: str, arguments: dict[str, Any]):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("bounded nonempty request_id required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("nonnegative expected_revision required")
        encoded = _json(arguments)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            binding, row, life = self._load(db, session_id)
            prior = db.execute("SELECT * FROM math_attempts WHERE session_id=? AND request_id=?",
                (session_id, request_id)).fetchone()
            if prior:
                if (prior["name"], prior["arguments_json"], prior["revision"]) != (name, encoded, expected_revision):
                    raise ValueError("divergent request replay")
                done = db.execute("SELECT response_json FROM math_outcomes WHERE session_id=? AND request_id=?",
                    (session_id, request_id)).fetchone()
                return json.loads(done[0]) if done else self._response(session_id, row["revision"], "running", {}, request_id)
            db.execute("INSERT INTO math_attempts VALUES (?, ?, ?, ?, ?)",
                (session_id, request_id, name, encoded, expected_revision))
            reason = ("stale session revision" if expected_revision != row["revision"] else
                "terminal session" if life["state"] != "open" else
                "another mutation is running" if life["active"] and name != "cancel" else None)
            if reason:
                response = self._response(session_id, row["revision"], "rejected", {"diagnostic": reason}, request_id)
                self._save_outcome(db, session_id, request_id, response)
                return response
            if name == "cancel":
                if life["active"]:
                    cancelled = self._response(session_id, row["revision"], "cancelled",
                        {"diagnostic": "Execution may finish; its result cannot enter graph state."}, life["active"])
                    self._save_outcome(db, session_id, life["active"], cancelled)
                db.execute("UPDATE math_lifecycle SET state='cancelled', active=NULL WHERE session_id=?", (session_id,))
                db.execute("UPDATE sessions SET revision=revision+1 WHERE session_id=?", (session_id,))
                response = self._response(session_id, row["revision"] + 1, "cancelled", {}, request_id)
                self._save_outcome(db, session_id, request_id, response)
                return response
            db.execute("UPDATE math_lifecycle SET active=? WHERE session_id=?", (request_id, session_id))
            graph = self._restore(binding, json.loads(row["state_json"]))
        # Work happens on a detached state; exceptions cannot partially publish it.
        terminal = None
        try:
            from .math_interface_helpers import normalize_support_nodes, resolve_references
            def resolve(producing_request, path):
                with self._connect() as db:
                    saved = db.execute("SELECT response_json FROM math_outcomes WHERE session_id=? AND request_id=?",
                        (session_id, producing_request)).fetchone()
                if saved is None:
                    raise ValueError("unknown or unfinished producing request in this session")
                outcome = json.loads(saved[0])
                if outcome["status"] != "completed" or outcome["revision"] > expected_revision:
                    raise ValueError("named reference requires a completed prior result")
                payload = outcome["result"]
                node = payload.get("output_nodes", {}).get(path, {}).get("id")
                proposal = payload.get("node", {})
                if node is None and proposal:
                    node = proposal.get("atomic_nodes", {}).get(path)
                    if not proposal.get("atomic_nodes") and path == "$":
                        node = proposal.get("id")
                if node is None or node not in graph.nodes:
                    raise ValueError("unknown exact output path in producing request")
                return node
            normalized, support_repairs = normalize_support_nodes(arguments) if name == "submit" else (arguments, [])
            dispatched_arguments, resolutions = resolve_references(normalized, resolve)
            repair_target = dispatched_arguments.pop("repair_target", None)
            if repair_target is not None:
                if name not in {"run_experiment", "run_calculation_graph", "retrieve_context"}:
                    raise ValueError("repair_target requires calculation, experiment or retrieval")
                if graph.obligations.get(repair_target, {}).get("status") != "open":
                    raise ValueError("repair_target must identify an open obligation in this session")
            if name == "close":
                terminal = arguments["outcome"]
                if terminal not in {"completed", "partial"}:
                    raise ValueError("close outcome must be completed or partial")
                if terminal == "completed":
                    with self._connect() as db:
                        submissions = db.execute("SELECT o.response_json FROM math_outcomes o JOIN math_attempts a USING(session_id, request_id) WHERE a.session_id=? AND a.name='submit' ORDER BY a.rowid DESC", (session_id,)).fetchall()
                    latest = json.loads(submissions[0][0]) if submissions else {}
                    if latest.get("revision") != row["revision"] or not latest.get("result", {}).get("admitted"):
                        raise ValueError("completed closure requires a current admitted submission")
                result, changed = {"outcome": terminal, "frontier": graph.frontier(), "candidate": arguments.get("candidate")}, True
                if arguments.get("repair_blocker"):
                    result["repair_blocker"] = {"description": arguments["repair_blocker"], "authority": "harness-reported; not independently verified"}
            elif name in {"record_step", "retrieve_context", "run_experiment", "run_calculation_graph", "substantiate", "substantiate_application", "submit"}:
                result, changed = self._dispatch(graph, binding, name, dispatched_arguments,
                    research_store=self.research_store, worker=self.worker)
            else:
                raise ValueError("unknown mutation")
            if resolutions:
                result["resolved_references"] = resolutions
            if support_repairs:
                result.setdefault("interface_normalizations", []).extend(support_repairs)
            if repair_target is not None:
                receipt_id = result.get("receipt_id")
                receipt = graph.receipts.get(receipt_id, {})
                performed = bool(result.get("retrieval_receipt")) if name == "retrieve_context" else receipt.get("output", {}).get("outcome") in {"executed", "timeout", "code_failed", "output_limit"}
                result["repair_attempt"] = {"target_id": repair_target, "performed": performed,
                    "receipt_id": receipt_id, "operation": name}
            status = result.get("status", "completed")
        except Exception as exc:
            result, changed, terminal = {"diagnostic": str(exc)[:500], "error": type(exc).__name__}, False, None
            from .math_calculation_assistance import ApplicationSupportError
            if isinstance(exc, ApplicationSupportError):
                result["support_diagnostics"] = exc.support_diagnostics
            if isinstance(exc, ValueError):
                diagnostic = str(exc)
                if "indexed" in diagnostic or "cardinality" in diagnostic:
                    result["repair_guidance"] = {
                        "indexed_sets": [spec.model_dump(mode="json") for spec in binding.indexed_sets],
                        "instruction": 'For every required output path in the returned indexed_sets, supply one derived contribution per member. Tag computed steps with index_scope={"set_id":...,"member":...} and combine once using op="sum", index_sum=set_id, or use aggregations. A specialized/substituted output must retain this lineage too. Products cannot serve as indexed sums. Do not invent a decomposition to satisfy the interface.',
                        "authority": "Do not split an asserted total into invented contributions. A remembered formula, convention conversion, or invariant does not independently establish its physical coefficient."}
                elif "proposal bundle" in diagnostic:
                    result["repair_guidance"] = {"atomic_dependencies": {
                        identifier: graph.nodes[identifier]["atomic_nodes"]
                        for identifier in arguments.get("depends_on", [])
                        if isinstance(identifier, str) and identifier in graph.nodes and graph.nodes[identifier].get("atomic_nodes")},
                        "instruction": "Select the relevant atomic child IDs; the controller cannot choose which mathematical claims you depend on."}
                elif "raw_definition" in diagnostic:
                    result["repair_guidance"] = {"instruction": "raw_definition binds numeric integer/rational inputs to an exact quoted value. For a symbolic input use op=symbol, its simple name as value, and exact provenance; do not use raw_definition to bind a symbol. This does not establish any coefficient or application."}
                elif "symbol input" in diagnostic:
                    result["repair_guidance"] = {"instruction": 'Use op="symbol", value="x". Do not encode expressions or unsupported assumptions as symbol values.'}
            status = "rejected" if isinstance(exc, (ValueError, TypeError, KeyError)) else "failed"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _, current, life = self._load(db, session_id)
            existing = db.execute("SELECT response_json FROM math_outcomes WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if existing:
                db.execute("INSERT INTO math_late_results VALUES (?, ?, ?, ?)",
                    (session_id, request_id, _json(result), _json(self._state(graph))))
                return json.loads(existing[0])
            if life["active"] != request_id or current["revision"] != row["revision"]:
                raise RuntimeError("session changed during reserved attempt")
            revision = row["revision"] + int(changed)
            if changed:
                db.execute("UPDATE sessions SET revision=?, state_json=? WHERE session_id=?", (revision, _json(self._state(graph)), session_id))
            db.execute("UPDATE math_lifecycle SET active=NULL, state=? WHERE session_id=?", (terminal or "open", session_id))
            response = self._response(session_id, revision, status, result, request_id)
            feedback_graph = graph if changed else self._restore(binding, json.loads(row["state_json"]))
            selected = result.get("proposal", {}).get("support_nodes") if name == "submit" else None
            result.update(self._repair_view(db, session_id, feedback_graph, (request_id, response), support_nodes=selected))
            self._save_outcome(db, session_id, request_id, response)
            return response

    def recover_interrupted(self):
        """Call only after acquiring exclusive ownership of this database process."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for life in db.execute("SELECT * FROM math_lifecycle WHERE active IS NOT NULL AND scope_json=?", (self._scope(),)).fetchall():
                _, row, _ = self._load(db, life["session_id"])
                response = self._response(life["session_id"], row["revision"], "interrupted",
                    {"diagnostic": "Execution outcome unknown; not replayed or admitted."}, life["active"])
                self._save_outcome(db, life["session_id"], life["active"], response)
                db.execute("UPDATE math_lifecycle SET active=NULL WHERE session_id=?", (life["session_id"],))
