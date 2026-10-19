# v0.3.1 candidate qualification

Status: local Linux operational checks passed for the v0.3.1 candidate.
The hosted Ubuntu/macOS matrix has not run for this candidate, and no production PDF model has passed the transcription fidelity gate.
The v0.3.0 hosted results retain their historical scope and do not qualify the v0.3.1 patch.

The target platforms remain Ubuntu/Linux and native macOS Apple silicon with Python 3.11–3.13.
The configured Langflow application, base package and LFX runtime remain pinned at 1.12.0 in managed installation and CI.
Local development regression results use Python 3.13.2 and LFX 1.12.2 with reused dependencies; they do not substitute for the pinned hosted jobs or a clean-host installation.

| Evidence | Scope and status |
| --- | --- |
| Source regression suite | 1,858 passed, 19 optional skips, one retained expected failure |
| Installed-wheel regression suite | 1,819 passed, 58 optional skips, one retained expected failure; imports resolve to the installed wheel |
| Source archive and wheel | Wheel built from the source archive; all 699 candidate files, 218 runtime files and 304 packaged resources pass inventory and byte checks |
| Configuration and stored-state upgrade | Isolated upgrade from the published v0.3.0 wheel preserves configuration bytes, shared/private source artifacts, corpus records and private calculation/proof exports across three databases |
| Live PDF workflow | Isolated private Langflow canvas, actual image calls, 30 retained crops, short-PDF OCR and ingestion passed operational checks during patch qualification |
| Exact ingestion export and replay | Standalone OCR and ingestion exported identical document bytes; replay reused saved recognition |
| PDF transcription fidelity | Open: both tested vision profiles returned mathematical errors; no production model is qualified |
| Native macOS provisioning | Simulated setup contracts covered locally; fresh native provisioning and this revision's hosted jobs remain pending |
| Hosted platform matrix | Pending a later authorized push; no v0.3.1 hosted result is asserted |

Optional Lean, provider and isolated-worker checks are counted according to their actual availability and explicit opt-in settings.
Local skips cover missing native Zsh, optional provider extensions and explicitly configured worker/image integrations.
The installed-wheel run additionally skips 39 direct Lean checks because the development toolchain is absent from the curated source tree.
The final qualification-document update changes no runtime or workflow bytes; the rebuilt artifacts receive focused package and contract checks.
Skipped checks are not passes, and the retained adapter expected failure is separate from maintained workflow acceptance.
Full PDF preparation requires the dedicated image-capable model and matching v0.3.1 services described in the [release notes](RELEASE_NOTES_v0.3.1.md#upgrade-from-v030).

The preparation artifacts and detailed receipts remain local until publication is authorized.
No release tag, GitHub release, DOI deposit or live installation upgrade is part of candidate preparation.
