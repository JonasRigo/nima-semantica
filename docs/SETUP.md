# Installation and model configuration

## Prerequisites

The deployment targets are Ubuntu/Linux and macOS Apple silicon with Python 3.11–3.13. See [platform verification status](PLATFORM_SUPPORT.md) before approving a release candidate.

Linux managed setup requires Docker Engine with Compose, systemd user services, Bubblewrap and `prlimit`.
PDF provisioning additionally needs Python 3.11; Lean provisioning needs Elan and Bubblewrap with the `--size` option for bounded temporary filesystems.
Debian 11's Bubblewrap 0.4.1 lacks that option: the Lean service can start, but theorem verification cannot complete with that version.
Install these operating-system prerequisites before running the wizard.

On macOS, install and start Docker Desktop with a Linux ARM64 engine and Docker Compose. Run a native ARM64 Python interpreter, not Rosetta. PDF and Lean execute in Linux containers; host Elan, Bubblewrap, `prlimit`, and systemd are not required. Docker must enforce memory, swap and process limits and permit the tested worker namespace isolation. Setup runs an isolation preflight and refuses to continue when it fails. Allocate sufficient Docker memory for the selected models and concurrent services; the PDF and Lean workers each have an 8 GB maximum.

Use a dedicated Python environment and install the release wheel with its `mcp` extra, or install a source checkout with `pip install -e '.[mcp]'`.
Building from source also requires Git so packaging can check the public inventory against the ignore rules.
For the Linux CPU stack, install `torch==2.13.0+cpu` from `https://download.pytorch.org/whl/cpu` first, as shown in the [quick start](../README.md#start-here), to avoid the default CUDA dependency download.
Linux managed Langflow and PDF provisioning select CPU builds explicitly. Native macOS Langflow uses native macOS wheels.
The Langflow application, base package and LFX runtime are pinned together at 1.12.0, with `lfx-openai==0.1.4`; managed builds apply the bundled `constraints-tested.txt` as well. Newer OpenAI extension bundles can satisfy dependency metadata yet fail to import against this LFX runtime; native setup checks those imports explicitly.

## Wizard

The same wizard configures independent LLM and embedding providers. LLM choices are OpenRouter, OpenAI, native Anthropic, native Gemini, native Ollama, or an arbitrary OpenAI-compatible endpoint (for example a local LM Studio or vLLM server). Model identifiers are operator-selected, not restricted to a cloud model list. Custom endpoint URLs, credential variable names, token limits and provider generation parameters are supported. A credential value is never written into installation JSON; `--credential -` explicitly selects a no-key compatible endpoint. Native Ollama does not require a cloud credential.

Embeddings are a separate choice: lexical-only (`none`), Ollama, OpenAI, or a compatible embedding endpoint. Ollama setup records the installed model digest and observed dimension. Compatible/OpenAI embeddings require an explicit revision and dimension. LLM and embedding servers may be different, including one local and one remote. No hosted LLM call is needed for corpus ingestion, indexing or deterministic retrieval.

An entirely local setup, after installing the chosen models in Ollama:

```sh
nima setup --non-interactive --provider ollama --model YOUR_LOCAL_CHAT_MODEL \
  --base-url http://127.0.0.1:11434 \
  --embedding-provider ollama --embedding-model YOUR_LOCAL_EMBEDDING_MODEL \
  --embedding-base-url http://127.0.0.1:11434 --provision
```

For compatible local servers, select `--provider compatible --base-url URL --credential -`; use the independent `--embedding-provider compatible --embedding-base-url URL --embedding-credential - --embedding-model MODEL --embedding-revision REVISION --embedding-dimension N` flags for embeddings. `--model-parameters` accepts a JSON object of generation options, and `--max-tokens` controls the output budget. Interactive setup asks for the same endpoint and credential choices.

Size the local model's context window for the workflow, not just its output. Use `--context-window 32768 --max-tokens 4096 --model-timeout 180`, choosing a window supported by your model and available memory. Ollama profiles default to an explicit 32,768-token window; a legacy `parameters.num_ctx` is also honored and must agree with an explicit window. Other providers use their server default unless you supply a window. Larger local windows consume more memory. Output limits do not increase the input context window.

NIMA preflights guarded model invocations with a conservative UTF-8 byte estimate plus message/tool framing and the output reserve. This is not exact provider tokenization and can reject inputs that a particular tokenizer would fit. It never silently trims source text or provenance: over-budget feedback asks for fewer regions, less retrieval/history, a smaller output reserve or a larger supported window. Remote servers must still enforce their actual limits. `context_window` and `request_timeout_seconds` are also available in installation profiles and per-tool overrides. SDK request timeouts default to 180 seconds; these are not whole-workflow deadlines and do not guarantee remote generation is cancelled.

Blocking tool execution runs off Langflow's event loop, keeping health and other non-store requests responsive. Store access remains serialized for consistency. Cancellation waits for an in-flight synchronous operation to finish or time out and close its store; it does not abandon a writer or automatically switch providers.

Provider support does not imply every model supports every workflow: agentic tools require reliable structured/tool-call output and usage reporting. Ollama cannot force tool selection server-side; NIMA rejects invalid or multiple tool calls and does not fall back to a paid cloud model. Choose a suitable local model for those tools; lexical/vector graph retrieval does not require these model capabilities. Native Anthropic and Gemini adapters are tested for construction and protocol binding, but live calls require the operator's own credentials.

```sh
nima setup --provision --pdf --lean
```

Choose the tool LLM and provider, an independent optional embedding model, and any tool-specific overrides.
Omitting embeddings enables lexical retrieval; vector retrieval requires the exact configured embedding model.
The wizard records effective assignments for every model input.
OpenRouter/OpenAI/compatible providers use their configured compatible API base; Anthropic, Gemini and Ollama use native APIs.
Supply custom endpoints interactively, through `--base-url`/`--embedding-base-url`, or an installation JSON.

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
Managed Langflow uses host networking on Linux and listens on loopback. On macOS, Langflow runs natively in `langflow-env` under the data directory, using Python 3.11 and a user LaunchAgent. No Langflow Desktop signup is required. Native Langflow, CLI and MCP share the corpus through macOS filesystem locks. Do not mount this corpus into Docker Desktop: host locks were not honored inside the container in verification. Compose publishes only execution-worker ports on loopback; workers never mount the corpus.
Custom deployments must provide reachable worker URLs and token-file paths.

The optional `pdf_container_url`, `symbolic_container_url`, and `lean_container_url` settings distinguish Langflow endpoints from native client URLs for custom container deployments. Each model profile also accepts `container_url`; an empty value preserves `base_url`. Managed macOS setup uses the native loopback endpoints for Langflow. Existing Linux configuration files remain valid.

macOS worker definitions and stage logs live in `installation/compose-macos.yaml` and `installation/` under the configured data directory. Langflow's generated `org.nima.langflow.<installation-id>.plist` lives in `~/Library/LaunchAgents` and starts at login; its stdout/stderr logs live under `installation/`. Setup leaves other Langflow installations untouched. Rerunning setup resumes completed builds, checks model assets again and restarts native Langflow. It preserves enabled PDF/Lean services and rejects operator-edited Compose or LaunchAgent definitions. Workers restart when Docker restarts. Only the trusted symbolic controller has Docker socket access; generated-code containers do not receive it. PDF and Lean retain Bubblewrap isolation inside non-root containers.

Native macOS provisional PDF reading also requires the managed `nima-fast-pdf` image; it uses a bounded Linux process because Darwin cannot enforce the reader's address-space limit. Missing Docker or a missing image produces an error, never an unbounded host fallback.

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
