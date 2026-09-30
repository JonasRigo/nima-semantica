# Deep Extraction

Select prepared source regions and an ontology, then ask for entities, claims and relations relevant to your question. Inspect the source coverage before treating the proposal as a document-level account.

## What you provide and receive

You receive regional extraction records, graph proposals, exact source references and unresolved issues. Separate proposals are not automatically a coherent graph: explicitly reconcile them using the consolidation modes below. The proposal remains separate from accepted project knowledge.

Call this workflow through your project’s Langflow MCP toolbox.

Request fields: `mode`, `operation_id`, `run_id`, `question`, `source_region_ids`, `source_id`, `ontology_profile`, `graph_revision`, `target_record_id`, `parent_record_id`.
For a large source, first send `{"mode":"plan","source_id":"SOURCE_ID"}`.
Planning is read-only and returns non-overlapping, exact-coverage batches of at most
32 prepared regions. Pass the selected ontology and question to planning, then submit
each returned request unchanged; operation IDs are included and replay-stable.
There is no fixed total batch count. Whole-document execution is limited to 32 regions too.
Regional requests may also include `source_id` as a membership assertion; this never
expands their region selection. For a deliberate new attempt, plan with a new
`plan_attempt_id` and execute only outstanding batches. Identical plans replay prior work.
An extraction artifact is a persistent proposal; Update Project Graph `preview`
does not save a proposal, and `prepare` returns the exact delta and approval hash
without committing it. Review Research can assess a selected target in an extraction
artifact before graph admission.
Use [Tool Guide](tool_guide.md) with `{"tool": "Deep Extraction"}` for the complete nested schema, allowed values and required fields.

## Start with the contract

This preview inspects the configured workflow without starting an investigation.
Replace it with the complete execution request after inspecting the returned contract and scope.

```json
{
  "mode": "preview"
}
```

## Why it works this way

Extraction makes an interpretation explicit enough to challenge. Use the source passages and graph inspection tools to review it before admission.

## Consolidate batches and papers

Call `consolidation_plan` with the selected `ontology_profile`, exact `graph_revision`, and
`consolidation: {"artifact_ids": ["SAVED_CANDIDATE_HASH"], "limit": 32}`. It is read-only.
The inventory gives stable candidate-scoped IDs, exact evidence references, original IDs,
component counts, a `plan_hash`, and `next_request` for remaining pages. A `query` filters
the entire selected inventory, not just the current page. By default compatible existing
project nodes are included; set `include_project_graph: false` for candidate-only work.
Selected candidates must be self-contained project graphs, including their endpoints and
parent dependencies. Equal local names in corpus and project scope are distinct identities;
cross-scope correction is not an implicit consolidation step.

Reconcile repeated identities within each paper, then across papers. Labels alone do not
establish identity: inspect definitions, assumptions, parameter ranges and cited evidence.
Keep incompatible claims separate; use ontology-valid supported relations, or retain
uncertainty in `unresolved`. Do not invent a hub or relation merely to connect components.

Submit `consolidate` with a fresh `operation_id`, the same selection/revision and:

```json
{
  "artifact_ids": ["SAVED_CANDIDATE_HASH"],
  "plan_hash": "RETURNED_PLAN_HASH",
  "merges": [{"node_ids": ["NODE_A", "NODE_B"], "canonical_id": "NODE_A", "rationale": "Evidence-backed identity decision."}],
  "links": [{"source_id": "CLAIM_A", "target_id": "CLAIM_B", "relation": "supports", "region_ids": ["ENDPOINT_EVIDENCE_REGION"], "rationale": "Source-grounded proposed relationship."}],
  "unresolved": ["Remaining ambiguity or coverage limitation."]
}
```

This example is the nested `consolidation` value, not the whole request; replace placeholders.
Merges must have disjoint, same-type members. A canonical ID selects one member and must
retain an existing project identity when present. Combining multiple already-admitted
identities is a separate explicit correction. All evidence and property variants are retained,
including provisional warnings; new cross-links remain proposed, not verified.

The result is an immutable saved `graph_proposal`, with leaf lineage and before/after
component counts. Nothing is committed. Continue with that consolidated artifact plus new
candidates; re-adding an ancestor candidate is rejected as overlapping input. Per-call
bounds (128 artifacts, 128 members per merge, 256 merges/links) are not a total paper limit.
An aggregate is bounded to 10,000 nodes, 20,000 edges and 32 MB without silent truncation.
Decision requests have a 1 MB complete-read transport bound; graph payloads travel by artifact ID.

Use Update Project Graph `prepare` on the final artifact, review/approve the exact delta,
then commit and run Analyze Graph at the returned revision. Analyze Graph defaults to
4,096 nodes/8,192 edges and 20,000 closure facts; Update Project Graph defaults to 12,288
changes. These are operator budgets, not public permissions. Large candidates travel by
artifact ID (up to 32 MB), not oversized inline JSON. A connected graph is not a certificate
of full-text coverage, correct reconciliation or scientific validity.

[Tool catalogue](../TOOLS.md) · [User guide](../README.md)
