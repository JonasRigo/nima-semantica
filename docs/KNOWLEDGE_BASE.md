# Files and chat with the knowledge base

## Save PDF OCR without ingestion

```sh
nima ocr /path/to/paper.pdf --output /path/to/transcription.md
```

This command saves Markdown with LaTeX equations and a matching `transcription.ocr.json` evidence sidecar.
The destination is a file path; missing parent directories are created.
Use `--force` to replace existing exports.
OCR uses the configured PDF service and its dedicated Langflow vision workflow, without creating a corpus, project or embedding index.
Progress goes to stderr and the result summary goes to stdout as JSON.
Exit codes are 0 for complete normalization, 1 for saved partial normalization with unresolved regions, and 2 for an error.

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
Full PDF preparation requires the configured PDF normalizer and its dedicated Langflow vision workflow, preserving page provenance and formula diagnostics.
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
nima ingest papers paper.pdf --mode full --ocr-output-dir /path/to/transcriptions
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

Fast preparation has no page-count limit.
The 4 MB extracted-text limit and the PDF reader's existing input-size, memory, CPU and timeout limits still apply.
On managed macOS installations, rerun `nima setup --provision` after updating NIMA to rebuild the isolated fast-PDF reader.
After updating NIMA, use `--retry` for a source whose earlier fast preparation failed at the page limit; the earlier failure receipt is retained.

`--ocr-output-dir DIRECTORY` also saves each full PDF's exact ingested normalization as `<source-stem>-<source-hash-prefix>.md` and a matching `.ocr.json` sidecar.
The hash prefix contains the first 12 hexadecimal characters of the archived PDF's SHA-256 digest.
Exports reuse retained artifacts, including ingestion through an active corpus owner; they do not repeat recognition.
Use `--overwrite-ocr` to replace exports without replacing corpus evidence or changing `--retry` semantics.
OCR export requires full preparation; non-PDF members of a mixed batch have no OCR export.
Normalization and indexing status are reported separately from export paths and errors.
If indexing fails after preparation, available OCR is still exported; if export fails after successful indexing, ingestion remains retained and the command returns nonzero.
Changing the recognition workflow or model creates a separately identified full-PDF preparation; previously retained evidence is not silently rewritten.

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

Use `nima chat` in a terminal or ask your coding-agent harness to chat with the knowledge base.
Select the corpus and, when needed, the project containing the evidence you want to search.

### Terminal chat

After [setup](SETUP.md) and ingestion, ask a single question with `--question`:

```sh
nima chat --corpus papers --project my-research --knowledge sources --question 'What approximation controls the calculation?'
```

Replace `papers` and `my-research` with your existing corpus and project names.
Omit `--question` to start an interactive session:

```sh
nima chat --corpus papers --project my-research
```

Type questions at the `nima>` prompt and use `/quit` or `/exit` to leave.
Conversation history is bounded and lasts only for the current terminal session.

| Option | Meaning |
| --- | --- |
| `--corpus NAME` | Required corpus to search. |
| `--project NAME` | Include the selected project's private sources and knowledge. |
| `--knowledge sources`, `graph`, or `both` | Search source passages, recorded graph knowledge, or both; default: `both`. |
| `--scope corpus`, `project`, or `both` | Select visibility; default: `both` with a project, otherwise `corpus`. Project-only scope requires `--project`. |
| `--mode lexical`, `vector`, or `hybrid` | Select retrieval mode. By default, configured embeddings select hybrid retrieval for source questions; otherwise retrieval is lexical. Vector and hybrid retrieval require configured embeddings and a ready vector index for source search. |
| `--question TEXT` | Ask one question and exit; omit for interactive chat. |
| `--json` | Return structured output, including answer citations, retrieval details and available model usage. |
| `--retrieve-only` | Return retrieved evidence without an answer-model call. |
| `--context-tokens N` | Request an evidence token budget up to the installation's chat ceiling. |
| `--answer-tokens N` | Request an answer output limit up to the installation's chat ceiling. |

For example, ask about recorded project knowledge or shared corpus literature:

```sh
nima chat --corpus papers --project my-research --knowledge graph --question 'Which assumptions remain unsupported?' --json
nima chat --corpus papers --knowledge sources --scope corpus --question 'What is a regulator?'
```

Direct graph search works without a matching source passage and expands only a bounded neighborhood.
Each answer citation resolves to a source region or graph reference in the current evidence packet.
Use `--json` to inspect those references, or `--retrieve-only` to inspect the evidence without generating an answer.
Run `nima chat --help` for the command's options.

