# Knowledge, calculation reports and scientific figures

NIMA supports source evidence, admitted corpus/project graphs, private mathematical and proof sessions, and human-readable project artifacts.
Scientific status stays attached to each object when it is retrieved, rendered or exported.
An export or an answer does not admit a scientific claim.

## A human-readable workspace

New workspaces resolve a versioned layout from `project_layout` in the installation configuration and optional `nima project init --layout layout.json` overrides.
The resolved paths are pinned in `.nima/project.json`; changing installation defaults affects future projects.
Existing workspaces keep their files and paths until explicit adoption.

| Role | Default directory | Content |
|---|---|---|
| sources | `sources/` | Original documents and source inventories |
| excerpts | `excerpts/` | Checked excerpts and transcription notes |
| ontology | `ontology/` | Project ontology and admission proposals |
| vocabulary | `vocabulary/` | Sealed or provisional vocabulary snapshots |
| calculations | `calculations/` | Detailed derivations, exact graph exports and provenance |
| reports | `reports/` | Paper, research, progress and proof documents |
| figures | `figures/` | Versioned figure masters, previews and companions |
| scripts | `scripts/` | Reproducible analysis and rendering code |
| receipts | `receipts/` | Validation and execution evidence |
| exports | `exports/` | OKF and other interchange bundles |
| archive | `archive/` | Superseded reviewed candidates |

A partial override is enough:

```json
{"paths":{"calculations":"calculation","reports":"manuscript"}}
```

Paths must remain inside the workspace and must not traverse symlinks.
Adoption creates directories and pins their meanings without moving existing scientific files.
It saves the original binding for recovery and requires the exact preview hash:

```bash
nima project configure experiment --corpus papers --path . --layout layout.json --preview-file layout-preview.json
nima project configure experiment --corpus papers --path . --layout layout.json --preview-file layout-preview.json --apply HASH_FROM_PREVIEW
```

## Ingestion and retrieval

```bash
nima ingest papers source.pdf --index-mode vector
nima chat --corpus papers --project experiment --knowledge sources --question 'What approximation controls the calculation?' --json
nima chat --corpus papers --project experiment --knowledge graph --question 'Which assumptions remain unsupported?' --json
nima chat --corpus papers --knowledge sources --scope corpus --question 'What is a regulator?'
```

Add `--project experiment` for project-private sources; omit it for corpus sources shared with connected projects.
Local files submitted to an active owner are staged under the installation's `ingestion-inbox` with exact byte hashes and a scope manifest.
Raw PDF bytes do not pass through a model prompt.
Original PDF normalization runs in resumable page batches; cached batches are checked before reuse.
Physical PDF page locations are retained, without assuming an offset to printed page numbers.
Unresolved formula recognition stays explicitly provisional and is never presented as a verified equation.
An index may be ready while mathematical normalization remains partial; inspect both fields.

`--knowledge sources|graph|both` selects evidence type; `--scope corpus|project|both` selects visibility.
Direct graph search works without a matching source passage and expands only a bounded neighborhood.
`--retrieve-only` returns the native packet without an answer-model call.
Harness corpus-chat uses the same retrieval boundary and its own model; terminal chat makes one configured answer call.
When the configured model credential is absent from the terminal environment, terminal answering uses the maintained **Answer Knowledge** owner workflow and its operator model connection.
It never reads a stored credential back into the shell.
A configured owner connection is required in this case.

Defaults are a conservative 4,000-token evidence upper bound, a 1,000-token history upper bound, and a 1,024-token answer limit.
The conservative estimator counts UTF-8 bytes, which overestimates ordinary model token counts.
The bounds cover evidence/history separately, not the complete prompt framing or provider accounting.
Actual provider usage is returned when available.
`chat` in installation configuration controls these defaults; the owner enforces its configured ceilings.
Start an interactive terminal session by omitting `--question`; use `/quit` to leave.
Every answer citation resolves to a source region or graph reference in the current packet.

## The complete project in upstream OKF Studio

```bash
nima project graph experiment --corpus papers --path . --open
```

The standard export includes the visible admitted graph, source regions, the pinned vocabulary, every visible private math/proof session, receipts, and open obligations.
Use `--session ID` to select sessions or `--no-private` for admitted knowledge and sources only.
The generated root is `exports/studio-complete` by default and can be watched by upstream OKF Studio.
Generated-file hashes protect human edits against silent replacement.
Private working state retains its original scientific status in metadata; its presentation in OKF does not change admission.
Private exports should be shared only with the intended audience.
Canonical OKF graph export remains available separately.

NIMA uses the upstream application, with no second graph GUI.

