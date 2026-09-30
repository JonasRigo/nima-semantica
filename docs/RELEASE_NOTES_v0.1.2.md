# NIMA-Semantica v0.1.2

This release improves source ingestion, agent-facing tool contracts and consolidation of regional and paper-level proposals into a coherent project graph. Ubuntu Linux and Apple-silicon macOS remain the supported configurations; scientific results remain proposals until separately verified within a stated scope.

## Source ingestion

- Persistent fast ingestion and HTML/arXiv HTML input complement full PDF preparation and supplied Markdown/LaTeX sources.
- Preparation quality, unresolved diagnostics and exact source provenance survive indexing, extraction, consolidation and graph promotion. Fast preparation is not fidelity certification; Deep Research's temporary fast reads remain temporary.
- The `nima-ingest` skill guides ontology selection, preparation, extraction planning and explicitly approved graph updates.
- Embedding failures now carry actionable diagnostics, and misleading chunker terminal progress is suppressed.

## Tools and graph consolidation

- Public tools explain valid request shapes, per-call limits, prerequisite state and recovery steps. Invalid requests return structured feedback rather than opaque parsing failures.
- Extraction planning supplies executable batches and continuation information. An explicit document identifier can assert membership of selected regions without silently expanding the request.
- Consolidation planning and execution reconcile regional and cross-paper proposals using explicit identity decisions and source-backed links. Immutable evidence, differing properties, provisional labels and unresolved limitations are retained.
- Analyze Graph reports connected components. A connected graph is not proof of semantic correctness or complete literature coverage.
- `nima list` and `nima project list` expose registered projects. Workflow publication recovers from transient Langflow read-after-create failures without duplicating flows.

## Verification and limitations

See [v0.1.2 qualification](PLATFORM_SUPPORT_v0.1.2.md) for exact candidate evidence and remaining release gates. Recorded live campaigns include the Pauli consolidation and a 20-source literature graph; the latter contains explicitly deferred regions and unverified scientific proposals.

The setup wizard preserves independent LLM and embedding choices, native Anthropic/Gemini/Ollama adapters, and OpenAI-compatible endpoints including local servers. Live agentic qualification uses OpenRouter; local Ollama is qualified for embeddings, not for the capabilities of arbitrary local chat models. Native Anthropic/Gemini live qualification and improved responsiveness/context budgeting for slow local chat remain limitations unless separately qualified; configuration and adapter tests are not live API certification.

## Upgrade

Back up corpus data and installation configuration, install the release wheel, and follow [SETUP.md](SETUP.md). Run `nima doctor` and refresh managed project toolboxes through the documented setup workflow. Do not overwrite manually edited Langflow flows without reviewing the reported differences. Provider choices and embedding identities must not change implicitly during an upgrade.

macOS uses native Langflow with Docker Desktop execution workers. Intel Macs, Rosetta and untested Linux distributions are outside the qualification. No new versioned DOI or publication date is asserted before the release is actually published.
