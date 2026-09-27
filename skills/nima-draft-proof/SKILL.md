---
name: nima-draft-proof
description: Develop and compare proof strategies using a private NIMA proof graph and the full research toolbox.
---

# Draft Proof

Read [the shared tool and state contract](../_shared/tools.md) before acting.

Open a `nima_proof_open` session in draft mode with the exact target and assumptions, or inspect an existing bound session.
Record definitions, candidate strategies, proposed lemmas, dependencies and obligations using `record_node`.
Choose any useful existing tool: retrieval, Deep Extraction, Hypothesis Generation, Compare Research Objects, Analyze Graph, Trace Claim Dependencies, calculation, counterexamples, review or Lean.
Attach authentic specialist receipts with `attach_receipt`; failed checks remain history.
Compare routes and record why a route was selected, revised or abandoned. Inspect the frontier when selecting the draft.
Submit the strategy node with its argument. A completed draft may retain explicit open lemmas; it is not a completed proof.
Close partial when no useful route was found, preserving the blockers. Export the draft with its exact nodes for Conduct Proof when the user requests execution.
Conduct Proof opens a conduct session linked to this draft; the original draft mode is immutable.
