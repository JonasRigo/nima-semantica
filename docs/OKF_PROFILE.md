# NIMA OKF profile and interchange boundary

Use NIMA’s OKF exports to inspect a graph as Markdown or exchange its represented state.
We use Google Open Knowledge Format v0.2 as the external interchange reference.
The reference is [the upstream specification at commit ad30107c31c06aec8a7d5636e0d1058118604e6f](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/ad30107c31c06aec8a7d5636e0d1058118604e6f/SPEC.md).
This pins the design reference; it does not assert complete implementation conformance.
No upstream OKF implementation library is required.

## Representation and authority

The external format represents concepts as UTF-8 Markdown with YAML frontmatter and relationships as links; it permits producer extensions.
NIMA uses this interchange boundary through `src/nima_semantica/okf_io.py`.
Its graph profile is defined by the version-2 `OKFNode`, `OKFEdge`, `OKFSnapshot`, and `OKFDelta` contracts and the generic `OKFReference` contracts in `src/nima_semantica/okf_contracts.py`.
These are NIMA-specific typed contracts, not class definitions supplied by the upstream specification.
They carry scope, revisions, ontology identity, exact evidence, scientific status, and approved mutation semantics.

`src/nima_semantica/okf_mapping.py` translates the profile into concept documents using `nima.node` and `nima.relations` extension metadata.
Markdown relation links expose connectivity; the extension retains typed edge identity and provenance.
Disagreement between the generated relation links and relation metadata is an import conflict.
General OKF document ingestion and admission of a NIMA graph are separate operations: a valid external concept need not contain NIMA metadata, and a NIMA graph import requires explicit scope, ontology, evidence, and commit checks.
External lifecycle or trust fields never confer NIMA scientific verification or graph-admission authority.

SQLite remains the persistence implementation, GraphRAG indexes remain derived projections, and Semantica remains the logic/reasoning dependency.
The interchange layer creates no second authoritative graph and performs no implicit promotion or graph commit.

## Supported interchange

The current adapter reads and writes concept frontmatter, bodies, and extension fields, retains root index metadata/body and skips nested index/log files on import, and translates NIMA node/edge metadata.
Existing tests cover simple concept and graph round trips and relation disagreement.
Those tests establish this subset only.

NIMA's manifest-backed snapshot profile now round-trips losslessly, including object order; this does not imply lossless interchange of every upstream OKF feature.
Arbitrary nested index documents, log history, binary attachments, upstream trust/lifecycle interpretation, and unsupported bundle versions are not graph-admission features.
Do not assume full upstream conformance when exchanging features outside this supported subset.
Export requires a new destination and rejects existing directories and symlinked ancestors.
See [inspection and reports](INSPECTION_REPORTS.md) for export commands and [projects](PROJECTS.md) for canonical storage and resumption.
