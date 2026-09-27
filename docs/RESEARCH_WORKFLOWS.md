# Research workflows

Choose a [skill](skills/README.md) that matches your question.
It guides your agent through the relevant tools and leaves room to change the approach when the evidence demands it.
Research, literature review, KB chat, critique and focused review have separate entry points.
All can use current project knowledge and exact corpus evidence.

## Calculate Mathematics

Use `nima-calculate-mathematics` with the private `nima_math_*` service.
Ask your agent to explore, retrieve methods, execute isolated calculations and record dependencies.
Inspect the frontier for selected outputs before submission; preserve unresolved assumptions and failed attempts.
The private graph survives a harness session and remains separate from the project graph.

## Draft Proof and Conduct Proof

Use `nima-draft-proof` to explore routes and proposed lemmas in draft mode.
Use `nima-conduct-proof` to develop a selected argument in conduct mode.
Both use `nima_proof_*` and can invoke the complete existing toolbox: comparison, counterexamples, extraction, graph analysis, dependency tracing, hypothesis generation, review, retrieval, calculation and Lean.

Record targets, assumptions, steps and obligations in the private proof graph.
Attach authentic specialist receipts; revise steps by adding replacements with explicit supersession.
Draft completion can retain open lemmas. Completed conduct sessions require a current selected argument with represented obligations addressed.
Informal arguments remain uncertified. Lean verification is optional and retains its exact formal scope.
Close partial work with a concrete outstanding prerequisite rather than losing it when a chat ends.

## Literature, review and persistence

Deep Research returns source-attributed regional/consolidated proposals and remaining questions.
Fast-read proposals require preparation and exact source reconciliation before project-graph admission.
Hypothesis Generation and Compare Research Objects help choose alternatives; Review Research and scoped counterexample checks examine selected claims.
Save requested analyses through Save Research Analysis, then read back their artifacts.
Project graph updates retain their separate exact approval boundary.

See the [tool catalogue](TOOLS.md) for individual operations and [private graph sessions](PRIVATE_GRAPHS.md) for persistence and resumption.