**Linux (x86_64):** install the upstream `.deb` or AppImage from the [OKF Studio releases](https://github.com/saschb2b/okf-studio/releases/tag/v0.10.0).
Standard graphical NIMA setup can install the pinned official Debian package into NIMA's installation directory.
Headless installations still support exports.

**macOS:** build OKF Studio from source; upstream does not provide a macOS installer in its releases.
Install the Xcode Command Line Tools (`xcode-select --install`), a stable Rust toolchain through rustup, and Node.js 20.19+ or 22.12+ with pnpm, following the [upstream build instructions](https://github.com/saschb2b/okf-studio/blob/v0.10.0/README.md#develop-and-run).
Build the version used by NIMA's Linux installer:

```bash
git clone --branch v0.10.0 --depth 1 https://github.com/saschb2b/okf-studio.git
cd okf-studio
pnpm install
pnpm tauri build
```

The macOS build produces the native executable and `.app`/`.dmg` bundles.
With the default build output directory, configure NIMA to use the executable:

```bash
export NIMA_OKF_STUDIO="$PWD/target/release/okf-viewer"
nima project graph experiment --corpus papers --path /path/to/workspace --open
```

On either platform, an existing executable can be selected with `studio_executable` in the installation JSON or `NIMA_OKF_STUDIO` in the shell environment.
Use an absolute path to the executable itself, rather than a macOS `.app` directory.
Save the environment setting in your shell startup file or use the installation JSON to retain it for future sessions.

## Detailed atomistic calculation documents

```bash
nima math report SESSION_ID --path .
```

Calculate Mathematics ends by writing `calculation.md`, the exact private graph snapshot and a provenance manifest under the configured calculations directory.
The selected output derivation is ordered by dependency, followed by the full retained audit.
Every node, operand, exact value, origin, execution receipt, condition, discarded remainder and open obligation remains traceable.
The session's full sealed vocabulary is included when pinned; otherwise the report clearly labels a provisional glossary.
The export uses a consistent read-only snapshot or the configured private owner and never recovers or mutates a session.
A conditional calculation remains conditional in the human-readable report.

## Shared reports and readable figures

```bash
nima report doctor
nima report init first-report --kind research --path .
nima report build first-report --path .
```

The same SciPost-style template supports `paper`, `research`, `progress` and `proof` roles.
Each includes source context, methods, results, interpretation and an appendix for open obligations.
Templates are starting documents, not completed scientific results.

Standard `nima setup --provision` includes the reporting dependencies and a managed or existing Blender installation.
`--minimal` explicitly skips reporting provisioning.
Python-only installation can use `pip install 'nima-semantica[reporting,chat]'`; `nima report doctor` diagnoses external Blender/LaTeX dependencies.
Linux Blender archives are checksum-pinned; existing Blender is preferred and its version recorded in rendering receipts.

Use `nima_semantica.figure_tools.global_plot_defs()` for actual LaTeX rendering with Computer Modern and 14/15 pt defaults.
Choose final physical dimensions before rendering; never shrink a large figure to a page without checking the resulting text size.
`export_figure` saves the editable SVG, vector PDF, PNG preview, companion Markdown, reproducible source and data identity.
Blender geometry can be embedded in a hybrid SVG/PDF with vector typography, arrows and plots.
The geometry remains editable in a `.blend` master; raster geometry does not become vector geometry merely because the container is SVG.

The shared reporting references contain the full illustration conventions, including physical operator hats, local arrow meanings, readable strokes, consistent colors and explicit physical assumptions.
Conditional domain examples for NQS, SCOL, electron/spin geometry and operator strings are separate from the general scientific guidance.
Keep equations that are unnecessary for visual understanding in captions and companion Markdown.
Result panels must use numerical data or referenced benchmarks, with parameters and comparison conditions recorded.
Preserve reviewed alternatives under versioned names.

## Upgrade and ownership behavior

Managed skills have a hash manifest in `.nima/skills.json`.
Unmodified managed files update with the package; independently edited files are listed as conflicts and preserved.
Configuration reconciliation records a reviewable preview and recoverable before-bytes.
Existing operator connections and project/session pins are retained.
Managed Linux Langflow runs with the data-root owner's UID/GID and writable HOME/cache directories so new artifacts are readable by the native owner.
This does not retroactively change ownership of historical artifacts; an existing installation requires an explicit, scoped migration with backups and byte verification.

For manually launched private Docker services, set `NIMA_SERVICE_UID` and `NIMA_SERVICE_GID` to the native data owner (`id -u` and `id -g`); the Linux template defaults to 1000:1000.
