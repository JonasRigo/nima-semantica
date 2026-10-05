"""Shared typed arguments for native, MCP, and inspectable canvas entry points."""
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from .math_task_contract import MathTaskFact
from .math_indexed_count import IndexedSet
from .math_graph_program import _ARITY


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenSession(Arguments):
    request_id: str = Field(min_length=1, max_length=128, description="Stable creation request ID; identical retries resume the same session.")
    task: str = Field(min_length=1, max_length=24000)
    required_paths: list[str] = Field(min_length=1, max_length=128)
    task_facts: list[MathTaskFact] = Field(default_factory=list)
    indexed_sets: list[IndexedSet] = Field(default_factory=list)
    session_id: str | None = Field(default=None, description="Existing session to resume with identical binding; omit to create.")


class Session(Arguments):
    session_id: str


class Frontier(Session):
    support_nodes: dict[str, str] | None = Field(default=None, description="Optional current candidate: required output path to exact node ID. Inspection only; does not alter or qualify nodes. Otherwise view the latest candidate batch.")


class SourceSpan(Arguments):
    start: int = Field(ge=0, description="Unicode character offset, inclusive")
    end: int = Field(gt=0, description="Unicode character offset, exclusive")


class Inspect(Session):
    kind: Literal["node", "receipt", "action"]
    identifier: str
    source_span: SourceSpan | None = None


class Mutation(Session):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class OutputReference(Arguments):
    request_id: str = Field(min_length=1, max_length=128, description="Completed producing request in this same session; never the latest request implicitly.")
    output_path: str = Field(min_length=1, description="Exact output key; $ for a scalar record_step value.")


NodeReference = str | OutputReference


class RecordStep(Mutation):
    kind: Literal["definition", "hypothesis", "exploration", "assumption", "derivation", "claim"]
    statement: str = Field(min_length=1, max_length=1200)
    value: Any
    depends_on: list[NodeReference]
    supersedes: NodeReference | None = None


class Experiment(Mutation):
    source: str = Field(min_length=1, max_length=30000)
    depends_on: list[NodeReference]
    purpose: str
    repair_target: NodeReference | None = Field(default=None, description="Open obligation being investigated; links this attempt, not a claim of resolution.")


class AtomicStep(Arguments):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    op: Literal["add", "subtract", "multiply", "divide", "power", "negative", "simplify", "differentiate", "kronecker", "trace", "transpose", "adjoint", "substitute", "sum", "matrix", "symbol", "integer", "rational", "imaginary_unit"]
    args: list[str] = Field(default_factory=list, description="Prior local step IDs in this batch; substitute takes expression, old symbol, new value.")
    value: Any = Field(default=None, description='symbol: a simple string such as "x"; integer: integer; rational: [numerator,denominator]; matrix: [rows,columns]. Omit on computed operations.')
    provenance: list[NodeReference] = Field(default_factory=list)
    assumption: str | None = Field(default=None, min_length=1, max_length=300, description="For an unsupported input only: explicitly state the assumption. Controller records a provisional premise and links it; it cannot qualify submission. Otherwise cite exact input provenance. Derived steps inherit operand provenance.")
    meaning: str = Field(min_length=1, max_length=300)
    application: Literal["algebraic", "physical_rule"] = "algebraic"
    index_scope: dict[str, str] | None = Field(default=None, description='Member contribution annotation: {"set_id":"branches","member":"left"}; use the exact set and members returned by status.')
    index_sum: str | None = Field(default=None, description='Set ID on add/sum ONLY. Arguments must be separately computed contributions tagged with index_scope, one per declared member. Never tag a product as a sum.')
    index_count_of: str | None = None
    raw_definition: dict[str, str] | None = Field(default=None, description="Exact numeric input definition only (integer/rational), with evidence_id and quote. Not for symbolic names: use symbol value and provenance. Arithmetic numbers do not establish domain coefficients.")


class IndexedAggregation(Arguments):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    set_id: str = Field(description="Exact harness-declared indexed set ID.")
    contributions: dict[str, str] = Field(description="Each declared member exactly once, mapped to an existing local step ID. Values and decomposition must be supplied by the caller, never inferred.")


