# Paper Figure Scripts

Each main-text figure must have an independent generation entry point. Use the single-figure script when editing or checking one figure.

Human curation is maintained in MySQL. Figure 2 uses the saved evaluation reference and predictions documented in [`../results/figure2/README.md`](../results/figure2/README.md).

## Current evaluation

All figures come from runs stored in MySQL and MinIO. The manuscript's single
main run is `20261020-164533-deepseekv4-flash-guangdong` (2,530 cases, sealed).

- Figure 2: `python research/paper/scripts/make_fig02_domain_benchmark.py`.
- Ablation tables: `python benchmark/scripts/summarize_flow_controls.py`
  (paired, case by case) and `python benchmark/scripts/summarize_ablation_ladder.py`
  (one row per rung of the ladder).
- Ablation figure: `python research/paper/scripts/make_fig03_agent_ablation_2530.py`,
  which reads those tables from `../results/`.

## Retained illustration sources

The Figure 1, 3, 4, 5 and 6 drawing scripts remain alongside the original
artwork. Previous benchmark tables and run directories have been removed.
These scripts are retained as editable figure sources; they are not entrypoints
for the current evaluation. In particular, the original Figure 3 rendering or
its template replay does not establish the final output of the current run.

Do not run `make_revised_results.py` or regenerate other manuscript figures
while cleaning the repository. Validate their source data individually when
updating the corresponding result with the user.

## AF3 Research Scripts

### Current manuscript figure composition

- Figure 4: `python research/paper/scripts/compose_cca_position_figures.py --figure cca`.
- Figure 5: `python research/paper/scripts/compose_cca_position_figures.py --figure positions`.
  Panel C uses the experimental PDB 9H0L/7NWW renders retained in
  `../results/fig5c_pilot/`. The source coordinates and class mappings are
  documented in that directory. The dated V19/V14 AF3 rerun in
  `../results/fig5c_af3_20261027/` is retained as a supplementary computational
  check.

These commands require `pdflatex` (with TikZ and standalone) and `pdftoppm`.
They create `fig04_cca_terminal.{pdf,png}` and
`fig05_variable_anticodon.{pdf,png}` without rerunning predictions or scoring.
Figure 4 retains panel A of `fig05_msa_examples.pdf` and the four AF3 terminal
panels, now labelled A--E. Figure 5 retains the original panels B/C, now labelled
A/B. Vector alignment artwork and source structure renders remain unchanged;
only whitespace and panel headings are adjusted in the composed outputs. Figure
4 removes an empty horizontal band between the alignment row labels and bases
and reduces the AF3 tile size to balance the two types of evidence.

### Terminal-panel artwork used in the current Figure 4

Figure 4 takes its four terminal panels from
`results/af3_cca4_20261019/figures_individual_row/`, the landscape reflow of the
delivered `figure_cca/individual_compact` renders. `--style v4` switches the
compose step back to the earlier `figures_abcd/*_v4.png` renders. The reflow is
produced by `render_individual_row.py` (same structures, projection, colours and
shared scale as the source; run inside the AF3 workspace, see its docstring).
Distances are not printed inside the panels; they are reported in the text.

- `prepare_af3_cca4.py`: structure preparation.
- `analyze_cca_stacking.py`: terminal-base geometry statistics.
- `build_cca3_site.py`: data export for the static structure viewer.
- `render_af3_cca4_*.py`, `render_terminal_faceon.py`: structure rendering. The
  superseded PyMOL-era renders (`figures_pymol/`, `figures_abcd_pymol*/`,
  `figures_terminal_pymol/`) were removed from the working tree; the live inputs
  for Figure 4 are `results/af3_cca4_20261019/figures_abcd/*_v4.png`.
- `compose_af3_cca4_*.py`, `draw_terminal_fragments.py`: figure composition.
- `probe_*.py`, `preview_faceon_planes.py`, `calibrate_ortho_scale.py`: view and camera diagnostics.

The materials are indexed in [`../results/af3_cca4_20261019/README.md`](../results/af3_cca4_20261019/README.md) and [`../site_cca3/README.md`](../site_cca3/README.md).
