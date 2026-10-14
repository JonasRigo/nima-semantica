# NIMA-Semantica v0.2.1

This release adds configurable index construction and recovery, actionable source-access diagnostics, and optional project vocabulary guidance.

## Index reliability

The installation's `projection_limits` apply consistently to CLI ingestion, project initialization and refresh.
An explicit `--projection-max-tokens` overrides the installation's one-million-token default within the supported ten-million-token maximum.
`nima index check` validates source access, integrity, counts and embedding compatibility through a read-only snapshot alongside the owner.
`nima index rebuild` reuses prepared sources and valid embedding matrices, routes writes through the existing configured owner when needed, and retains earlier failed receipts.
Changed budgets receive separately bound projection attempts.

Source preparation, index readiness and scientific verification retain their distinct meanings.
Permission denial, missing artifacts, modified bytes, stale revisions and exceeded budgets have structured diagnostics.
Operator access repairs remain scoped and explicit; NIMA does not automatically change ACLs or ownership.
The scoped receipt lookup already included in v0.2.0 is retained.

## Optional normalized reasoning

[Normalized reasoning](NORMALIZED_REASONING.md) is off by default and can be enabled per project or selected investigation.
It supplies bounded, authenticated vocabulary context to existing model/harness calls, preserves complete definitions for exact readback, and permits new concepts and challenges to definitions as provisional extensions.
Missing vocabulary is non-blocking, and disabling guidance skips vocabulary reads and prompt additions.
Existing private sessions retain their original identities and vocabulary pins after settings change.
Semantic observations remain advisory; vocabulary agreement never establishes mathematical truth or project admission.

See [reliability and recovery](RELIABILITY.md) for indexing commands and [qualification scope](PLATFORM_SUPPORT_v0.2.1.md) for release checks.
Install the matching wheel and rebuild/reload managed services and project toolboxes to use the changed runtime.
This publication remains GitHub-only.
