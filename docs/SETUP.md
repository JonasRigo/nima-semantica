# Installation and model configuration

## Prerequisites

The managed stack targets Linux with Python 3.11–3.13, Docker Engine with Compose, systemd user services, Bubblewrap and `prlimit`.
PDF provisioning additionally needs Python 3.11; Lean provisioning needs Elan and Bubblewrap with the `--size` option for bounded temporary filesystems.
Debian 11's Bubblewrap 0.4.1 lacks that option: the Lean service can start, but theorem verification cannot complete with that version.
Install these operating-system prerequisites before running the wizard.
Use a dedicated Python environment and install the release wheel with its `mcp` extra, or install a source checkout with `pip install -e '.[mcp]'`.
Building from source also requires Git so packaging can check the public inventory against the ignore rules.
For the Linux CPU stack, install `torch==2.13.0+cpu` from `https://download.pytorch.org/whl/cpu` first, as shown in the [quick start](../README.md#start-here), to avoid the default CUDA dependency download.
Managed Langflow and PDF provisioning also select CPU builds explicitly.
The Langflow application, base package and LFX runtime are pinned together at 1.12.0; managed builds apply the bundled `constraints-tested.txt` as well.

## Wizard

```sh
nima setup --provision --pdf --lean
```

Choose the tool LLM and provider, an optional Ollama embedding model, and any tool-specific overrides.
Omitting embeddings enables lexical retrieval; vector retrieval requires the exact configured embedding model.
The wizard records effective assignments for every model input.
OpenRouter and compatible providers use their configured API base; Ollama uses its compatible chat endpoint.
Supply custom endpoint/provider settings through `--base-url` or an installation JSON.

`--provision` authorizes dependency and model downloads and service startup.
PDF assets are downloaded and verified in a separate environment; ingestion uses the advanced NIMA normalizer.
Lean uses the pinned toolchain recorded during setup. Unavailable selected dependencies stop the stage with an actionable error.
Omit `--provision` to configure an installation using services you already manage.

The installation file is `$XDG_CONFIG_HOME/nima/installation.json`, normally `~/.config/nima/installation.json`.
Override it with `NIMA_CONFIG` or `nima --config /path/to/installation.json ...`.
Research data defaults to `$XDG_DATA_HOME/nima`, normally `~/.local/share/nima`.
Private provisioning logs and stage checkpoints live under its `installation/` directory.
Rerun setup to resume completed stages; it preserves existing configuration backups and refuses conflicting service definitions.

## Model overrides and credentials

The `llm` profile is the shared default. `overrides` selects a tool ID or an exact role printed by setup.
Each profile specifies provider, model, endpoint, credential-variable name, output-token limit and provider parameters.
The embedding profile separately pins model, revision and dimensions.
Changing an embedding identity requires an explicitly rebuilt index.

```json
{
  "data_root": "/home/researcher/.local/share/nima",
  "llm": {
    "provider": "openrouter",
    "model": "YOUR_MODEL_ID",
    "base_url": "https://openrouter.ai/api/v1",
    "credential": "NIMA_MODEL_API_KEY",
    "max_tokens": 8192
  },
  "overrides": {}
}
```

```sh
nima setup --from-config installation.json --non-interactive
```

Keep credential values out of this file.
During project installation, existing Langflow Credential variables are reused; missing values come from the named environment variable or a hidden terminal prompt.
Deterministic tools need no LLM. Mathematics and proof graph skills use the model selected in the coding-agent harness.

## Verify and maintain

```sh
nima doctor
nima project init my-research --corpus papers --path /path/to/project
```

Project initialization installs the configured canvases and records exact live identities.
A live canvas changed in the editor must be exported and reconciled before replacement.
Managed Langflow uses host networking on Linux and listens on loopback; the data directory is shared with its container at the same absolute path.
Custom deployments must provide reachable worker URLs and token-file paths.

Back up the data directory with services stopped, or use a consistent database backup procedure.
Preserve SQLite databases, their sidecars, immutable artifacts, private graph databases and installation/project manifests together.
Never replace a database with an OKF export or delete a writer lock to take over a live process.

## Literature discovery

The default discovery providers are arXiv, OpenAlex and Crossref.
Deep Research v11 is configured for provisional fast reading; full-text acquisition is initially limited to `arxiv.org` and `export.arxiv.org` with a 20 MB response limit.
Set `discovery`, `acquisition.domains` and `allow_fast_read` in the installation file to choose the permitted providers and source hosts before project installation.
Other hosts remain unavailable until included in that policy.
This setting does not automatically ingest sources into canonical corpus knowledge or approve project graph changes.

Lean verification is enabled when its worker is configured.
External LeanSearch is optional: set `allow_lean_search` to `true` to permit sending theorem queries to that service.
