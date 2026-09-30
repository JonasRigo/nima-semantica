# Files and chat with the knowledge base

## Ingest a file

```sh
nima ingest papers /path/to/paper.pdf
nima ingest papers /path/to/notes.md --project my-research
```

The first command publishes a shared corpus source. The second stores project-specific notes.
Supported inputs are individual local PDF, text, Markdown, LaTeX, JSON and HTML files,
or policy-approved HTTP(S) URLs, including arXiv HTML. Batches accept up to 16 inputs;
directories are not accepted. Papers run sequentially; batch output reports failures
per input and returns a nonzero exit code if any requested index is not ready.
The current preparation limit is 20 MB per file.
PDFs require the configured advanced PDF normalizer, which preserves page provenance and formula diagnostics.
The command returns source identifiers, receipts and actual indexing status.
Repeated identical ingestion reuses its recorded result; failed or partial normalization remains visible.

Lexical indexing is the default. Use `--index-mode vector` after configuring an embedding profile.
Source preparation and ontology extraction are separate operations: ingestion makes material searchable without automatically accepting its claims into a project graph.

## Full and fast preparation

CLI progress is written to stderr as stage messages; stdout remains JSON.
`index_ready: true` reports indexing success even when fast preparation retains
`status: partial` for fidelity warnings. A `failed_stage` identifies an actual failure.
After correcting a failed attempt, add `--retry` to start a new receipted attempt;
the same command without this flag replays its saved result. Existing evidence and
failed receipts are retained. New source regions have a hard 1,800-character bound
with exact source offsets; this is not a model-specific token-limit guarantee.

```sh
nima ingest papers paper.pdf --mode full --index-mode vector
nima ingest papers paper-a.pdf paper-b.pdf --mode fast
nima ingest papers https://arxiv.org/html/2501.12345v2 --mode fast --format html
```

`--mode full` is the default; `--format auto` detects PDF/HTML, while `pdf` and
`html` assert the intended format. URLs follow the installation's acquisition
policy, including host restrictions, robots rules, redirect checks and size/time
limits. Prefer an explicitly versioned arXiv URL. Requested/resolved URLs, source
bytes and hashes are retained in acquisition provenance. No PDF fallback or slower
preparation is performed silently. Unversioned URLs identify the archived bytes,
not a guaranteed immutable arXiv version; use a versioned URL for reproducibility.

Full HTML preparation retains headings, reference targets and equation markup,
including arXiv MathML `alttext`/TeX annotations. Images and undecoded equations
are diagnosed as unresolved; HTML is not presumed scientifically trustworthy.

Fast mode uses the existing lightweight reader (on macOS, PDF text extraction
still requires the isolated fast-PDF Docker image). It bypasses advanced PDF
normalization, not provenance validation. Its source descriptors, regions and
citations retain `fast_provisional` quality and warnings. Lexical/vector indexing
and graph extraction are allowed. `include_provisional: false` on Retrieve Research
Context excludes provisional regions and explicitly provisional graph objects.
Embedding configuration alone does not enable vector indexing.

Promotion to shared corpus scope requires explicit approval and retains evidence
quality and scientific status. Sources already shared in the corpus can support
promoted provisional claims; project-private evidence cannot be shared implicitly.
Full preparation of the same original file creates separate regions and a
`SourcePreparationUpgrade` link. The old evidence remains immutable. Reconcile
citations and approve replacement graph changes explicitly; neither parsing nor
promotion certifies a scientific assertion. Temporary Deep Research fast passages
remain separate and are not made searchable by this change.

Use the [Ingest papers skill](skills/nima-ingest.md) to coordinate these steps with
an exact ontology and a selected extraction question.

## Chat with corpus literature and project notes

Ask the harness to use **Chat with Knowledge Base** (`nima-corpus-chat`).
Specify whether you want corpus literature, project notes, project knowledge or a combination.
For example: “Using this project's notes and corpus papers, explain the assumptions behind the current calculation and cite the exact passages.”

The skill uses Inspect Corpus for inventory/readiness, Retrieve Research Context for passages and Read Evidence for exact regions or saved artifacts.
It uses graph analysis and dependency tracing when the question concerns recorded relationships.
Ordinary KB chat does not start a broad research campaign or modify the knowledge graphs.
If evidence is missing, the answer identifies the searched scope and missing material.

## Build project knowledge

Use Deep Extraction on selected prepared evidence when ontology-bound entities, claims and relations are useful.
For long or multiple papers, regional proposals are intermediate results. Use Deep Extraction
`consolidation_plan` and `consolidate` to reconcile repeated identities within papers and
then across papers, retaining source evidence, property variants and unresolved ambiguities.
Cross-paper relations need explicit ontology-valid decisions backed by source evidence;
no automatic name-based merge or artificial connectivity is applied. See
[consolidation guidance](tools/deep_extraction.md#consolidate-batches-and-papers).
Inspect the proposal and its exact source references. Use Analyze Graph and Trace Claim Dependencies to understand its represented structure.
Apply a selected change through Update Project Graph with its exact approval and current revision.
Corpus material remains shared; project notes and project records retain their project scope.
