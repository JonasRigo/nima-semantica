# Ingest papers

Use `nima-ingest` for a supplied paper list, an exact ontology, and full or fast preparation.
For example: “Ingest these arXiv HTML papers in fast mode with literature_evidence; propose
the relevant graph additions but wait for my approval before committing.”

For a multi-paper map: “Ingest these papers using `literature_review`, reconcile the
regional proposals within and across papers into one coherent candidate, preserve
source provenance and unresolved differences, then prepare the exact graph update
for approval and run Analyze Graph.” The skill uses Deep Extraction's explicit
[consolidation modes](../tools/deep_extraction.md#consolidate-batches-and-papers);
similar labels alone are not merged, and unrelated objects are not linked just to
make the graph connected. Report coverage gaps separately from connectivity.

Fast sources are searchable and explicitly provisional. Exact citations prove correspondence
to retained extracted text, not fidelity to the original mathematics or scientific truth.
Approved corpus sharing preserves this distinction. Full preparation creates separately
identified regions and upgrade links; existing graph evidence must be reconciled explicitly.

See [knowledge-base ingestion](../KNOWLEDGE_BASE.md) for CLI options.
