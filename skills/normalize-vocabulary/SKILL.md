---
name: normalize-vocabulary
description: Create or refresh a corpus-named vocabulary from the linked NIMA project graph, retaining evidence, mathematical qualifiers, aliases and usage distinctions. Use when the user requests vocabulary normalization or a project workflow requires its vocabulary reference.
---

# Normalize project vocabulary

Read [the shared tool and state contract](../_shared/tools.md).
Resolve the corpus, project and configured CLI from `.nima/project.json`; consult project instructions and any existing vocabulary before choosing terminology.
The default output is `<corpus_id>-vocab.md` in the project directory, even when the inspected graph belongs to a differently named project.
Retain the exact inspected project scope in the file.

## Inspect an exact graph

Use the consolidated project graph as the reference and check its coverage and unresolved consolidation receipts.
Extraction candidates and private proof graphs are separate evidence; do not present their terms as admitted project vocabulary.
If the requested scope is incomplete, identify the missing coverage and label the vocabulary provisional instead of claiming normalization is complete.

Obtain a canonical scoped export with the installation's configured CLI:

```text
nima export okf --corpus CORPUS_ID --project PROJECT_ID --output PRIVATE_EXPORT_DIRECTORY
```

Use `cli_command` from the project binding when `nima` is unavailable on PATH.
Retain the returned revision and the export's snapshot content hash.
Read the exported concept properties, full scoped node identities, relations, statuses and attached evidence, not just the human-readable titles.
Use Load Ontology to resolve the graph's actual ontology digest and distinguish its node/relation vocabulary from scientific terminology.
Retrieve Research Context and Read Evidence can resolve definition passages and exact references when the graph's properties are insufficient.
An export may require the store owner to release its writer lock; use an existing revision-bound export or coordinate with that owner rather than opening a competing store, killing a worker or bypassing the lock.

## Choose evidence-backed terms

For each represented concept, record its preferred term, supported aliases, definition or meaning, usage distinctions and exact graph/source references.
Preserve source notation, domains, assumptions, parameter ranges, approximation conditions and mathematical qualifiers.
Use established scientific terms appropriate to the project and its literature.
Mark definitions or alias choices inferred by the harness as proposed conventions rather than source assertions.

Similar spelling or embedding similarity is a candidate for comparison, not evidence of synonymy.
Keep distinct definitions, parameterized variants, incompatible conventions and unresolved identity decisions separate.
An absent definition stays unresolved; do not invent one or upgrade a recorded assertion into a proved result.
Retain useful existing entries that the new snapshot cannot verify in a clearly separate legacy section, with their former scope and unresolved status.

## Write and verify the reference

Write the corpus-named Markdown file with a scope header containing the corpus and project IDs, inspected graph revision, snapshot hash, ontology digest(s), generation date and coverage limitations.
Organize entries so preferred terms, aliases, definitions, usage distinctions and exact references can be searched directly.
Include proposed additions, conflicting conventions and unresolved definitions as explicit sections when present.
Use semantic source line breaks and preserve equations and source notation.
Check entry citations against the inspected graph and exact source evidence; report the output path, scope and unresolved gaps.
If the graph changes during inspection, retain the pinned revision and mark the reference as describing that earlier snapshot, or refresh it from a new coherent snapshot.

Writing this local terminology reference does not approve renaming graph entities, merging identities, saving a new ontology or admitting a graph delta.
If the user also requests a persistent graph correction, prepare its exact proposal and use the existing graph-update approval contract.
