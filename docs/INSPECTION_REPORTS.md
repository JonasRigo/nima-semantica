# Inspection and reports

## Export an OKF snapshot

```sh
nima export okf --corpus papers --project my-research --output ./project-okf
nima export okf --corpus papers --output ./corpus-okf
```

Use a new output directory. The command exports the selected revision with Markdown inspection files and OKF metadata, and prints its location.
Exports are on-demand snapshots; NIMA does not continuously mirror or watch Markdown files.
Editing an export does not change canonical storage.
Resume investigations using the project binding and canonical store rather than treating an old export as current state.

## Save and inspect results

Save Research Analysis creates immutable JSON and Markdown artifacts with exact evidence and receipt references.
Use Read Evidence to inspect the returned artifact IDs.
Private mathematics and proof exports contain working state and action history; saving such an export does not admit its contents to the project graph.

## LaTeX reports

Skills that produce a requested mathematical document use the bundled SciPost-style template in `nima-conduct-proof/assets/scipost-report.tex`.
The template is adapted from the ProofLab starter and includes mathematical statements, proof structure, failed routes, an evidence ledger and a notation appendix.
Replace example identifiers with actual NIMA records and omit sections that do not apply.
Keep scientific prose readable; place detailed receipt and reproducibility material in appendices.
Generate TeX and bibliography sources first, then compile a PDF when you have a TeX toolchain installed and want a PDF.
