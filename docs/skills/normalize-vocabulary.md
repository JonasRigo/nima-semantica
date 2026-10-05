# Normalize vocabulary

Use `normalize-vocabulary` to create or refresh a terminology reference from your linked NIMA project graph.
The default filename is `<corpus_id>-vocab.md`, while its header records the actual project scope, graph revision and snapshot hash.

## Ask your agent

> Use `normalize-vocabulary`: Refresh our corpus vocabulary from the consolidated project graph, retaining definitions, aliases, usage distinctions and exact evidence.

The skill compares graph concepts with their source definitions and keeps mathematical qualifiers, incompatible conventions and unresolved identity questions explicit.
It distinguishes ontology vocabulary, source terminology and proposed project conventions.
Incomplete graph coverage produces an explicitly provisional reference.
Writing the reference does not rename or merge graph objects.

Project initialization installs the skill for your selected harnesses.
For an existing project, repeat initialization with the same corpus, project and path to copy newly installed skill resources.
See [projects and harness connections](../PROJECTS.md).

[Portable skill instructions](../../skills/normalize-vocabulary/SKILL.md) · [All skills](README.md)
