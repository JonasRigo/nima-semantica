# Contribute a Langflow workflow

A NIMA workflow is an editable Langflow canvas with an explicit input/output contract, operator-owned scope and declared capabilities.
The harness decides when to call it.

## Build a first workflow

Start with the runnable `examples/custom_workflows/inspect_graph` package.
Open its canvas in Langflow, inspect the named request and result components, and run it against a disposable project.
It uses the existing Analyze Graph components, so its request and result contracts are available through Tool Guide.

For your own workflow, prefer existing NIMA and standard Langflow components.
Keep prompts, model inputs, evidence bindings and outputs visible.
Use the input, retrieval and output normalizers when adapting another flow, preserving exact region identities and scoped context.
A graph-producing flow returns a proposal; admission remains an explicit approved Update Project Graph operation.

## Package and configure

Keep `workflow.json` beside its referenced canvas. Use a namespaced name such as `mygroup.inspect_graph` and an explicit version.
Declare JSON input/output schemas and the required `allow_*` capabilities.
The installer enables only those declared capabilities for a custom workflow.
Model inputs use the installation default or an override keyed by the workflow name.
Credentials remain named references; never export resolved keys.

```sh
nima workflow validate /path/to/workflow.json
nima workflow install /path/to/workflow.json --corpus papers --project my-research
```

Validation checks canvas ports, connections and model wiring.
Installation writes the flow into the selected project's toolbox, verifies readback and records its package digest.
An identical install is a no-op. Changed packages require a new versioned name or explicit reconciliation of the existing deployment.
Discover the newly installed tool from the harness and run a small, scoped example.

## Test and maintain

Test valid input, invalid input, missing evidence and unavailable workers.
Check exact output IDs and receipts, and confirm that opening multiple result views does not repeat writes or model calls.
For project/corpus operations, test foreign scope and stale revisions.
Inspect the actual saved canvas and exported artifact; a successfully imported canvas alone is not an executed workflow.
Keep operational tests with the contribution and add its model roles and capabilities to its manifest.

## Build a source distribution

Use Git when building from source, including an unpacked source archive.
The build checks the public resource inventory against `.gitignore` before packaging it; ignored files are rejected even if previously tracked.
Installing a prebuilt wheel does not require Git.
Run `python -m pytest -q` in your development environment before contributing a change.
Tests that execute isolated calculations also need the configured symbolic worker or the local Docker worker image.
