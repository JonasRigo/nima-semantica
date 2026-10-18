---
name: nima-write-report
description: Write scientific papers, research reports, progress reports and proof reports from NIMA evidence, with shared SciPost-style sources and reproducible plots or illustrations.
---

# Scientific reports

Read the project binding and [shared tool contract](../_shared/tools.md).
Resolve output destinations through `layout.paths` in `.nima/project.json`.
Use `nima report init NAME --kind paper|research|progress|proof --path WORKSPACE` for a new report; preserve and edit an existing manuscript when appropriate.
The shared [SciPost-style template](../_shared/reporting/scipost-report.tex) generalizes the proof-report template; omit sections that do not serve the requested report.

Read exact source evidence and saved private calculation/proof records.
When a calculation is included, produce its detailed derivation with `nima math report SESSION --path WORKSPACE` and use its actual results, qualifications and provenance.
Use the session's pinned vocabulary; label an unsealed glossary provisional.
Keep scientific explanations readable and place detailed receipts, notation and every open obligation in appendices.
Do not hard-wrap Markdown or TeX prose; break source lines at semantic boundaries.

For plots or illustrations read [figure standards](../_shared/reporting/figures.md), and load [domain conventions](../_shared/reporting/domain-illustrations.md) only when their subject applies.
Choose final physical dimensions before drawing.
Use the managed Blender toolchain for geometry that benefits from depth, retaining vector typography, arrows, diagrams and plots.
Use `nima_semantica.figure_tools` for LaTeX/Computer Modern plots and physical-size export checks.
Every figure needs a notation/assumptions/caption companion, reproducible source and data, editable masters, PDF and PNG.
Keep substantial alternatives under separate versioned names.

Run `nima report doctor` to check dependencies and `nima report build NAME --path WORKSPACE` to compile a generated report.
Inspect the PDF at final page size, including mathematical indices, accents and daggers.
Record partial or conditional results honestly; document creation does not admit graph knowledge or resolve scientific obligations.
