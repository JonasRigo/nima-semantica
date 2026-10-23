# v0.4.0 expected utility and graph capacity

Version 0.4.0 prepares the maintained mainline as the baseline for subsequent experimental performance work.
It adds an advisory expected-utility library service and offline CLI, increases graph and dependency-traversal capacities, and reconciles development changes with the maintained public source history.
Publication and live-installation changes are separate operations from preparing this source candidate.

## Expected utility

Use `nima decision evaluate REQUEST --context CONTEXT` to compare explicitly supplied actions without model calls, server access or execution.
The service uses exact rational arithmetic, a declared utility policy, caller-owned eligibility, one-step information value and finite scenario sensitivity.
Shadow mode records no operational recommendation; advisory mode abstains on ties, incomplete eligible estimates or unstable rankings.
The library additionally supports authorized, atomic assessment/observation recording through an existing owner with scoped execution receipts and exact replay.
See [the guide and complete examples](EXPECTED_UTILITY.md).

The service evaluates the supplied model and candidate set.
It does not automatically estimate success probabilities, execute ranked actions, grant permissions, approve scientific claims or replace the harness's judgment.
Calibration and decision-quality evaluation remain required before relying on a policy in normal research.

## Graph and traversal capacity

Analyze Graph now defaults to and permits at most 16,000 input nodes.
Its existing edge, inference-work, time and authority limits remain independent.
Trace Claim Dependencies loads up to 16,000 nodes and 24,000 edges by default, traverses up to 4,096 visited nodes by default, and permits an operator ceiling of 16,000 visited nodes.
Depth, traversed-edge, path, work and time budgets still bound the result independently.
A full reachable subgraph can therefore coexist with incomplete path enumeration; diagnostics retain that distinction.

Large reasoning results retain their complete conclusion inventory in the authenticated receipt payload when the direct 10,000-output-ID list would overflow.
This preserves the result and its exact readback rather than increasing the receipt-list bound or truncating conclusions.
See [dependency traversal budgets](tools/trace_claim_dependencies.md#input-capacity-and-traversal-budgets).

Updating installed packages does not overwrite saved operator settings or embedded workflow source.
Update the maintained Analyze Graph/Trace Claim Dependencies components and republish the affected project workflows through the normal managed workflow procedure when adopting the new defaults.
Retain deliberate lower operator limits and original failed receipts; changed retries need fresh operation identities.
No scientific graph capacity or status is inferred from successful analysis or traversal.

## Mainline cleanup and compatibility

The candidate preserves the already-published exact graph-approval CLI, extraction timeout/diagnostic repairs, credential readiness, chat evidence selection, Inspect Corpus fix and PDF recognition transition.
Development copies that would regress those fixes are reconciled against the public mainline.
Private proof-campaign documents, planning ledgers and recovery data are excluded from release artifacts.
The historical v0.1.1 platform report is retained in its versioned file; the current platform guide now identifies the actual candidate.

Version 0.4.0 introduces no database schema migration and preserves existing graph, private-session, vocabulary and receipt identities.
Existing PDF recognition remains dependent on the configured vision model and retains its open mathematical transcription-fidelity limitation.
Historical imported-flow and optional-platform/worker limitations keep their original scope.
See [qualification status](PLATFORM_SUPPORT.md) for executed checks and remaining platform gates.