class Calculate(Mutation):
    steps: list[AtomicStep] = Field(default_factory=list, max_length=128, description="At most 128 new atomic steps. Controller-expanded reuse/aggregation is separately bounded to 512 steps and 32000 source characters.")
    reuse: dict[str, NodeReference] = Field(default_factory=dict, description="Local operand alias to an executed compiled node/output. Controller reexecutes the exact closure and retains original operation identities and support after exact observation agreement; unresolved support stays unresolved.")
    comparison_bindings: dict[str, str] | None = Field(default=None, description="Optional local step ID to original captured variable name for reconstructs_receipt. Default compares shared names; diagnostics only, not evidence promotion.")
    outputs: dict[str, str]
    purpose: str
    reconstructs_receipt: str | None = Field(default=None, description="Optional successful exploratory receipt in this session whose method this atomic plan reconstructs. Audit link only: provide actual operations and exact input provenance; no Python output is promoted or assumed equivalent.")
    repair_target: NodeReference | None = Field(default=None, description="Open obligation this independent calculation investigates; do not assume the disputed premise.")
    aggregations: list[IndexedAggregation] = Field(default_factory=list, max_length=16, description="Optional controller-built member annotations and sums. Steps may reference declared aggregation IDs; controller orders explicit dependencies. No indexing is required for ordinary calculations.")


class Retrieve(Mutation):
    mode: Literal["lexical", "vector", "hybrid"] | None = None
    query: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=2000)
    repair_target: NodeReference | None = Field(default=None, description="Open obligation for this targeted evidence search; search alone does not resolve it.")


class Substantiate(Mutation):
    hypothesis_id: NodeReference
    evidence_id: NodeReference
    rationale: str


class Application(Mutation):
    operation_id: NodeReference
    evidence_id: NodeReference
    rationale: str
    method_source_id: NodeReference | None = None
    method_quote: str | None = None
    method_span: SourceSpan | None = Field(default=None, description="Select the exact source interval instead of transcribing method_quote; controller copies its text and provenance. Applicability checks remain unchanged.")


class Submit(Mutation):
    answer: Any = Field(default=None, description="Omit to let the controller assemble exact selected graph values. Explicitly supplied JSON, including null, is still checked unchanged.")
    support_nodes: dict[str, NodeReference | Annotated[list[NodeReference], Field(min_length=1, max_length=1)]] = Field(description="One exact node per required output path. Extra valid local references are retained as supplementary evidence, not answer fields or qualified claims. Missing required paths still fail. A singleton list is unwrapped; multiple candidates are never chosen automatically.")


class Close(Mutation):
    outcome: Literal["completed", "partial"]
    candidate: Any = None
    repair_blocker: str | None = Field(default=None, min_length=1, max_length=1200, description="For partial closure without a substantive repair attempt: concrete capability, evidence, authorization or resource blocker. Recorded as harness-reported, not independently verified.")


TOOLS = {
    "open": (OpenSession, "Create or resume an immutable task-bound private calculation session."),
    "status": (Session, "Inspect lifecycle, task binding and current revision."),
    "frontier": (Frontier, "Request qualification of the selected candidate: inspect its unresolved obligations; full historical audit remains available through export/inspection."),
    "inspect": (Inspect, "Read an exact node, source passage, execution receipt or action attempt."),
    "export": (Session, "Export the private graph; does not publish to research graphs."),
    "record_step": (RecordStep, "Record one provisional mathematical step with exact dependencies."),
    "retrieve_context": (Retrieve, "Retrieve exact scoped literature, returning previews and inspectable provenance."),
    "run_experiment": (Experiment, "Run exploratory Python in the isolated symbolic worker; observations are not proofs."),
    "run_calculation_graph": (Calculate, "Compile and execute atomic symbolic operations with graph-linked provenance."),
    "substantiate": (Substantiate, "Propose evidence for an unresolved claim under existing graph admission rules."),
    "substantiate_application": (Application, "Propose exact evidence supporting applicability of a physical operation."),
    "submit": (Submit, "Select support_nodes and omit answer for controller-assembled exact values; unchanged admission checks still apply, and admission is not a mathematical truth certificate."),
    "close": (Close, "Close with a current admitted result or an explicit partial candidate and obligations."),
    "cancel": (Mutation, "Cancel the session; running work cannot publish late results."),
}

for _operation, (_schema, _description) in list(TOOLS.items()):
    _sequencing = (" Read-only; may run concurrently." if _operation in {"status", "frontier", "inspect", "export"}
        else " Session mutation: run sequentially, await its returned revision before the next mutation (including retrieval).")
    TOOLS[_operation] = (_schema, _description + _sequencing)


