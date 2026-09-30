# v0.1.2 platform qualification

Release-candidate evidence collected on 2026-09-30. Publication requires a successful full hosted matrix for the candidate head and verified distribution checksums. This document records qualification, not a published release, an Apple certification or an Ubuntu vendor certification.

## Supported configurations

Ubuntu 24.04 and native Apple-silicon macOS with Python 3.11–3.13 are the target platforms. Native Langflow uses the pinned Python 3.11 stack. macOS execution workers use Docker Desktop; worker containers must not mount the native corpus. Intel Macs, Rosetta and other Linux distributions are not covered by these results.

## Candidate evidence

The runtime implementation was frozen at `fb68fb1`; the subsequent Lean HTTP-framing fix changes only a test. Final documentation and manifest updates do not change that implementation. Distribution hashes belong in the accompanying `SHA256SUMS` and verification report, avoiding a self-referential hash inside the wheel.

| Fresh check | Result | Boundary |
| --- | --- | --- |
| Clean installed wheel, macOS 26.6.2 ARM64 / Python 3.11 | 1,644 passed, 54 skipped, one expected failure | Native pinned Langflow stack; isolated fixtures |
| macOS ARM64 / Python 3.12 | 1,416 passed, 86 skipped | Core/MCP environment, without Langflow |
| macOS ARM64 / Python 3.13 | 1,416 passed, 86 skipped | Core/MCP environment, without Langflow |
| Ubuntu ARM64 / Python 3.12 | 1,394 passed, 108 skipped | Network-disabled container; no Langflow or Docker socket |
| Opt-in Lean tool/canvas and symbolic sandbox checks | 52 passed | Installed wheel against isolated execution workers |
| Opt-in symbolic reasoning/controller checks | 98 passed | Synthetic fixtures and authenticated worker transport |
| Linux Lean project and seccomp checks | 49 passed | Non-root ARM64 container, pinned Lean 4.32.1, public code only |
| Isolated 0.1.1 → 0.1.2 wheel upgrade | Passed | Exact source text, graph revision, configuration bytes and provider/embedding identities preserved |

The [candidate CI matrix](https://github.com/JonasRigo/nima-semantica/actions/workflows/tests.yml?query=branch%3Arelease%2Fv0.1.2-rc1) has 13 jobs: nine Ubuntu 24.04/macOS 15/macOS 26 × Python 3.11/3.12/3.13 contract jobs, three Python 3.11 Langflow jobs, and source/wheel packaging. macOS contract jobs explicitly require ARM64. Inspect the run for the exact candidate head; an earlier successful run is not approval of later code. A Darwin malformed-request test race found by this matrix was corrected without weakening HTTP 400 or no-execution assertions.

The wheel is built from its source archive. Archive verification checks all 601 inventoried source files, 270 packaged resources and 172 implementation files, their hashes and privacy exclusions. A fresh environment was dependency-resolved under the tested constraints; installed-wheel tests are not an editable-checkout substitute.

The isolated native MCP stack exposed all 21 tools. Fresh checks passed fast preparation, Ollama vector embedding, retrieval with provisional labels, exact evidence reading, operation replay, report save/read-back and Analyze Graph. `partial` fast preparation retained its quality warning while reporting a ready index; it was not counted as source-fidelity certification. A synthetic OpenRouter extraction produced a persisted graph proposal without committing graph changes.

Responsiveness was measured using a private clone of a maintained preparation flow with an injected three-second synchronous delay. Its server answered 28 concurrent health requests, with an 85 ms maximum. Store access remains serialized; this does not promise parallel corpus writers. Local context rejection, SDK timeout diagnostics and cancellation cleanup also have regression tests.

The live 20-source campaign exercised preparation, 106 extraction batches, within-paper and cross-paper consolidation, exact approved commit, Analyze Graph, lexical retrieval and exact evidence reading. It produced one connected graph with 1,338 nodes and 2,567 edges. Of 3,046 prepared regions, 2,208 were extracted, 315 deferred and 523 classified as no relevant content. The graph remains scientifically unverified. Pauli consolidation also passed on an isolated copy; a subsequent successful user-run consolidation is user-reported evidence, not an independently repeated test here.

## Release gates

- Build the wheel from the reviewed source archive, verify packaged resources and privacy exclusions, and record hashes.
- Run the final candidate on native macOS and Ubuntu, including clean installed-wheel and upgrade checks outside the source tree.
- Pass the hosted Ubuntu/macOS/Python matrix for the exact candidate commit; inspect every skipped and expected-failure test.
- Exercise current ingestion, extraction, consolidation, graph analysis, evidence and report persistence using isolated test data. Record provider identities and distinguish fresh checks from inherited evidence.
- Preserve research graphs and installation configuration; do not publish or create a release tag during candidate qualification.

## Provider and verification boundaries

The fresh bounded live campaign used exactly ten OpenRouter calls: two each for `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash` and `openai/gpt-6-luna` to verify structured tool selection, tool-result conversation history and token accounting, plus four GPT calls for real extraction. Claude/Gemini qualification here is a protocol smoke test through OpenRouter, not every scientific workflow and not their direct native APIs. No private research evidence was submitted.

Local `embeddinggemma:latest` embeddings passed the MCP stack checks. Local `qwen3:8b` returned a short response under the configured limits; an oversized input was rejected before generation. This is a local-chat transport/robustness check, not agentic capability certification. No fallback to a cloud model occurs. Compatible remote embeddings retain prior live evidence and current contract coverage; they were not called again in this ten-LLM-call campaign.

Native Anthropic/Gemini APIs retain independent SDK adapters and construction/binding tests. The user clarified that live model qualification should use OpenRouter; direct-API live certification remains outside this campaign. Model-specific reasoning, context and tool-use capabilities still require operator selection.

Skipped optional integrations are not passes. Native macOS skips include 39 tests requiring a Linux-local Lean/Bubblewrap toolchain, one Linux seccomp test and opt-in worker tests; separate Linux/worker runs exercise those boundaries. Core-only environments additionally skip Langflow/provider SDK integrations. Hosted jobs do not silently count missing worker fixtures as qualified. The single expected failure is an imported reference-loop scheduling limitation, not a maintained toolbox flow: extraction runs, but its downstream output adapter is not built. That reference workflow is not qualified for end-to-end use.

A structural graph analysis is not mathematical verification or evidence that every scientific claim was extracted correctly. Research graphs and live installation configuration were not changed by this release campaign.
