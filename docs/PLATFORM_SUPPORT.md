# v0.1.1 platform qualification

Status: qualified for v0.1.1 under the tested configurations below. All 12 hosted CI jobs passed; the operator authorized release on 2026-09-28.

The release targets Ubuntu/Linux and macOS Apple silicon, with native Python 3.11–3.13. macOS runs Langflow natively in a dedicated Python 3.11 environment and uses Docker Desktop for PDF, symbolic and Lean workers. Other Linux distributions must supply the documented prerequisites; they are not verified merely because Ubuntu passes. Intel Macs and Rosetta are outside the macOS support claim.

## Required evidence

| Check | Required configuration | Status |
| --- | --- | --- |
| Native library and CLI | Ubuntu 24.04, Python 3.11–3.13 | Hosted x86-64 matrix passed; ARM64/Python 3.12 container: 1,344 passed, 73 skipped |
| Native library and CLI | macOS ARM64, Python 3.11–3.13 | Latest core 3.12/3.13: 1,341 passed, 74 skipped each; full 3.11 integration suite below |
| Shipped Langflow workflows | Linux and native macOS, pinned Langflow 1.12.0 | Native suite: 1,564 passed, 47 skipped, 1 expected failure; 21 published API checks and MCP discovery/call passed |
| Full managed deployment | macOS ARM64 and Docker Desktop | Provisioning and all doctor checks passed; native restart and repeat publication passed |
| PDF model assets and normalization | Pinned Python 3.11 worker | Asset integrity, real normalization and cache replay passed |
| Lean verification and adversarial isolation | Lean 4.32.1, Linux ARM64 worker | 45 adversarial tests passed; authenticated service and published workflow passed |
| Wheel and source archive | Clean installation outside source tree | Wheel built from sdist; packaged resources matched; installed-wheel suite: 1,564 passed, 47 skipped, 1 expected failure; dependency check passed |

