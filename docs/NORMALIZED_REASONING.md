# Optional normalized reasoning

Normalized reasoning supplies the selected project's definitions, assumptions and usage distinctions when framing and assessing an argument.
It is off by default and reuses existing harness and tool model calls.
Vocabulary agreement does not establish scientific truth, source correspondence or project admission.

## Prepare and select a vocabulary

Use [normalize-vocabulary](skills/normalize-vocabulary.md) to create matching corpus-named Markdown and JSON references from exact project evidence.
Existing Markdown references remain readable; regenerate the structured companion before binding it for guidance.
The structured reference pins corpus/project scope, graph revision, ontology digests, graph snapshot hash, vocabulary content hash and coverage limitations.
Definitions, notation, qualifiers and conflicting conventions remain complete and inspectable.

```bash
nima project configure PROJECT --corpus CORPUS --normalized-reasoning on --vocabulary CORPUS-vocab.json --path WORKSPACE
nima project configure PROJECT --corpus CORPUS --normalized-reasoning off --path WORKSPACE
```

Configuration is recoverable and applies to new investigations.
Reload configured owners after their configuration files change, and refresh the published project toolbox to distribute its operator settings.
Active private sessions retain their original vocabulary pin and remain accessible after a setting change.
Hypothesis/review requests and private session `open` accept `normalized_reasoning: false` or `true` as an investigation override; omission inherits the selected project settings.

When disabled, NIMA performs no vocabulary reads and adds no vocabulary prompt or assessment calls.
When enabled without readable vocabulary, ordinary work can continue with an explicit `unavailable` diagnostic.
A stale reference needs explicit refresh for new work.
Foreign or tampered vocabulary bindings are rejected; disable guidance or bind a validated reference to continue.
Creating a vocabulary reference alone does not enable the feature.

## Inspect context and retain discoveries

```bash
nima vocabulary context PROJECT --corpus CORPUS --query "Selected investigation"
nima vocabulary read PROJECT --corpus CORPUS --entry ENTRY_ID
```

Context selects complete entries within the declared default 16 KiB allowance and reports omissions with an exact artifact readback reference.
Private mathematics `inspect` accepts `kind: "vocabulary"`; private proof `inspect` accepts `identifier: "vocabulary", state_section: "vocabulary"` to inspect the session's original context.
These reads do not revise the session or change scientific status.

New concepts, borrowed terminology, distinctions and challenges to existing definitions remain possible.
Record their provisional definitions, motivation, related concepts, evidence or speculative basis, changed assumptions and open questions.
The existing Save Research Analysis workflow persists snapshots with `save_vocabulary` and extension revisions with `propose_vocabulary`.
Private submissions and hypothesis/review actions accept optional `vocabulary_assessment`, `vocabulary_findings` and `vocabulary_extensions` from the existing model/harness assessment.
Findings bind the selected snapshot and exact argument and remain advisory.
Neither unknown words nor unresolved disagreements force an argument to conform to the existing reference.
Persistent ontology edits, identity merges and graph admission retain their own exact proposal and authorization contracts.
