---
name: nima-ingest
description: Ingest a supplied list of papers or arXiv HTML URLs into NIMA, choose full or explicitly provisional fast preparation, and coordinate extraction with a selected ontology and approved graph updates.
---

# Ingest papers

Read [the shared tool and state contract](../_shared/tools.md) before acting.

Establish the paper list, corpus/project scope, desired ontology, and preparation mode.
Full preparation is the default; use fast mode when requested. Ask whether a speed preference
means provisional extraction if that tradeoff is unclear. Keep the existing embedding choice;
do not assume configuring an embedding model means vector indexing was requested.

## Prepare and inspect

Use the configured NIMA CLI environment, not an unrelated system Python.
For up to 16 explicit files/URLs per call (repeat for larger lists; no fixed total batch count):

```sh
nima ingest CORPUS PAPER_OR_URL ... --mode full --index-mode vector
nima ingest CORPUS PAPER_OR_URL ... --mode fast --index-mode lexical
```

Omit `--project` for shared corpus papers. Use `--project PROJECT` only for private project
material; do not broaden visibility to make promotion easier. Inputs are individual files or
policy-approved URLs, not directory trees. Prefer a supplied versioned arXiv HTML URL when
available. Do not invent a version, fetch arbitrary linked resources, or silently substitute
PDF/full preparation for a failed fast/HTML request. The CLI archives acquisition provenance.

Process batch outcomes per paper; a successful neighbor does not repair a failed input.
Retain source IDs, region IDs, original and normalized hashes, receipts, extraction quality,
and unresolved diagnostics. Inspect readiness before repeating work. Report partial failures
and use the same input/options for replay; do not run concurrent corpus writers.

## Ontology and graph

Use Load Ontology to resolve the requested exact name/version or digest. If missing, draft
and validate a profile with Save Ontology and obtain authorization before saving it.
Use Deep Extraction on prepared regions with that ontology and the user's extraction focus.
First call Deep Extraction with `{"mode":"plan","source_id":"SOURCE_ID"}` to obtain
bounded region batches, passing the selected `ontology_profile` and extraction `question`
to the planning request. Submit returned batch objects unchanged: they include regional
scope and replay-stable operation IDs. An optional `source_id` in regional mode checks
membership without expanding scope. For a deliberate new attempt, re-plan with a new
`plan_attempt_id` and select only the failed/deferred work; do not repeat successful batches.
Track coverage across every batch; a successful
abstract batch is not full-paper extraction. Do not hand-author a replacement graph when
extraction fails and present it as tool-generated output.
Without a focus, propose one from the paper/topic rather than inventing scientific priorities.
Extraction creates proposals; Update Project Graph requires explicit approval of the exact delta.
For a coherent graph across batches or papers, read
[consolidation guidance](references/consolidation.md). Regional proposals must be reconciled
explicitly; repeated commits alone do not resolve identities or create cross-paper relations.
`preview` is non-persistent request inspection. Use `prepare` with the extracted graph
artifact to obtain the exact delta/hash for approval; preserve the extraction artifact ID.
If retrieval reports stale projections, refresh with the configured CLI's
`project refresh PROJECT --corpus CORPUS` and retry against the current revision.

Fast-prepared regions are searchable but not fully validated. Preserve `fast_provisional`
labels in evidence locators, graph nodes/edges, citations, summaries, and exports. Never omit
the warning merely because a graph was approved, promoted, or cites an exact text span.
Use `include_provisional=false` in Retrieve Research Context when the user excludes fast evidence.

Corpus promotion is a separate, explicitly approved sharing operation, not verification.
Preview with `nima project promote PROJECT --corpus CORPUS --rationale "REASON"`.
Show the selected nodes/edges and preparation quality to the user. Only after approval,
repeat the same command with `--approve-proposal HASH --approved-by OPERATOR`.
Retain preparation quality, scientific status, ontology, and promotion origin.
Private supporting evidence must not be exposed implicitly. Promotion currently selects the
project-owned graph; do not use it when the user only approved an unspecified subset.

## Upgrade and report

For an upgrade, run full preparation on the same original source and inspect the
SourcePreparationUpgrade linkage. Existing fast regions and graph citations remain immutable.
Reconcile important quotations/formulas against the new regions, rerun focused extraction,
and request approval for the exact replacement graph delta. Full parsing is not scientific proof.

Report each paper's mode, preparation/index status, ontology, extraction/graph status, warnings,
and remaining actions. Deep Research's temporary ResearchFastPassage records are not these
indexed sources; prepare their retained originals before treating them as corpus evidence.