Hosted CI passed for Ubuntu 24.04 and native macOS 15/26 across Python 3.11–3.13, plus native Langflow checks on each OS. [Qualification run 36455316152](https://github.com/JonasRigo/nima-semantica/actions/runs/36455316152) tested implementation commit `4839b8c3c7eaf8ad6bf35b675eb46b09611b13b0`. The final release adds qualification documentation and metadata without changing that tested implementation. The labels follow the [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). Hosted native macOS checks do not establish Docker Desktop integration coverage; the separate Apple-silicon full-stack campaign supplies that evidence.

| Hosted job group | Passed per job | Skipped per job | Expected failures |
| --- | ---: | ---: | ---: |
| Ubuntu core, Python 3.11/3.12/3.13 | 1,337 | 80 | 0 |
| macOS 15/26 core, Python 3.11/3.12/3.13 | 1,313 | 104 | 0 |
| Ubuntu Langflow, Python 3.11 | 1,558 | 53 | 1 |
| macOS 15/26 Langflow, Python 3.11 | 1,534 | 77 | 1 |

CI reports were reviewed for every skip. Core jobs omit optional Langflow/native-provider packages; hosted macOS also omits Docker-dependent symbolic checks. Direct Lean/bwrap, explicitly gated remote workers and the legacy Lean-image integration are absent from the default CI environment. Local live-service, Docker Desktop and adversarial-worker results supplement those skips; no skipped check is counted as a pass. JUnit reports are attached to the qualification run.

## Compatibility changes

- Platform-specific provisioning and diagnostics retain the existing Linux service path.
- Separate host/container endpoints preserve existing configuration defaults.
- Managed macOS workers use ARM64 images, private tokens, loopback ports and isolation preflights.
- Native Langflow, CLI and MCP share macOS filesystem locking. A live probe found that Docker Desktop did not honor a host-held corpus lock; sharing the corpus with a Docker Langflow instance is therefore unsupported on macOS. Worker containers never mount the corpus.
- Native macOS provisional PDF extraction uses Docker resource limits instead of unsupported Darwin `RLIMIT_AS`.
- Execution receipts record the actual NIMA package version.

## Release gate

Do not tag or publish v0.1.1 until all required configurations pass. Record the final revision, OS/interpreter versions, dependency identities, image IDs, test results and every skip. Mocked tests and model connectivity probes do not replace live workflow evidence. Any unresolved installation, persistence, workflow or isolation failure blocks full approval.

No new DOI is asserted before publication. The v0.1.0 DOI identifies the previous release only.

## Campaign checkpoint — 2026-09-28

Paused at the operator's request to move to a more reliable network. Native Python 3.11.7, 3.12.3 and 3.13.14 each passed 1,321 tests with live Docker symbolic execution enabled (70 skips and one upstream Starlette deprecation warning per run). These results include cross-process native corpus locking and the macOS deployment contracts; they do not qualify the skipped Langflow/Lean integrations. Installed dependency checks passed for all three core environments.

After resuming on a stable connection, native Langflow and all worker images installed successfully. All doctor checks passed. Live full PDF normalization and replay passed. The Lean service accepted a valid proof and rejected sorry, a custom axiom, an invalid proof, host-file reads and subprocess execution. The extended local adversarial suite passed all 45 tests under the production worker isolation settings.

All 21 maintained workflows were published privately and exercised through the native API using valid preview/read-only requests. MCP listed all 21 tools and executed Tool Guide. Live Verify Lean completed with kernel acceptance. An OpenRouter `openai/gpt-6-luna` comparison produced the expected advisory partial result, and Unicode ingestion with local `qwen3-embedding:0.6b` completed a 1,024-dimensional vector index and replay. Native service restart and repeated project publication preserved the project.

Additional blockers fixed during this phase: missing OpenCV system libraries in the PDF image, incompatible `lfx-openai==0.1.5` against LFX 1.12.0 (pin 0.1.4, validated for imports and dependency metadata), asynchronous launchd shutdown/re-registration, and stale workflow source hashes. The expected test failure is the pre-existing reference-loop scheduling issue, not a maintained toolbox flow. Remaining native skips include direct Linux/toolchain-specific tests; these do not replace the separate worker qualification.

At this earlier checkpoint, preliminary wheel/resource checks had passed but final artifact rebuilding and manifest regeneration remained. Those steps were subsequently completed as recorded below.

### Provider-flexibility follow-up

The unified wizard now configures chat and embedding endpoints independently, including native Ollama, Anthropic and Gemini chat and arbitrary OpenAI-compatible servers. Live all-local setup, source ingestion, 1,024-dimensional Ollama embedding, hybrid retrieval, native Ollama structured tool calling and usage accounting passed. The native provider regression run passed 1,561 tests with 47 skips and one expected failure. Native Anthropic/Gemini construction and tool binding are tested; their live APIs have not been qualified with credentials.

The extended local `qwen3:8b` comparison did not finish within the 600-second client deadline. Ollama logs showed input context truncation at its 4,096-token default; the verification service was restarted to stop that run. A larger context is configurable, but the full comparison still needs rerunning before claiming that model/workflow combination is qualified. Slow synchronous model execution also delayed other API requests during this run.

Creating a second project exposed a repeat-publication name conflict: Langflow assigns globally unique flow names, while updates formerly restored the original name. Updates now preserve the server-assigned name; live republication passed.

### Approved validation configurations

Per the operator's decision, `qwen3:8b` is not used to qualify agentic/LLM capabilities. Its long comparison failure is not a release gate for the approved OpenRouter configuration. Ollama chat remains configurable without a model-quality certification. Context budgeting and responsiveness under slow local inference remain documented robustness limitations.

Live published-workflow runs passed with `openai/gpt-6-luna` via OpenRouter and each of:

- Ollama `qwen3-embedding:0.6b`, pinned digest, 1,024 dimensions.
- OpenRouter `openai/text-embedding-3-small`, 1,536 dimensions, through the compatible endpoint. The declared revision is an operator alias dated 2026-09-28, not a provider-guaranteed immutable weight revision.

Both configurations passed Unicode source ingestion, vector indexing, hybrid retrieval, comparison, extraction, and exact replay of those operations. Expected `partial` results retain unverified proposals; they are not scientific or graph-admission approvals. OpenRouter also passed live counterexample search with symbolic execution, hypothesis generation, review, substantiation and Lean drafting with formal verification accepted. Fast PDF extraction, full normalization/replay, all doctor checks and MCP discovery/execution passed again.

The installed-wheel run used Python 3.11 outside the checkout; all 515 installed dependencies were compatible. The default full native run's 47 skips comprise 39 direct local Lean/bwrap checks, one Linux-seccomp check, one explicitly gated symbolic-service check, one legacy isolated-Lean-image check, and five explicitly gated pinned-Lean-service checks. Separate worker evidence must be considered alongside this suite; skipped checks are not represented as passes. The expected failure remains the historical reference-loop scheduling test.

The explicitly enabled Lean-service/canvas and symbolic-service test selection passed all 54 tests, covering six checks skipped by the default environment. After the deep-research correction below, its focused 22-test suite also passed on Python 3.12 and 3.13.

Live deep research exposed a model-generated relation outside the review ontology. Validation now occurs within the existing bounded correction stage, with one audited retry and explicit ontology feedback. A second invalid graph is still rejected, and no graph is published by the correction step. Two new tests cover corrected and persistently invalid responses. This is a validation/recovery fix, not permission to accept arbitrary model graph relations.

The corrected live deep-research run completed with three regional passes, ten coverage facets, a provisional review graph and explicit uncovered questions. Coverage remained partial and graph admission remained `not_eligible_until_prepared`, as required for fast provisional reading. Its logs additionally exposed an arXiv HTML adapter header mismatch against pinned LFX: direct URL-component calls require a list of key/value rows, not a DataFrame. The adapter and its regression assertion now use that format.

The corrected pinned URL reader fetched real arXiv HTML successfully. After both fixes, the installed-wheel suite passed 1,564 tests (47 documented skips, one expected failure). Subsequent release edits are limited to documentation, citation date, resource inventory and release manifest. Rebuilt artifacts retain identical tested Python code and workflow resources.

The experimental branch was committed and pushed as `4839b8c`, and all hosted matrix jobs passed. The operator subsequently authorized merging, tagging and publishing v0.1.1. See the [release notes](RELEASE_NOTES_v0.1.1.md) for installation, support boundaries and follow-up work.
