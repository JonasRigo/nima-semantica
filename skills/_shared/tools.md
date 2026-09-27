# NIMA tool and state contract

Read `.nima/project.json` or run `nima project status PROJECT_ID --corpus CORPUS_ID` to establish the current installation and scope.
Discover the connected toolbox through MCP and consult Tool Guide for current request schemas.
Langflow tools accept a JSON request serialized in `input_value`; private mathematics and proof tools accept their typed `request` object.
Operator configuration owns corpus/project scope and capabilities; a request cannot widen them.

## Knowledge and working state

Corpus sources are shared within the installation. Project notes and project knowledge belong to the selected project.
Retrieve Research Context locates evidence; Read Evidence reads the exact returned region or artifact.
Preserve source IDs, revisions, quotation offsets and receipts. A citation is not a proof of its scientific assertion.
Use the configured scope when inspecting evidence, including when source bytes also exist in another project.

Prepare and Index Sources makes sources searchable. Deep Extraction proposes ontology-bound interpretations.
Analyze Graph and Trace Claim Dependencies inspect represented structure. Update Project Graph requires an exact proposal and its explicit approval.
Save Research Analysis persists a caller-authored report and its evidence; read the result back through Read Evidence.
Private graph exports do not themselves update project knowledge.

## Flexible workflow

Start from the user's question and current project state. Choose existing tools by need rather than following a mandatory pipeline.
Hypothesis Generation proposes routes; Compare Research Objects compares selected alternatives; Review Research assesses claims and arguments; Search for Counterexamples checks an explicit scoped encoding.
Deep Research discovers and reads literature, returning a source-attributed proposal and remaining questions.
Its fast-read proposal requires source preparation and exact reconciliation before project graph admission.
Use Draft Lean and Verify Lean when formalization serves the task; retain the exact target and verification environment.

Use `nima-calculate-mathematics` with `nima_math_*` for private calculation state.
Use `nima-draft-proof` and `nima-conduct-proof` with `nima_proof_*` for private proof state.
These skills can use the full toolbox.
For comparison of unadmitted private strategies, use caller-supplied mathematical objects with their exact target, assumptions and open obligations in the description.
Proof-specific comparison modes require an existing scoped graph target; never invent a project graph identity for a private node. The harness selects tools, decomposes work, evaluates alternatives and decides when to stop.
Scientific outputs remain uncertified. Operational completion and a scoped Lean receipt have distinct meanings.

## Recovery and continuation

After a rejected action, inspect its diagnostic and current revision before changing arguments.
Replay uncertain delivery only with the same request ID and identical arguments. Use a fresh ID for changed work.
Run mutations in a private session sequentially. Do not automatically rerun interrupted execution.
If the same unchanged operation fails twice, investigate configuration, missing evidence or the contract before another attempt.
A missing source, failed check or open necessary obligation remains explicit; it is not silently converted to a successful result.
Record terminal partial work with the exact outstanding prerequisite so another session can resume it.
Existing user authorization persists; seek additional approval only for an action outside that scope.

## LaTeX output

For requested mathematical reports, start from [the SciPost-style template](../nima-conduct-proof/assets/scipost-report.tex).
Write complete mathematical statements and source references, keeping NIMA IDs and detailed receipts in the evidence appendix where useful.
Keep Markdown and TeX prose on semantic source lines, not fixed-width hard wraps.
Produce `.tex` and bibliography sources; compile a PDF when a TeX installation is available and requested.
