# Private calculation and proof sessions

Use private graphs when an investigation needs its own working memory: tentative steps, failed routes, execution evidence and a frontier you can resume.
Your coding agent chooses the strategy; NIMA records operations and checks their represented dependencies.
Project initialization installs both MCP connections and binds them to your corpus and project.

## Calculation sessions

Open work with `nima_math_open`, then inspect status, frontier and available operations before continuing.
Use the [Calculate Mathematics skill](skills/nima-calculate-mathematics.md) for the full procedure.
The service uses isolated symbolic execution; there is no host-Python execution fallback.

## Contract and lifecycle

`math-session-v1` returns session, revision, status, request identity and result.
Operations cover lifecycle, frontier, exact inspection, export, provisional steps, retrieval, experiments, atomic calculations, substantiation and submission.
`nima_math_operations` exposes the compiler's supported operations.
MCP arguments use a typed `request` object; canvas JSON is that inner object.

Mutations require a current expected revision and request ID.
Identical retries return the recorded result; divergent replay is rejected.
Read-only inspection does not advance revisions.
Malformed arguments rejected by transport validation do not become graph attempts.
External work runs outside database transactions after a durable reservation.
Cancellation blocks admission; an already running isolated worker may continue until timeout.
Restart recovery retains interrupted attempts with unknown execution outcomes and never reruns them automatically.
Graph mutations and outcomes commit together; failed persistence leaves accepted state intact and the attempt recoverable.

Retrieval previews are bounded; exact passages and complete retrieval references remain inspectable.
Claims, execution observations and physical applicability retain distinct authority.
Completed closure requires a current admitted submission; partial closure retains the candidate and open obligations.
Export does not write to a project or corpus graph.

## Optional domain-independent helpers

Ordinary calculations need no indexed set or aggregation annotation.
When a task explicitly declares a finite indexed set, `run_calculation_graph` can accept an `aggregations` list instead of handwritten member wrappers and sums:

```json
{"id":"total","set_id":"branches","contributions":{"left":"left_value","right":"right_value"}}
```

Each value names an already declared local calculation step; every bound member must be supplied exactly once.
The controller appends member annotations and a sum in the declared member order, retaining the original terms and all their provenance and open obligations.
It does not infer missing terms, split an asserted total, supply weights, or assume equal contributions.
The same mechanism applies to algebraic branches, matrix contributions, probability outcomes, and physical channels.
Matching values in different members are allowed only as explicitly supplied mathematics, not an inferred multiplicity.
Expanded calculations retain the 128-step limit and the existing indexed-count checks.

Graph dependency, provenance, substantiation and submission reference fields also accept an exact producing request and output path:

```json
{"request_id":"previous-calculation","output_path":"answer"}
```

The controller resolves this inside the current session, from a completed immutable producing outcome and at the current expected revision.
There is no implicit latest-result selection or cross-session search.
For a scalar `record_step` value the path is `$`; for a bundle use an exact atomic path returned by that request.
Calculated outputs use their declared output keys.
References select existing nodes without promoting them: an assumption remains an assumption, a proposal remains provisional, and execution does not prove applicability.
Raw requests, resolved links and aggregation expansions remain inspectable in attempt outcomes.
Domain-specific checks apply only under their existing explicit contracts; these helpers introduce no physics-specific coefficient or rule.


## Proof sessions

Use [Draft Proof](skills/nima-draft-proof.md) to explore routes and [Conduct Proof](skills/nima-conduct-proof.md) to develop an argument.
Both use `nima_proof_*`; choose the session mode explicitly.
Record definitions, assumptions, lemmas, steps, evidence and open obligations, and attach authentic specialist receipts.
Superseding a supporting node reopens dependent obligations.
Use comparison, counterexample search, extraction, graph analysis, dependency tracing, hypothesis generation, calculation and Lean whenever they help the argument.
Private node identifiers are not canonical project graph identifiers; supply explicit object content when a comparison has no project target.

## Resume and retain work

Run `nima project status PROJECT_ID --corpus CORPUS_ID` to locate stored sessions.
Inspect the selected session and its current revision before sending another mutation.
Only one process may own a private graph database at a time; close the previous writer before changing clients.
Export a session for inspection, or save an analysis with exact references to its findings.
Use [Update Project Graph](tools/update_project_graph.md) when you decide to admit a selected result to project knowledge.
