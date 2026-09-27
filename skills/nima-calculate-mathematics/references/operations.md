# Atomic calculation interface

MCP tools accept typed fields inside a `request` object.
Langflow canvases accept the same inner object as request JSON.
Use the operation catalogue for supported names and arities; it is generated from the compiler.

```json
{
  "session_id": "returned-session-id",
  "request_id": "sum-1",
  "expected_revision": 0,
  "steps": [
    {"id": "one", "op": "integer", "value": 1, "meaning": "Arithmetic unit"},
    {"id": "two", "op": "add", "args": ["one", "one"], "meaning": "Sum of two units"}
  ],
  "outputs": {"answer": "two"},
  "purpose": "Compute the requested sum"
}
```

Each `args` entry refers to an explicitly declared step or aggregation in this calculation batch.
The controller orders dependencies before compilation, preserving argument order for noncommuting operations; forward references are allowed, cycles and missing references are not.
Each `provenance` entry refers to an exact graph node, or an earlier local input dependency.
Output paths identify requested answer fields; the response supplies graph IDs for submission.
Do not fabricate IDs or receipt links.

Every input needs a traceable origin.
Choose its origin deliberately: construct pure arithmetic from literal operations; cite an exact task/source definition for a defined quantity; declare an unresolved assumption for a proposed domain input.
For example, arithmetic two can be computed as one plus one, but that does not establish two as a multiplicity, probability coefficient, or physical prefactor.
Exact task-symbol spellings are bound by the controller, and arithmetic `0`, `1` and `i` receive controller-owned literal origins.
For another named numeric definition, provide `provenance` and an exact `raw_definition` quote, for example `{"evidence_id":"task","quote":"c = 2"}` on the input `c`.
`raw_definition` applies only to numeric integer/rational inputs, not symbols; bind symbolic names with their `value` and `provenance`.
For an unproved input, use `assumption: "Unproved coefficient; derive before submission"` instead of fabricated provenance.
The controller records and links that assumption; its descendants remain unsupported until the assumption is independently resolved.
Operations inherit operand provenance, so do not repeat references on every multiplication.
An arithmetic zero is not evidence that a domain quantity vanishes; compute the cancellation or retain an explicit assumption.

Use optional `aggregations` to sum explicitly supplied indexed contributions; do not split a remembered total into invented member values.
Every output listed in an indexed set's `required_paths`, including a limit or specialized output, must retain that member-sum lineage; a separately asserted formula cannot substitute for it.
Reference a prior output by `{"request_id":"prior-calculation","output_path":"answer"}` in provenance/dependency/support fields to avoid copying graph IDs.
Neither convenience changes the evidence status.
Submit one supporting reference per output path; a singleton list is unwrapped automatically, but multiple references are not selected or merged.

Routine responses are compact: results, execution diagnostics and a provisional support summary.
Request `frontier` when ready to qualify a candidate; it exposes its prioritized `repair_task`, current obligations and recorded/attempted/resolved progress.
Historical obligations remain in the full graph/export and immutable action outcomes, not in the default qualification backlog.
Use `frontier` with `support_nodes: {"answer": "exact-node-id"}` for an explicit selection; no node is modified or qualified by selection.
Without explicit selection, the controller retains the latest complete output batch or later completed submission selection; partial updates are reported as pending and never merged automatically.
If no complete batch or submission exists, the latest partial batch is shown with its missing paths.
Pass its `target_id` as `repair_target` on a calculation, experiment, or retrieval that actually investigates the objection.
This is an audit label, not a provenance dependency or evidence certification.
Inspect the supplied context and determine the value from definitions without assuming the recorded claim is right.
Find the smallest missing relation, state its inputs and connection to the requested result, and derive it before comparing with the previous attempt.
The default repair task omits the old value and persuasive claim text; exact prior nodes and receipts remain available through inspection.
Use independent calculation or retrieve the missing atomic definition/general method, then use existing substantiation with qualifying evidence; a differing result requires a revised candidate, not support for the old value.
If work cannot proceed, close partial with `repair_blocker` identifying the precise missing prerequisite and why authorized calculation/retrieval cannot supply it.
An unsuccessful formula search alone does not establish this: symbolic exploration may still be available.
The blocker remains a harness-reported explanation, not a verified fact or a reason to clear the obligation.

For a successful exploratory receipt, inspect its `compilation_handoff`, then pass `reconstructs_receipt: "exact-receipt-id"` on a new atomic calculation that reconstructs the actual method.
The controller records the original source hash and new compiled calculation connection while keeping the exploration immutable and unqualified.
Do not treat the link as a proven equivalence; definitions, assumptions, and operations must still be explicit in the new graph.
Small method calculations can use output keys such as `method.coefficient` without replacing the selected final answer.
Use their exact compiled output IDs with existing substantiation tools; physical applicability still requires the exact method-source quote and independent qualified lineage specified by `substantiate_application`.
If all current required outputs have support, try `submit` with those nodes rather than closing partial because unrelated historical obligations remain open.
Omit `answer` on `submit` to have the controller assemble exact selected values from `support_nodes`; admission checks remain unchanged.
An explicitly supplied answer, including JSON null, remains checked as supplied.
For scalar or arbitrary JSON output use `$`; dotted output paths assemble nested object keys without guessing array shape.

For `substitute`, supply three operands: expression, old symbol, new value.
For a matrix, first declare entries, then supply their IDs in row-major order and `value: [rows, columns]`.
For noncommuting operators, preserve operand order.
Use `application: "physical_rule"` where an operation applies a physical assumption; algebraic consistency cannot discharge that assumption.
Optional indexed-count and raw-definition fields apply only when supported by the task and its evidence.

The atomic compiler supports fewer operations than exploratory SymPy/Python.
Exploration supports general calculation, but does not automatically become admissible graph evidence.
Keep this limitation explicit when returning partial results.

## Controller-owned assistance

Use `reuse: {"A": "existing-compiled-node-id"}` (or an exact producing-request/output reference) on a calculation, then use `A` as a local operand.
The controller recompiles the original atomic dependency closure and retains the original node as provenance; this neither copies an asserted result nor clears unresolved premises.
Ordinary algebraic scaling and repeated terms do not independently create physical-applicability obligations in the MCP service.
Explicit `physical_rule`, unsupported input provenance and indexed-count checks remain enforced; label domain applications honestly rather than disguising them as algebra.

Successful new explorations expose captured named top-level mathematical values through receipt inspection.
With `reconstructs_receipt`, shared local/variable names are compared automatically; use optional `comparison_bindings: {"local_step": "original_variable"}` when names differ.
This compares exact observed representations only: different expressions may still be mathematically equivalent, and matching values do not establish scientific truth.
Older receipts without captured values remain inspectable but have no automatic comparison coverage.
No exploratory value is imported as an authoritative definition.

Use `inspect` with `kind: "node"`, its source identifier and `source_span: {"start": 0, "end": 40}` to inspect a selected interval.
Offsets are half-open Unicode character offsets, not bytes.
Supply the same interval as `method_span` with `method_source_id` on `substantiate_application`; the controller copies exact text and preserves its source hash and provenance.
If also supplied, `method_quote` must agree exactly; no fuzzy replacement or automatic applicability approval is performed.

All session mutations, including retrieval, must be sequential and use the revision returned by the preceding mutation.
Read-only status/frontier/inspection/export calls may run concurrently.
During exploration, unresolved support is a reason to investigate a premise, not a requirement to complete quotation/substantiation before the next calculation.
