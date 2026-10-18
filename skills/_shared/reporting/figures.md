# Scientific figures

Choose the final physical dimensions before building the composition.
Use a white background, straight explanatory geometry, restrained and consistent colors, clear strokes and crisp mathematical typography.
Physical illustrations may use additional colors when they make objects and processes distinguishable.
Combine color with labels, line styles or symbols so meaning survives grayscale viewing.

## Explanation and composition

Match the visual explanation to the figure's role.
An overview or physical-phenomenon illustration should show recognizable materials, physical processes, short plain-language labels and an intuitive sequence.
Keep detailed equations, assumptions and extended interpretation in the companion and caption unless a formula is essential to the depicted operation.
Use aligned sites and operators, successive operations, clearly distinguished branches and nearby measurable responses.
Place labels next to the object or operation they explain.
Give every arrow a defined physical or mathematical meaning, with the defining action beside its transition when needed.
Show two or three iterations of an iterative construction so continuation is apparent.
Include coefficients, signs and local operations; identify selected branches and omitted terms explicitly.
Never present an illustrative sequence as a complete expansion.
Keep the same physical system across comparisons when possible; use a one-dimensional example when it explains the operation more clearly.
Give preliminary numerical results substantial figures and connect them to the physical question or demonstrated capability.
Use actual numerical data or existing benchmark figures with references, parameters and comparison conditions.

Use deliberate alignment and compact spacing.
Remove unused space between rows and annotations while preserving mathematical indices and distinct operations.
Keep strokes, alignment guides, fermionic-site outlines, arrows and arrowheads visible at final page size.
Treat marker area and outline thickness separately.
For colored lattice markers, use the supplied v7 balance: larger identifiable markers, visible guides and arrows, compact vertical spacing, and mathematical actions beside transitions.
Place panel labels at the left as `a)`, `b)`, etc.; center short panel headings and essential introductory equations.
Legends are explanatory keys, never labeled panels.
If a legend needs separation, use a compact rounded rectangular box.
Keep standalone descriptive headings and extended prose in the companion rather than inside the figure.

## Typography and export

Use `nima_semantica.figure_tools.global_plot_defs()` for Matplotlib: actual LaTeX (`text.usetex=True`), serif Computer Modern, default font/ticks 14 pt, axes titles/labels 15 pt, legends 15 pt and figure title 15 pt.
Choose explicit sizes at the final physical dimensions; never shrink a finished composition to make it fit.
Use 14 pt as the default minimum for primary labels, and inspect mathematical indices at page scale.
Put hats on physical Hamiltonians, creation/annihilation operators, densities and identities, distinguishing them from coefficients and state labels.
Apply each hat only to the symbol letter: `$\hat{c}^{\dagger}_{i}$`, `$\hat{H}_{t}$`.
Check the rendered accents, subscripts, superscripts and daggers, not only their source syntax.

Flat diagrams and plots remain vector.
When depth explains material geometry or microscopic motion, use Blender geometry with vector text, arrows and plot overlays.
SVG/PDF exports containing shaded Blender objects are hybrid: they embed a high-resolution render while keeping the overlays vector.
Do not describe a raster-only PDF as a vector figure.
Save the `.blend`, editable SVG composition, PDF, PNG preview, reproducible source and numerical data, and tool/version provenance.
Preserve previously reviewed candidates and substantial alternatives under versioned filenames.

Each figure has a companion Markdown file explaining notation, color meanings, arrow actions, physical interpretation, assumptions, omitted contributions, parameters, references and a proposed caption.
Perform numerical/source checks and visual inspection at final paper size before using the figure in a report.

## Blender physical objects

Represent electrons as three-dimensional yellow spheres.
When spin matters, pass a three-dimensional spin arrow through each sphere's center.
Flat site markers instead serve lattice alignment and operator-construction diagrams.
Depict interactions with recognizable wavy connectors, using modest tubular depth when useful.
Distinguish interactions from straight bonds, hopping arrows and process arrows and explain each connector in the companion.
Use the spheres, central spin arrows, connectors and restrained depth of the user-supplied NQS review conclusion's frustrated-magnetism panel as the visual reference; its exact composition and colors are not universal requirements.

For NQS, SCOL and operator constructions, load [domain conventions](domain-illustrations.md) only when applicable.
