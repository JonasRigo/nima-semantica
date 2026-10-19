# v0.3.1 — configurable PDF recognition and OCR exports

Version 0.3.1 replaces local formula generation with a dedicated Langflow vision workflow and adds standalone OCR and ingestion exports.
PDF rendering, layout analysis, formula crops, physical-page batching and retained source evidence continue to provide the normalization context.
The recognition model and transcription prompt are editable in the private Langflow canvas.

The release candidate is prepared locally; publication and the hosted platform matrix remain pending.
See [qualification status](PLATFORM_SUPPORT_v0.3.1.md) for the evidence and its limits.

## OCR and ingestion commands

```sh
nima ocr paper.pdf --output /path/to/transcription.md
nima ingest papers paper.pdf --mode full --ocr-output-dir /path/to/transcriptions
```

Standalone OCR writes Markdown with LaTeX and a matching `.ocr.json` evidence sidecar without creating corpus records.
It refuses existing output files unless `--force` is supplied.
Exit status is 0 for complete normalization, 1 for partial normalization and 2 for an error.
Completion describes the parser's reported coverage, not independently verified transcription fidelity.

Ingestion exports the exact retained normalization used for the source, including evidence and unresolved formula diagnostics.
Files use `<source-stem>-<source-hash-prefix>.md` with a matching `.ocr.json` sidecar.
Use `--overwrite-ocr` to replace exports; corpus evidence remains immutable.
Export paths remain local to the CLI, including when ingestion routes through an active corpus owner.
An export failure returns a failure status while preserving completed corpus work, so replay can retry the export without repeating recognition.
`--ocr-output-dir` requires full preparation.

## Upgrade from v0.3.0

Full PDF preparation now requires an independent PDF model profile and the matching v0.3.1 PDF service and Langflow runtime.
The former recognition backend, its asset manifest and its checkpoint compatibility are removed.
Existing source artifacts and graph citations are preserved; historical recognition checkpoints are not reused by the new backend.
New recognition identities include workflow, model configuration and implementation fingerprints, and a changed profile creates a distinct preparation identity.

After installing the v0.3.1 wheel in the CLI environment, configure the dedicated profile and update the managed services:

```sh
nima setup --pdf --pdf-model-profile /path/to/vision-profile.json --provision
```

Interactive `nima setup --pdf --provision` asks for the PDF model when no dedicated profile exists.
For services maintained outside managed setup, update the NIMA package in the PDF and Langflow environments, start Langflow, and run `nima setup --pdf --pdf-model-profile /path/to/vision-profile.json` to install the private canvas.
Using v0.3.1 clients with an older PDF service is unsupported.
Review service configuration and preserve the installation data before upgrading.

The JSON file is a `ModelProfile`, with operator-selected provider, exact model identifier, endpoint, credential reference, output budget and optional generation settings.
The model must accept image input; no model is selected implicitly from the main profile or built into the application.
The installation's `pdf_recognition.concurrency` defaults to four and accepts values from 1 to 32.
Credentials stay in Langflow, while the trusted PDF broker handles bounded requests and the parser subprocess retains network isolation.
See [dedicated PDF configuration](SETUP.md#dedicated-pdf-vision-model) for details.

## Qualification and limitations

Local checks cover private workflow installation, real image submission, crop checkpoint reuse, short-PDF OCR, corpus ingestion and identical exported normalization.
The source and installed-wheel regression suites, archive inventory checks and upgrade-preservation checks are recorded in the [qualification status](PLATFORM_SUPPORT_v0.3.1.md).

No PDF model is certified by this release.
Both vision profiles tested on 30 saved crops produced mathematical transcription errors, including a lost subscript and changed multi-line bounds.
Neither profile passed the fidelity gate for adoption in the live installation.
Readiness and a successful OCR exit status do not certify the returned mathematics; inspect important formulas against the retained crops.
No recognition speedup over the former backend is claimed because comparable historical recognition timings were unavailable.

Existing follow-ups for an operator-facing exact graph-approval interface and a fresh scientific demonstration on the presentation Mac remain open.
This candidate does not change their status or upgrade running services automatically.
