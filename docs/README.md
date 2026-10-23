# NIMA user guide

Use NIMA to carry an investigation across papers, calculations and coding-agent sessions without losing its evidence or unfinished questions.
You choose the questions and decide what belongs in the research record.
We provide persistent knowledge, inspectable tools and adaptable skills to support that work.

See the [v0.4.0 release notes](RELEASE_NOTES_v0.4.0.md) for the current source candidate.
The [qualification status](PLATFORM_SUPPORT.md) separates v0.4.0 checks from historical platform evidence and the open PDF model-fidelity gate.

## Get started

1. [Install NIMA and configure models](SETUP.md) with the setup wizard.
2. [Initialize a project and connect your harness](PROJECTS.md).
3. [Ingest papers or private notes and chat with your knowledge base](KNOWLEDGE_BASE.md).
4. Choose a [research skill](skills/README.md), then inspect the resulting evidence and artifacts.

## Work with your project

| Task | Guide |
| --- | --- |
| Ask questions with `nima chat` or your coding-agent harness | [Chat with the knowledge base](KNOWLEDGE_BASE.md#chat-with-corpus-literature-and-project-notes) |
| Investigate, calculate, review or develop a proof | [Research workflows](RESEARCH_WORKFLOWS.md) |
| Compare the expected merit of research actions | [Expected utility](EXPECTED_UTILITY.md) |
| Understand a tool and its effects | [Tool catalogue](TOOLS.md) |
| Continue private calculations and proofs | [Private graph sessions](PRIVATE_GRAPHS.md) |
| Diagnose MCP connections and configure reliable private services | [Reliability and recovery](RELIABILITY.md) |
| Guide arguments with optional project vocabulary and retain new concepts | [Normalized reasoning](NORMALIZED_REASONING.md) |
| Inspect Markdown exports and write reports | [Inspection and reports](INSPECTION_REPORTS.md) |
| Understand graph interchange | [NIMA’s OKF profile](OKF_PROFILE.md) |
| Add your own tool | [Contribute a Langflow workflow](CONTRIBUTING_WORKFLOWS.md) |

## Why the graphs are separate

A source tells you what was written; an investigation records what you propose and what you have checked.
NIMA retains this distinction through a shared corpus, project knowledge and private working graphs.
An ontology makes the vocabulary of an interpretation explicit, while provenance lets you return to its source.
Neither a tidy graph nor a fluent summary settles the underlying scientific question.
The useful result is an argument you can inspect, challenge and resume.

See [standard knowledge, workspace layouts, Studio, calculation documents and reporting](KNOWLEDGE_AND_REPORTING.md) for the v0.3.0 features.
