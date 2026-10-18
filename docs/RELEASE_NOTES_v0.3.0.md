# v0.3.0 — standard knowledge and scientific reporting

Version 0.3.0 introduces standard knowledge access and scientific reporting.
The GitHub release provides the matching source archive and wheel; no new DOI deposit or automatic live-service upgrade is included.

Version 0.3.0 adds configurable human-readable workspaces, bounded source/graph chat, complete upstream OKF Studio visualization, deterministic detailed private calculation documents, and shared SciPost-style scientific reports.
OKF Studio has installable Linux x86_64 packages; macOS users must build it from source and configure the resulting executable as described in the [installation guide](KNOWLEDGE_AND_REPORTING.md#the-complete-project-in-upstream-okf-studio).
Standard setup includes reporting tools and Blender, with an explicit minimal profile.
Figures use final-size LaTeX typography and versioned vector/hybrid masters with companion explanations.

Large PDF normalization is resumable by physical-page batches, preserves formula crops and page provenance, and propagates unresolved mathematical transcription into retrieval.
Local file ingestion routes through an active owner without competing for the corpus writer lease.
Scope selectors preserve historical defaults and receipts.
Native source diagnostics no longer duplicate an entire book's recognition metadata into every chunk.

Version 0.3.0 also includes controlled normalized integration, directional limits, Taylor/Laurent expansion and truncation, retaining conditions and remainders in atomic receipts.
These execution capabilities establish symbolic results under their recorded assumptions; they do not supply missing microscopic physical derivations.

Managed skill updates preserve human edits, and Linux service generation aligns new artifact ownership with the data-root owner.
Existing operator settings, immutable source bytes, sealed vocabularies, graph revisions and historical private sessions remain authoritative.

See [Knowledge and reporting](KNOWLEDGE_AND_REPORTING.md) for commands, configuration and limitations.
Qualification evidence and local operational changes are retained privately and are not part of public source artifacts.

Setup now exposes `nima` on PATH on Linux and macOS through an environment-bound launcher and managed Bash/Zsh startup entries.
`--no-modify-path` opts out, and reruns preserve existing configuration backups.
Langflow connection failures identify the unavailable endpoint and suggest an installation-specific start command; manual startup remains supported.

`nima list` lists corpora, and `nima list --corpus NAME` lists that corpus's projects.
The former `nima project list` command is removed.
`nima project delete PROJECT --corpus CORPUS` previews deletion of one project; `nima corpus delete CORPUS` previews deletion of the corpus and every linked project.
Deletion requires exact preview-hash approval and stopped database owners, backs up managed state, preserves workspace content and unrelated scopes, and supports completion after interruption.
Immutable artifact bytes and recovery backups are retained conservatively; deleting active state does not securely erase historical bytes.
Published workflows are removed only after ownership and content checks, and shared credentials are preserved.
The macOS Studio guide uses a pinned source build with local application bundling and updater signing disabled, and the CLI validates the native executable before launching it.

The standard operator-facing interface for exact graph approval remains follow-up work; current approval is bound through the operator context.
A fresh ontology-to-report demonstration in a coding-agent harness on the presentation Mac remains unqualified.
Earlier backend checks and prepared scientific examples retain their separate scope.
