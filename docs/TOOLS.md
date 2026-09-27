# Tool catalogue

NIMA gives you small, inspectable operations that you can combine into an investigation.
We separate source preparation, interpretation, checking and graph admission because each changes a different part of the research record.
A skill helps your agent choose an approach; a tool has a defined input, output and effect.

Your installation connects 21 Langflow workflows and two private graph MCP servers.
The catalogue below contains 24 capabilities: those workflows, Calculate Mathematics, and the Draft Proof and Conduct Proof skills over the shared proof service.
The private services expose several individually callable state and execution operations.

Use [Tool Guide](tools/tool_guide.md) to inspect the current schemas.
The setup wizard configures each workflow’s model inputs; deterministic operations need no LLM, and private graph skills use your harness’s model.

## Discover and prepare

| Capability | Purpose |
| --- | --- |
| [Tool Guide](tools/tool_guide.md) | Inspect public tool contracts and delivery status. |
| [Load Ontology](tools/load_ontology.md) | Resolve an immutable ontology identity. |
| [Save Ontology](tools/save_ontology.md) | Validate and save an immutable ontology profile. |
| [Prepare and Index Sources](tools/prepare_and_index_sources.md) | Prepare exact source regions and derived retrieval indexes. |

## Find and read evidence

| Capability | Purpose |
| --- | --- |
| [Inspect Corpus](tools/inspect_corpus.md) | Inspect scoped corpus inventory and readiness. |
| [Retrieve Research Context](tools/retrieve_research_context.md) | Retrieve revision-bound passages and graph context. |
| [Read Evidence](tools/read_evidence.md) | Read exact source passages and provenance. |

## Interpret and inspect knowledge

| Capability | Purpose |
| --- | --- |
| [Deep Extraction](tools/deep_extraction.md) | Extract regional graphs and reconcile document-level proposals. |
| [Analyze Graph](tools/analyze_graph.md) | Analyze scoped graph structure and logical consequences. |
| [Trace Claim Dependencies](tools/trace_claim_dependencies.md) | Trace claim dependencies, cycles, and outstanding premises. |
| [Substantiate Graph Snapshot](tools/substantiate_graph_snapshot.md) | Assess graph fidelity and supporting or contradicting evidence. |

## Investigate and review

| Capability | Purpose |
| --- | --- |
| [Deep Research](tools/deep_research.md) | Develop source-grounded research analyses. |
| [Hypothesis Generation](tools/hypothesis_generation.md) | Generate or reformulate grounded hypotheses. |
| [Compare Research Objects](tools/compare_research_objects.md) | Compare hypotheses or mathematical objects. |
| [Search for Counterexamples](tools/search_for_counterexamples.md) | Search for independently checkable refuting witnesses. |
| [Review Research](tools/review_research.md) | Review claims, arguments, and research artifacts. |

## Calculate and prove

| Capability | Purpose |
| --- | --- |
| [Calculate Mathematics](tools/calculate_mathematics.md) | Execute symbolic or numerical calculations. |
| [Draft Proof](tools/draft_proof.md) | Develop proof strategies through a harness skill and private proof MCP graph. |
| [Conduct Proof](tools/conduct_proof.md) | Develop arguments using a private proof graph and the full research toolbox. |
| [Draft Lean](tools/draft_lean.md) | Retrieve library context, draft formal statements and repair proof attempts using verifier diagnostics. |
| [Verify Lean](tools/verify_lean.md) | Check a formal proof in a pinned isolated environment. |

## Retain and resume results

| Capability | Purpose |
| --- | --- |
| [Update Project Graph](tools/update_project_graph.md) | Apply explicitly approved graph changes. |
| [Save Research Analysis](tools/save_research_analysis.md) | Persist analyses, reports, and research bundles. |
| [Research Run](tools/research_run.md) | Record research runs and transitions. |

## Evidence and decisions

The corpus retains sources and their provenance.
Your project retains the investigation’s claims, dependencies and approved updates.
Private calculation and proof sessions retain unfinished reasoning independently of both.
This separation lets you test a proposal without quietly turning it into accepted knowledge.

Successful execution tells you that a represented operation completed.
Its scientific meaning still depends on the definitions, assumptions and source interpretation you supplied.
Inspect exact evidence, preserve unresolved questions and approve the specific project changes you want to retain.