Terminal chat uses the configured installation model and makes one answer-model call per question.
Direct terminal model calls require the `chat` extra and any credential named in the model profile; from a source checkout, install the extra with `.venv/bin/python -m pip install '.[mcp,chat]'`.
When the configured credential is absent from the terminal environment, answering uses the maintained **Answer Knowledge** owner workflow and its operator model connection.
This requires a configured owner connection and a running Langflow service; no stored credential is read back into the shell.

New installations default to 12,000 estimated evidence tokens, 1,000 history tokens and a 1,024-token answer limit.
Retrieval first collects up to 48 candidate regions from 12 seeds, with an 80,000-character candidate limit.
Selection favors topic-bearing text, discounts repeated page headers and includes up to two adjacent continuations from the same source and scope.
It retains at most 16 whole passages; an oversized passage is omitted rather than clipped, and shorter useful passages can still fit.
These candidate limits, the evidence token budget and the 512,000-byte transport limit are independent.
Full citation references remain in retrieval output and answer citations; the model receives exact passage text with compact citation labels, locations and preparation-quality information.

Counting uses an initialized or locally cached, integrity-checked `tiktoken` vocabulary when available.
A model-specific vocabulary is identified as such; an unknown model can use the cached `cl100k_base` vocabulary as an explicitly labeled proxy estimate.
Without a local vocabulary, the fallback estimates one token per two UTF-8 bytes, which is conservative for typical English prose but is not a guaranteed bound for every model or language.
Retrieval never downloads a tokenizer or calls a model merely to count tokens.
The complete prompt estimate includes the system instructions, question, bounded history, evidence, paths and message framing.
The reserved answer and a 1,024-token safety allowance must also fit the smaller of the configured model context and the installation's chat context ceiling.
When the model context is unknown, the default 32,768-token chat ceiling is an operator limit, not a claim about that model's actual capacity; configure the model's `context_window` when known.
Actual provider usage is returned when available.

Use `--retrieve-only --json` to inspect `data.regions` and `data.traversal` before generating an answer.
Diagnostics report actual returned passages and source characters after packing, estimated evidence/prompt/history tokens, the estimator, reserved answer and safety allowances, serialized transport bytes and omissions by reason.
Answer output includes the same packing diagnostics under `retrieval.traversal`.
A single displayed citation means the answer used that citation; it does not enumerate every passage supplied to the model.

### Configure and update chat selection

Terminal chat and the saved **Answer Knowledge** workflow share the same retrieval and packing code.
The installation's `chat` object controls their default budgets and ceilings, including optional settings such as:

```json
{
  "context_tokens": 12000,
  "history_tokens": 1000,
  "answer_tokens": 1024,
  "model_context_tokens": 32768,
  "context_safety_tokens": 1024,
  "max_transport_bytes": 512000,
  "tokenizer_model": "",
  "retrieval": {
    "candidate_seeds": 12,
    "candidate_regions": 48,
    "candidate_characters": 80000,
    "max_passages": 16,
    "adjacent_regions": 2,
    "max_hops": 2,
    "max_nodes": 128,
    "max_edges": 512
  }
}
```

Existing explicit settings, including a legacy `context_tokens: 4000`, are preserved.
New optional fields receive their defaults when the configuration is loaded.
Change the installation file explicitly to raise a retained ceiling; both terminal and owner-backed requests enforce it.
The `tokenizer_model` field can select a known tokenizer model when the provider's model name is an alias; its vocabulary must already be available locally.

After installing the updated package, update the runtime serving Langflow as well; for a managed installation, use `nima setup --provision`.
Then republish each existing project's managed workflows with its usual workspace path, for example:

```sh
nima project init my-research --corpus papers --path /path/to/workspace --non-interactive
```

Republishing installs the updated **Answer Knowledge** component and its current `chat_defaults_json` settings.
Older published canvases retain their previous component code and settings until republished; `nima project refresh` alone does not replace them.
Operator-edited canvases remain protected by the publication ownership checks and require reconciliation before replacement.
Previously ingested sources do not need to be ingested again for this selection change.

### Chat through a coding-agent harness

Ask the harness to use **Chat with Knowledge Base** (`nima-corpus-chat`).
Specify whether you want corpus literature, project notes, project knowledge or a combination.
For example: “Using this project's notes and corpus papers, explain the assumptions behind the current calculation and cite the exact passages.”

The skill uses Inspect Corpus for inventory/readiness, Retrieve Research Context for passages and Read Evidence for exact regions or saved artifacts.
It uses graph analysis and dependency tracing when the question concerns recorded relationships.
Harness chat uses the same retrieval boundary as terminal chat, with the harness's own answer model.
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
