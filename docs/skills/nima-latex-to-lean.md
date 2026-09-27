# LaTeX to Lean

Use `nima-latex-to-lean` when you want the following outcome: lean sources, scoped kernel verification results and any unresolved correspondence between the informal and formal statements.

## Ask your agent

> Use `nima-latex-to-lean`: Formalize this LaTeX statement in Lean and verify the exact declaration in the configured environment.

Project initialization installs this skill for your selected harnesses.
See [projects and harness connections](../PROJECTS.md) if it is not available in a new session.

## Before you start

Supply the statement, definitions, assumptions and intended formal scope.

## How the investigation proceeds

Retrieve library context, use Draft Lean, inspect Verify Lean diagnostics and revise while preserving the target.
These steps guide your agent’s choices; adapt them to the evidence and the task.

## What you keep

Lean sources, scoped kernel verification results and any unresolved correspondence between the informal and formal statements.
Save substantive analyses through [Save Research Analysis](../tools/save_research_analysis.md) and inspect the returned artifact IDs.
Admit selected graph changes through [Update Project Graph](../tools/update_project_graph.md) with exact approval.
For a new chat, retain the project binding and relevant artifact or private session IDs, then inspect the current state before continuing.

[Portable skill instructions](../../skills/nima-latex-to-lean/SKILL.md) · [All skills](README.md)
