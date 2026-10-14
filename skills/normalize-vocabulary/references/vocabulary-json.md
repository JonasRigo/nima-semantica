# Structured vocabulary and optional guidance

Use the CLI command array in `.nima/project.json` if `nima` is unavailable on PATH.
All commands below use the selected installation, corpus and project.

Obtain the revision-bound header:

```bash
nima vocabulary header PROJECT --corpus CORPUS
```

Retain `schema_version`, `corpus_id`, `project_id`, `graph_revision`, `graph_snapshot_hash`, `ontology_digests` and `generated_at` exactly.
Add `coverage_limitations` and `entries` from the inspected graph and exact evidence.
Each entry has a stable `entry_id`, preferred `term`, complete `definition`, and a `status` of `source_defined`, `project_convention`, `proposed` or `unresolved`.
Optional fields are `aliases`, full scoped `concept_ids`, `notation`, `domain`, `assumptions`, `qualifiers`, `distinctions` and exact `evidence` references.
`source_defined` entries require evidence; conventions inferred by the harness remain proposed or unresolved until explicitly selected as project conventions.
Preserve contradictory source conventions as separate entries or explicit distinctions.
JSON escaping changes representation only; exact notation and quoted source text must survive readback.

Seal a prepared draft and generate the matching readable reference:

```bash
nima vocabulary seal DRAFT.json --corpus CORPUS --project PROJECT --output CORPUS-vocab.json --markdown CORPUS-vocab.md
nima vocabulary validate CORPUS-vocab.json --corpus CORPUS --project PROJECT
```

Sealing computes the vocabulary content hash, validates the pinned graph and exact evidence, and generates both files from identical structured content.
It does not create definitions or publish the reference.
Refresh the header and inspect changed concepts if the graph became stale; do not substitute a new revision without reviewing those changes.

When project configuration is requested:

```bash
nima project configure PROJECT --corpus CORPUS --normalized-reasoning on --vocabulary CORPUS-vocab.json --path WORKSPACE
nima project configure PROJECT --corpus CORPUS --normalized-reasoning off --path WORKSPACE
```

The first command publishes an immutable scoped snapshot through the existing artifact owner and records a recoverable configuration transition.
Configuration applies to new investigations; existing private sessions retain their original pin.
Reload configured owners after their configuration files change.

Read bounded context and complete selected entries:

```bash
nima vocabulary context PROJECT --corpus CORPUS --query "Selected research question"
nima vocabulary read PROJECT --corpus CORPUS --entry ENTRY_ID
```

New meanings remain provisional and can be used during exploration.
The existing Save Research Analysis tool accepts `mode: "propose_vocabulary"`, an explicit `operation_id`, current `graph_revision` and `vocabulary_extension`.
An extension contains `proposal_id`, `revision`, optional previous-record `supersedes`, corpus/project IDs, `base_snapshot_hash`, `task_id`, `kind`, `term`, `definition`, `motivation`, related entry IDs, changed assumptions, evidence or `speculative_basis`, competing interpretations and open questions.
Kinds are `new_term`, `new_concept`, `distinction`, `definition_challenge` and `borrowed_terminology`.
Revisions and withdrawals retain prior records; persistence never renames concepts or admits scientific claims.
