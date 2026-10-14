# NIMA reliability and recovery

Private mathematics and proof services retain exclusive ownership of their SQLite databases.
For several harness clients, run one authenticated loopback HTTP owner per database and connect every client through the packaged stdio proxy.
Standalone stdio remains available for a single owner.
An occupied owner is reported explicitly; reconnecting never performs another owner's interrupted-action recovery.

## Diagnostics and project binding

Run `nima doctor --project PROJECT --corpus CORPUS --mcp --path WORKSPACE` with the installation's `--config` option.
The report separates package and MCP SDK versions, configured endpoints, file access, database ownership, transport initialization, actual tool discovery, project scope and pinned retrieval readiness.
Authentication discovery is independent of transport readiness.
A working public endpoint can have unsupported OAuth discovery.
Startup diagnostics are bounded and redact credentials.

Prepare managed user services from an existing coherent project binding:

```bash
nima --config INSTALLATION private configure --binding PROJECT_BINDING --directory PRIVATE_SERVICE_DIRECTORY --proof-port 7881 --math-port 7882 --start
```

This creates private tokens, systemd unit files and a new `binding.json` with the service endpoints.
The service account must already be able to read the private configuration and own its database.
Keep existing configurations private; select the appropriate service account before changing paths.
For a container installation, run the same `nima_semantica.private_service` entry point under the data owner's account, with a persistent database and private token file.
Bind only to `127.0.0.1` or `::1`.
The packaged `deploy/private.compose.yml` provides a persistent container lifecycle for this route.
Select its image, existing data directory, private configuration directory, project MCP endpoint and worker connections through operator environment variables.
The selected container account needs SQLite shared-memory access in the corpus directory as well as access to private databases; a read-only volume can prevent SQLite WAL snapshots from opening.
Snapshot SQL remains read-only and never acquires the corpus writer lease.

The generated units support `systemctl --user status`, `restart` and `stop` using their recorded names.
Only the owner performs interrupted-action recovery at startup.
Concurrent mutations of one session are rejected through its existing active-action and revision checks.
Independent sessions can proceed concurrently.

Preview the complete harness transition before applying it:

```bash
nima --config INSTALLATION harness reconcile --binding PRIVATE_SERVICE_DIRECTORY/binding.json --path WORKSPACE --preview-file PREVIEW.json
nima --config INSTALLATION harness reconcile --binding PRIVATE_SERVICE_DIRECTORY/binding.json --path WORKSPACE --preview-file PREVIEW.json --apply PREVIEW_HASH --recovery-directory NEW_RECOVERY_DIRECTORY
```

The preview pins every configuration input and destination by SHA-256.
Application retains exact original bytes and permissions and writes an all-files recovery manifest before replacing a destination.
Unrelated harness servers and server policies are preserved.
The target private configurations must agree on corpus, project and the explicitly selected projection.
Use `--project-manifest REGISTERED_PROJECT_JSON` to include the installation's registered binding in the same guarded transaction.
Legacy records and configuration files are retained.

After interruption, use `nima harness recover RECOVERY_MANIFEST --mode restore` or `--mode complete`.
Recovery validates every destination and backup before changing any file and refuses unrelated changes.
Reload the harness MCP connections after a completed transition.

## Receipt storage and exact inspection

New proof attachments contain a versioned receipt reference with the logical receipt ID, exact scope, authoritative record ID, canonical SHA-256 digest, payload byte count and a small status summary.
The configured corpus store selects the payload location.
A reference cannot select a filesystem path.
Resolution authenticates the payload and rejects unavailable, foreign or modified receipts.
Historical embedded receipts and campaign references remain readable during migration.

Large authoritative execution receipts are retained once as immutable corpus artifacts.
Ordinary receipt records contain the authenticated artifact reference and summary.
`ExecutionReceiptService.get` resolves the exact original receipt, including metadata.
Draft Lean child receipts refer to immutable input and output artifacts; native Lean attempt records refer to immutable request and result artifacts.
Private mathematical state deduplicates large source and execution material in a scope-bound immutable payload table and validates it when restoring the graph.

Private `export` returns bounded pages by default.
Follow `next_cursor` while `has_more` is true and retain the same session revision.
`partial_export` also identifies omitted large objects.
Exact objects are retained or omitted as a whole, with explicit retrieval references; proof statements and source passages are never silently shortened.
Use `inspect` with `full_payload: true` for the exact selected material.
When an export reference selects a section without an individual inspect operation, request the indicated full `export`.
Pages return at most 25 entries even when a larger limit is requested, with an aggregate default export limit of 65,536 bytes.
For an attached specialist receipt, set `resolve_receipt: true` as well.
An explicitly requested `export` with `full_payload: true` retrieves the full historical export.

Migrate an idle proof session while its owner is stopped:

```bash
nima --config INSTALLATION maintenance receipt-migration --proof-config PROOF_CONFIG --session SESSION --expected-revision REVISION --directory NEW_MIGRATION_DIRECTORY
```

The migration acquires ownership, makes and verifies a consistent SQLite backup, records a format version and receipt checkpoints, authenticates every replacement, and compares scientific/session semantics before committing.
Resume using the same directory and expected revision after interruption.
For an existing mathematics session, use `--math-config MATH_CONFIG` with the same session, revision and directory options to deduplicate its material with exact restored-graph comparison.
Node identities, dependencies, lifecycle, submission status, action identities and stored replay results remain intact.

