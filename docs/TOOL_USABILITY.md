# Tool usage and recovery audit

The supported boundary is agent-led continuation, not automatic batch execution.
Tool Guide publishes research tool schemas, executable-shaped examples, cross-field
prerequisites, declared bounds and recovery guidance. `nima_math_tools` and
`nima_proof_tools` publish every private operation's typed schema and limits;
`nima_math_operations` describes atomic arithmetic operations separately.
Examples contain placeholder identities and grant no permission to execute.

## Limits and failure policy

- Maximum text/list sizes bound a single transport or computation. They are not a
  fixed total number of batches, papers, actions across sessions, or research rounds.
- Minimum sizes, identity consistency, exact quotations, content hashes, graph revisions,
  ontology bindings and approval requirements protect correctness and cannot be bypassed.
- Operator budgets bound a single authorized execution: model actions, traversal,
  worker time and resource usage. An agent cannot grant itself larger budgets.
- Independent work may be divided; joint comparisons, coherent proof submissions,
  approved graph deltas and dependency closures cannot be split implicitly.
- Parse/schema rejection occurs before downstream service execution and returns
  field/constraint diagnostics without rejected values. Internal defects are not
  relabeled as user input errors. Private MCP preserves typed schemas and error status.
- Changed work needs new IDs; uncertain delivery reuses identical arguments and IDs.
  Inspect receipts before retrying interrupted work. Never erase failed/deferred coverage.

## Public research inventory

| Tool | Continuation or safe stopping condition |
| --- | --- |
| Tool Guide | Select one tool for its exact contract; no execution. |
| Research Run | Follow `next_request` until no further history; inspect current revision before writes. |
| Load Ontology | List then resolve immutable identities; missing profiles require explicit selection/save. |
| Save Ontology | Validate/save immutable profiles; resolve version conflicts without overwrite. |
| Prepare and Index Sources | Up to 16 sources per request; track per-source preparation/index failures separately. |
| Inspect Corpus | Follow revision-bound `next_request`; restart pagination on revision change. |
| Retrieve Research Context | Narrow/reformulate ranked queries or adjust authorized budgets; not exhaustive pagination. |
| Read Evidence | Read exact selected region/artifact and follow explicit provenance references; bounded links are not complete lineage. |
| Search for Counterexamples | Further scoped searches are separate attempts; budget exhaustion is not proof. |
| Verify Lean | Repair exact target/environment from diagnostics; do not split coherent submissions silently. |
| Deep Extraction | Plan exact-coverage batches and submit unchanged; preserve every proposal and gap. |
| Analyze Graph | Choose a saved smaller candidate or request operator budget changes; no public seed selector. |
| Trace Claim Dependencies | Retain boundary/unresolved nodes; separate traces do not imply complete closure. |
| Substantiate Graph Snapshot | Independent target groups retain shared revision and individual evidence/findings. |
| Update Project Graph | Prepare exact delta for approval; re-prepare after conflicts, never implicitly split approved changes. |
| Hypothesis Generation | Continue selected objectives or restate existing hypotheses; no mandatory round count. |
| Compare Research Objects | Joint comparison bounds require deliberate narrower scope, not automatic chunking. |
| Deep Research | Continue selected questions/passes; preserve temporary-read provenance and unanswered questions. |
| Review Research | Independent target groups retain exact content revisions and per-target outcomes. |
| Save Research Analysis | Persist selected report and evidence; resolve missing substantiation rather than dropping findings. |
| Draft Lean | Repair within pinned environment/target and configured budgets; report unresolved goals. |

## Private mathematics and proof inventory

Calculate Mathematics, Draft Proof and Conduct Proof retain private state; they do
not automatically admit claims into a project. Every operation is listed below.

| Operations | Usability and continuation boundary |
| --- | --- |
| Both: `open` | Replay identical creation bindings; different targets require different creation IDs. |
| Both: `status`, `frontier` | Inspect current revisions and open obligations; counts are not certification. |
| Both: `inspect`, `export` | Read exact private nodes/receipts or full private state; no graph publication. |
| Math: `record_step`; proof: `record_node` | Record explicit dependencies, use returned revision for the next mutation. |
| Both: `retrieve_context` | Retain scoped references and inspectable evidence; retrieval mutates session state. |
| Both: `run_experiment` | Worker bounds remain operator-owned; successful execution is not proof. |
| Math: `run_calculation_graph` | 128 new steps per call; expanded closure has separate 512-step/32000-character bounds. Reuse explicit prior outputs; do not discard dependencies to fit. |
| Math: `substantiate`, `substantiate_application` | Exact support and applicability evidence remain mandatory; rejection identifies unresolved obligations. |
| Proof: `resolve_obligation`, `attach_receipt` | Explicit support dependencies and authentic same-scope receipts; retain uncertified status. |
| Both: `submit` | Select exact conclusion/support; missing obligations remain visible, never auto-select alternative conclusions. |
| Both: `close`, `cancel` | Close partial with unresolved work when appropriate; interrupted execution is not blindly retried. |

## Regression coverage and deployment

Contract tests validate published examples and all catalog entries. Adapter tests
exercise rejected public overrides, malformed input, no downstream execution,
private MCP discovery/rejection and session lifecycles. Extraction tests cover
source membership, exact plan coverage and replay/new attempts. Corpus and run
tests exercise pagination, stale state and retained failed/interrupted outcomes.
Existing provenance, graph approval, provider-adapter and worker tests remain required.

Refresh generated contract references with `deploy/document_contracts.py --write`
in the configured Python environment after contract changes. Refresh flow snapshots
after component changes. Do not replace an installed flow while it is executing;
preserve operator configuration and user-edited flows during deployment.
