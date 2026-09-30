# v0.1.2 platform qualification

Status: release-candidate qualification in progress. Do not publish until the final artifact and hosted CI gates below are satisfied. No v0.1.2 release has been published by this campaign.

## Supported configurations

Ubuntu 24.04 and native Apple-silicon macOS with Python 3.11–3.13 are the target platforms. Native Langflow uses the pinned Python 3.11 stack. macOS execution workers use Docker Desktop; worker containers must not mount the native corpus. Intel Macs, Rosetta and other Linux distributions are not covered by these results.

## Candidate evidence

Fresh candidate results, exact artifact hashes and reviewed skip reasons will be recorded here after verification. The prior implementation campaign recorded 1,623 passed / 54 skipped / one expected failure on macOS and 1,374 passed / 107 skipped in Ubuntu ARM64. These historical totals are not fresh 0.1.2 artifact results.

The live 20-source campaign exercised preparation, 106 extraction batches, within-paper and cross-paper consolidation, exact approved commit, Analyze Graph, lexical retrieval and exact evidence reading. It produced one connected graph with 1,338 nodes and 2,567 edges. Of 3,046 prepared regions, 2,208 were extracted, 315 deferred and 523 classified as no relevant content. The graph remains scientifically unverified. Pauli consolidation also passed on an isolated copy; a subsequent successful user-run consolidation is user-reported evidence, not an independently repeated test here.

## Release gates

- Build the wheel from the reviewed source archive, verify packaged resources and privacy exclusions, and record hashes.
- Run the final candidate on native macOS and Ubuntu, including clean installed-wheel and upgrade checks outside the source tree.
- Pass the hosted Ubuntu/macOS/Python matrix for the exact candidate commit; inspect every skipped and expected-failure test.
- Exercise current ingestion, extraction, consolidation, graph analysis, evidence and report persistence using isolated test data. Record provider identities and distinguish fresh checks from inherited evidence.
- Preserve research graphs and installation configuration; do not publish or create a release tag during candidate qualification.

## Provider and verification boundaries

OpenRouter LLMs with local Ollama or compatible remote embeddings are the qualified live configurations from the previous platform campaign. Provider flexibility remains supported. Native Anthropic/Gemini have adapter-level tests, not live qualification; slow/weak local chat models are not certified for agentic work. These limitations must remain explicit rather than being counted as passing tests.

Skipped optional integrations are not passes. The historical expected failure concerns a reference-loop scheduler, not a maintained toolbox flow. A structural graph analysis is not mathematical verification or evidence that every scientific claim was extracted correctly.
