# Third-party notices

## NIMA-AGI PDF ingestion

The formula-quality gates, crop retries, recognition checkpoints, and detached-negation handling in `src/nima_semantica/pdf/_ported.py` are adapted from `src/nima/semantica_adapter.py` at NIMA-AGI revision `c05b1ac05909e1de656abdf88b0a4fd694da7a97`.
The PDF model manifest and selected regression fixtures are also derived from that revision.
Copyright 2026 NIMA-AGI contributors; licensed under Apache-2.0.
See `licenses/NIMA-AGI-Apache-2.0.txt` and `licenses/Apache-2.0.txt`.

Changes: removed all NIMA-AGI application and Semantica imports, introduced NIMA-Semantica-owned errors and hashing, made parser assets explicit and offline, added conversion-status checks and source-item provenance, and isolated deployment/configuration from the originating application.
These files retain Apache-2.0 licensing; the project's MIT license does not replace their license.
Model weights retain their respective upstream licenses and are separately provisioned, not redistributed in the source tree.
