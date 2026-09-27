# Files and chat with the knowledge base

## Ingest a file

```sh
nima ingest papers /path/to/paper.pdf
nima ingest papers /path/to/notes.md --project my-research
```

The first command publishes a shared corpus source. The second stores project-specific notes.
Supported inputs are individual local PDF, text, Markdown, LaTeX, JSON and HTML files; this CLI does not accept directories or URLs.
The current preparation limit is 20 MB per file.
PDFs require the configured advanced PDF normalizer, which preserves page provenance and formula diagnostics.
The command returns source identifiers, receipts and actual indexing status.
Repeated identical ingestion reuses its recorded result; failed or partial normalization remains visible.

Lexical indexing is the default. Use `--index-mode vector` after configuring an embedding profile.
Source preparation and ontology extraction are separate operations: ingestion makes material searchable without automatically accepting its claims into a project graph.

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
Inspect the proposal and its exact source references. Use Analyze Graph and Trace Claim Dependencies to understand its represented structure.
Apply a selected change through Update Project Graph with its exact approval and current revision.
Corpus material remains shared; project notes and project records retain their project scope.
