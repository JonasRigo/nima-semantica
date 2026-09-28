# NIMA-Semantica v0.1.1

This release adds Apple-silicon macOS support alongside Ubuntu/Linux, with Python 3.11–3.13 for the library and CLI.

## What's new

- Managed macOS deployment: native Langflow in a dedicated Python 3.11 environment, with Docker Desktop execution workers for PDF, symbolic mathematics and Lean. No Langflow Desktop account is required.
- One setup wizard with independently configurable chat and embedding providers. Chat supports OpenRouter, OpenAI-compatible endpoints, native Anthropic, Gemini and Ollama. Embeddings support Ollama and OpenAI-compatible endpoints, including OpenRouter.
- Native macOS corpus locking and atomic publication, platform-aware diagnostics, resumable provisioning and isolated PDF extraction.
- Pinned Langflow/OpenAI component compatibility, reliable native-service restart, and repeat publication across multiple projects.
- Deep-research fixes: one audited correction opportunity for invalid provisional graph relations, and compatible headers for the pinned arXiv HTML reader. Invalid proposals remain rejected; scientific admission rules are unchanged.

## Verification and support boundaries

The [qualification report](PLATFORM_SUPPORT.md) records hosted Ubuntu/macOS tests, native Apple-silicon integration checks, installed-wheel verification and documented skips. The local installed-wheel suite passed 1,564 tests, with 47 optional/platform-specific skips and one pre-existing expected reference-loop failure. Separate worker tests cover explicitly gated service and isolation checks.

Live agentic validation used OpenRouter `openai/gpt-6-luna`. Ingestion, vector indexing, hybrid retrieval, comparison, extraction and replay passed with both local Ollama embeddings and OpenRouter embeddings. Additional live checks covered counterexample search, hypothesis generation, review, substantiation, Lean drafting, deep research, MCP and PDF processing.

Provider configuration support is not certification of every model. Native Anthropic/Gemini have adapter-level tests, not live API qualification. The small local Ollama chat model is not used to qualify agentic capabilities. Live qualification for those APIs and improved context budgeting/responsiveness for slow local chat models are deferred to v0.1.2 work.

Intel Macs and Rosetta are not supported by this macOS qualification. Ubuntu is the tested Linux distribution; other distributions must satisfy the documented prerequisites. Docker Desktop workers must not mount the native corpus: host filesystem locks were not honored across that boundary in testing.

## Install or upgrade

Back up existing corpus data and installation configuration before upgrading. Download the wheel from the v0.1.1 release and follow [SETUP.md](SETUP.md), including CPU-only PyTorch instructions on Linux. Run `nima doctor` afterward. Existing installations are not silently migrated to another provider or embedding identity; select and verify those changes explicitly.

The release assets include the wheel, source archive and SHA256SUMS. No new versioned DOI is asserted until one has actually been assigned; the v0.1.0 DOI continues to identify that earlier release only.