def operation_catalogue():
    return {"contract_version": "math-session-v1", "operations": {
        name: {"min_args": bounds[0], "max_args": bounds[1]} for name, bounds in _ARITY.items()},
        "notes": ["symbol: value is a simple string, e.g. x; SymPy assumption dictionaries are not supported; use the separate assumption field for an explicitly provisional premise",
            "Treat the task as a novel calculation: retrieve atomic definitions and general methods rather than relying on a remembered final formula. Derive consequential coefficients and cancellations.",
            "Explore before qualification: open support does not prevent independent calculation. Request frontier when ready to qualify selected outputs; only existing substantiation rules resolve obligations. Routine responses are compact; inspect/export retain full history.",
            "Every input needs exact provenance or an explicit assumption. Exact task-symbol bindings and arithmetic 0,1,i origins are controller-recorded; they do not justify domain interpretations. Derived steps inherit actual operand dependencies.",
            'Optional aggregations=[{"id":"total","set_id":"branches","contributions":{"left":"left_value","right":"right_value"}}] generates annotations and sum from explicit existing step IDs; never supplies missing terms.',
            'Graph reference fields accept a node ID or {"request_id":"prior-request","output_path":"answer"}; references are exact, session-local and retain the original evidence status.',
            "matrix: declare entry steps first, args in row-major order, value=[rows,columns]",
            "Reuse compiled atomic definitions with reuse={local_alias: node_reference}; their dependency closure and unresolved support are retained. No exploratory stdout is promoted.",
            "Reconstruction compares captured observations by shared names or optional comparison_bindings. Inspect first_difference and calculation_diagnostics; representation matches are not scientific certification.",
            "Inspect exact source nodes for source_line_spans; method_span copies the selected quotation without retranscription. Applicability remains separately checked.",
            "substitute: args=[expression,old_symbol,new_value]",
            "provenance contains exact graph IDs; args are prior operation IDs",
            "Free-form Python remains exploratory; unknown operations are not silently certified.",
            "record_step returns provisional claims, not a bypass for rejected calculations. Use node.atomic_nodes[path] for bundle dependencies.",
            "Read status for exact task facts and indexed sets. Derive each contribution before summing; never invent a decomposition to satisfy the schema."],
        "indexed_example": {
            "set": {"set_id": "branches", "members": ["left", "right"]},
            "steps": [
                {"id": "left_term", "op": "simplify", "args": ["previous_left"], "meaning": "Previously derived left contribution", "index_scope": {"set_id": "branches", "member": "left"}},
                {"id": "right_term", "op": "simplify", "args": ["previous_right"], "meaning": "Previously derived right contribution", "index_scope": {"set_id": "branches", "member": "right"}},
                {"id": "total", "op": "sum", "args": ["left_term", "right_term"], "meaning": "Sum each contribution once", "index_sum": "branches"}],
            "note": "previous_left and previous_right must already be defined in the same batch; this template provides no mathematical values or evidence."},
        "example": [{"id": "one", "op": "integer", "value": 1, "meaning": "Arithmetic unit"},
            {"id": "two", "op": "add", "args": ["one", "one"], "meaning": "Sum"}]}


def invoke(service, operation: str, arguments: dict):
    """One shared adapter for MCP and canvas, with no provider dispatch."""
    request = TOOLS[operation][0].model_validate(arguments)
    from .math_response_views import response_view
    if operation == "open":
        return response_view(service.open(request.task, request.required_paths, request.task_facts,
            request.indexed_sets, request.session_id, request.request_id), operation)
    data = request.model_dump(mode="json", exclude_none=True)
    sid = data.pop("session_id")
    if operation == "inspect":
        kind = data["kind"]
        key = "request_id" if kind == "action" else kind + "_id"
        return service.read(sid, "inspect_" + kind, {key: data["identifier"], **({"source_span": data["source_span"]} if "source_span" in data else {})})
    if operation in {"status", "frontier", "export"}:
        return response_view(service.read(sid, operation, data), operation)
    rid, revision = data.pop("request_id"), data.pop("expected_revision")
    # Preserve explicit JSON null values in user claims and answers.
    if isinstance(request, RecordStep):
        data["value"] = request.value
    if isinstance(request, Submit) and "answer" in request.model_fields_set:
        data["answer"] = request.answer
    response = service.mutate(sid, rid, revision, operation, data)
    if operation == "retrieve_context" and "source_nodes" in response["result"]:
        import copy
        response = copy.deepcopy(response)
        response["result"].pop("retrieval_receipt", None)
        for node in response["result"]["source_nodes"]:
            text = node.pop("text")
            node.update(preview=text[:800], preview_truncated=len(text) > 800)
    return response_view(response, operation)
