# Projects and harness connections

The project binding `.nima/project.json` includes `cli_command`, an argument array
using the installation's Python interpreter. Use it when `nima` is not on PATH.
`nima project refresh PROJECT --corpus CORPUS` rebuilds the current project retrieval
projection and updates future-session pins without committing scientific graph changes.

List registered corpora and projects in the configured installation:

```sh
nima list
nima project list
nima project list --corpus papers
```

These commands emit JSON (`corpora` or `projects` arrays), sorted by corpus and
project ID. Empty installations and unmatched corpus filters return empty arrays.
They read local registrations without calling Langflow, LLMs, or workers, and do
not acquire the corpus writer lock. Project entries include the registration
directory and configured MCP URL; listing does not check service health.
Use `nima --config /path/to/installation.json ...` to select another installation.
Project folders without a `project.json` registration are not listed.

A corpus is shared within one NIMA installation; a project holds private notes and investigation-specific knowledge.
A private mathematics or proof session belongs to that project but does not itself become project knowledge.

## Initialize a workspace

```sh
nima project init my-research --corpus papers --path /path/to/workspace
```

This registers or reconnects the corpus, creates project-bound toolbox configuration, installs the private graph connections and copies portable skills.
The default harness selection is Codex, Claude Code and OpenCode; select a subset with `--harness codex,opencode`.
The command preserves unrelated client settings and refuses conflicting NIMA settings or edited installed skills.
`--offline` initializes local state and private graph configuration without publishing a Langflow toolbox.

The workspace receives `.nima/project.json` and `NIMA_PROJECT.md`.
Codex configuration lives in `.codex/config.toml`; Claude Code uses `.mcp.json`; OpenCode uses `opencode.json`.
Skills are installed under `.agents/skills` and, for Claude Code, `.claude/skills`.
Start your preferred harness in that workspace and complete its normal trust/connection prompts.
Its discovered tools include the project toolbox plus `nima_math_*` and `nima_proof_*`.

## Share project knowledge with the corpus

Preview all project-owned graph nodes/edges (no commit):

```sh
nima project promote my-research --corpus papers --rationale "Share reusable results"
```

Review the exact proposal, then repeat with `--approve-proposal HASH --approved-by OPERATOR`.
A changed graph produces another hash and requires a new approval. Source evidence must be
visible in corpus scope; this command does not publish private supporting documents.
Fast/provisional citations and scientific status are preserved. Promotion is sharing, not verification.

## Resume a new session

Read `NIMA_PROJECT.md`, then run:

```sh
nima project status my-research --corpus papers
```

Use the recorded installation, corpus and project identities; names in a chat message do not override operator scope.
Inspect existing research artifacts and graph sessions before repeating work.
Private graph mutations are sequential and revision-bound. Reopen the exact session; do not silently create a replacement after an interrupted operation.
Only one process owns a private graph database at a time; close an earlier harness connection before opening another writer.

Project bindings live under `DATA_ROOT/projects/CORPUS_ID/PROJECT_ID/`.
The shared canonical store is `DATA_ROOT/corpus/graph.sqlite3`, with immutable bytes under `DATA_ROOT/corpus/artifacts/`.
Private `math.sqlite3` and `proof.sqlite3` databases are in the project directory.
Keep installation-private paths and credentials out of public source control.
