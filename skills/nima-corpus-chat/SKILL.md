---
name: nima-corpus-chat
description: Answer questions about corpus literature, project notes and existing NIMA artifacts with exact evidence citations.
---

# Chat with Knowledge Base

Read [the shared tool and state contract](../_shared/tools.md) before acting.

Establish whether the question concerns corpus sources, project notes, project knowledge or a combination, and retain the project binding.
Use Inspect Corpus for readiness/inventory, Retrieve Research Context for passages, and Read Evidence for exact known regions or artifacts.
Use Analyze Graph or Trace Claim Dependencies when the question concerns recorded relations or prerequisites.
Answer directly with exact citations. If evidence is absent, report the searched scope and missing material.
Broaden into Deep Research only when the user requests a wider investigation.
For an ordinary KB conversation, no ingestion, graph update or report save is required.
For a requested saved answer, use Save Research Analysis and verify its returned artifact through Read Evidence.
A new conversation resumes by reading the project manifest and canonical state, not by reconstructing prior chat from memory.

For direct graph questions select Retrieve Research Context `knowledge="graph"`; use `knowledge="both"` for combined evidence.
Choose `visibility="corpus"`, `"project"` or `"both"` and preserve the operator binding.
Use the installation's configured evidence ceiling and inspect the actual returned passages, token estimator and omissions.
New terminal and owner-backed chat defaults use 12,000 estimated evidence tokens, 12 candidate seeds and at most two graph hops; explicitly configured older ceilings remain enforced.
For a passage that introduces a definition but ends before it, retrieve its adjacent continuation with exact source provenance.
Fetch exact cited nodes/passages when needed rather than dumping inventories, private graph histories or the full corpus into the conversation.
Do not add a separate NIMA answering-model call inside harness chat.
The terminal equivalent is `nima chat --corpus CORPUS --project PROJECT --knowledge both`; use `--question` and `--json` for a single inspectable answer.
