# NIMA-Semantica v0.2.0

This release improves MCP connection recovery, private database ownership, receipt storage, Lean execution, scoped retrieval and tool discovery.
Existing scientific status boundaries remain intact: operation completion, formal verification, source correspondence and project admission retain their separate meanings.

## Reliability changes

- `nima doctor` separates authentication discovery, transport initialization, permissions, owner identity, project scope and pinned retrieval readiness.
- Guarded harness reconciliation validates configuration hashes and retains original bytes, permissions and ownership for interruption recovery.
- Authenticated loopback owners and packaged stdio proxies allow multiple private clients to share one database owner without repeating another client's recovery.
- Versioned receipt references authenticate exact payloads, while bounded inspection and export avoid duplicating large attachments.
- Reversible receipt migration preserves session revisions, dependencies, lifecycle and replay history; SQLite space reclamation remains a separate maintenance operation.
- An operator-owned Lean policy provides separate compilation and replay allowances, durable operation reconciliation and explicit cancellation and delivery diagnostics.
- Scoped retrieval uses coherent corpus snapshots and the configured corpus owner, validates pinned projections and advertises context limits.
- Both private services provide complete record enumeration and exact canonical reads; default tool inventories are bounded and selected contracts remain complete.

## Upgrade and qualification

See [reliability and recovery](RELIABILITY.md) for configuration, migration and recovery commands, and [v0.2.0 qualification](PLATFORM_SUPPORT_v0.2.0.md) for verification scope.
Install the matching wheel and refresh managed workers and project toolboxes through the documented setup workflow.
Existing services must restart with the selected runtime; installing a wheel alone does not replace a running worker.
Retain consistent database backups and original configuration before changing ownership or migrating receipts.
Reload harness MCP connections after applying a guarded binding transition.

The local complete source and installed-wheel suites each passed 1,673 tests with 76 optional skips and one retained expected failure.
A live native Lean operation completed in 309.758 seconds with independent inspection and kernel replay.
That check did not establish source correspondence or promote the result to project admission.
All 12 retained proof sessions and the accepted receipt pairs matched their pre-upgrade scientific state.

This version is published on GitHub only.