SQLite space reclamation is separate from migration.
Stop the owner, retain the migration and authoritative receipt backups, and run `nima maintenance reclaim-sqlite --database DATABASE --backup NEW_BACKUP`.
Reclamation requires adequate temporary disk space and a verified consistent backup.
It reclaims unused pages without deleting scientific history.

## Lean execution and durable delivery

The installation's `lean_policy` is the operator-owned execution policy.
Its default compilation and independent inspection/kernel replay allowances are 1,200 seconds each, with 32,768 MiB memory.
The whole-job deadline is 2,460 seconds; queue wait is 300 seconds; synchronous transport permits 2,790 seconds including the 30-second delivery allowance.
Output, artifact and heartbeat budgets are part of the same policy and environment identity.
Private symbolic exploration retains its separate maximum of 120 seconds.

New managed Lean configurations use `policy` rather than separate legacy limit overrides.
`deploy/configure_lean.py --policy POLICY_JSON` pins the toolchain under the same supported policy.
Existing configurations retain their operator decisions; phase allowances are explicit even for legacy configurations.
Compilation cannot consume the replay allowance.

The maintained worker provides authenticated `/operations/submit`, `/operations/status?id=ID`, `/operations/result?id=ID` and `/operations/cancel` calls.
Submission binds an operation ID to the exact sources, ordered modules, targets, imports and environment.
Replaying that identity returns its existing operation; changed input requires a new identity.
Queue, compilation, replay, whole-job, transport, memory, heartbeat and output failures have separate diagnostics.
Status includes the phase, elapsed time and selected policy.

A lost client response produces `uncertain_delivery` rather than a definitive verification result.
Replay the identical request to reconcile its durable worker result.
A deliberate new attempt requires a new operation ID.
Cancellation retains a late result as audit material and prevents its admission as the cancellation's result.
Owner interruption is recovered once and remains explicit.
Pinned source/environment checks, independent inspection, allowed-axiom checks, declaration type fingerprints and kernel replay remain required.
Formal verification does not establish source correspondence or project admission.

## Retrieval and discovery

The installation's `projection_limits` select the complete index construction budget: one million lexical tokens and 100,000 source regions by default.
The maximum supported explicit request is ten million tokens and one million regions.
CLI ingestion, project initialization and project refresh accept `--projection-max-tokens` and apply that override consistently.
Limits never increase automatically.

```bash
nima index check CORPUS --project PROJECT --projection-max-tokens 5000000
nima index rebuild CORPUS --project PROJECT --projection-max-tokens 5000000
```

Omit `--project` to check or rebuild the public corpus projection.
Preflight checks source readability and integrity, token/region counts and existing embedding matrices through a coherent read-only snapshot alongside the writer.
Rebuild reuses prepared sources and valid embeddings without acquisition, parsing or provider calls, and routes through the configured project MCP owner when its corpus lease is occupied.
Only a successful, current projection updates future-session retrieval bindings.
Existing sessions retain their source pins and failed receipts remain unchanged.
An explicit `--attempt-id` starts a separately identified recovery attempt; do not reuse it with changed arguments.

Permission, missing-artifact, integrity, stale-revision and budget failures retain structured diagnostics.
For a corpus artifact that the configured reader cannot access, verify its scope and owner and grant only the required reader access through the operator's platform tooling.
NIMA performs no automatic ACL or ownership changes, and private-project artifacts must retain their visibility boundary.
Operator accounts running the corpus owner and CLI need coherent access to the selected corpus and SQLite shared-memory files.

Optional [normalized reasoning](NORMALIZED_REASONING.md) is off by default and can be disabled when vocabulary is unavailable.

Lexical retrieval and exact receipt/source reads use a consistent scoped SQLite snapshot alongside the corpus writer.
Embedding attempts use the operator-configured project research MCP endpoint through `NIMA_RESEARCH_MCP_URL`, so their receipts are written by the corpus owner.
Scope, pinned projection and embedding identity are checked on the returned native result; exact passages are authenticated through a fresh snapshot.
An unavailable corpus owner is reported without acquiring or stealing a writer lease.

Pinned projections are checked for existence, source revision, graph revision and scope.
Stale or missing projections have distinct diagnostics and require an explicit operator rebind.
Installation `retrieval_limits` configure the advertised private context bounds.
Its `owner_timeout_seconds` permits the corpus owner's exact-index validation and embedding attempt to finish; the default is 1,200 seconds, with an operator range of 30–3,600 seconds.
Ranked retrieval reports omitted evidence and is distinct from complete enumeration.
Both private services provide `enumerate_records` and `read_record` for deterministic record pages and exact canonical reads with hashes and scope checks.

The default public Tool Guide inventory is bounded to 32,768 bytes; private operation inventories are bounded to 16,384 bytes.
Select a public tool by exact name or ID for its full contract.
Pass an exact `operation` to the private catalogue for its complete input schema and limits.
The maintained Tool Guide canvas selects one output representation for MCP delivery.
Compact status never promotes operation completion or compilation to verified mathematics.
