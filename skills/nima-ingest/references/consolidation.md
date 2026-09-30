# Coherent multi-paper graphs

Keep the extraction manifest: each source, selected regions, coverage/deferred work, saved
candidate IDs and preparation warnings. A successful regional batch is not full-paper coverage.

Use Deep Extraction `consolidation_plan` with the selected ontology, exact current
`graph_revision` and `consolidation.artifact_ids`. Inspect `nodes`, evidence and origins;
follow `next_request` or search the whole inventory with `consolidation.query`. Planning
does not merge anything. The `plan_hash` binds the complete selection, not just one page.

Resolve identities within each paper first, then across papers. Judge identity from source
definitions and scope, not names alone. Distinguish parameterized quantities, smoothing and
normalization conventions, assumptions and claim attribution. Keep incompatible statements
separate. A relation needs the ontology's correct types and endpoint rules plus supporting
evidence; co-occurrence alone does not support a dependency or a supports edge.

Call `consolidate` with a fresh operation ID, the same selection/revision, and nested
`plan_hash`, `merges`, `links`, and `unresolved`. Each merge names disjoint `node_ids`, a
member `canonical_id`, and an evidence-backed `rationale`. Each link names `source_id`,
`target_id`, `relation`, endpoint `region_ids` and `rationale`. These are proposed semantic
decisions, not independent verification. Never fabricate a common hub to force connectivity.

The tool validates and persists a new `graph_proposal`; it does not commit. It preserves
all evidence, original property variants, provisional labels and leaf artifact lineage.
For more work, plan with this consolidated artifact plus new candidates, not its original
inputs again. Existing project identities must be retained when merging new candidates into
them. Corrections combining multiple already-admitted identities require a separate approved
delta. Consult Tool Guide for current per-call limits; there is no fixed total batch count.

Prepare the final saved artifact with Update Project Graph, present the exact delta for
approval, and commit only within existing user/operator authorization. Then Analyze Graph
at the committed revision. If graph or byte limits are exceeded, report the exact constraint
and request an operator change or a narrower coherent scope; never silently truncate or
split an atomic approved delta.

Report coverage and connectivity separately: source/region counts and deferrals, nodes,
edges, connected components, uncertain identities, conversion warnings and analysis status.
Analyze Graph checks represented structure, not whether all semantic relationships were found.
