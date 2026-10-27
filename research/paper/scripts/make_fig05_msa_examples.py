#!/usr/bin/env python3
"""Regenerate Figure 5 and its source table only."""

from __future__ import annotations

from make_revised_results import (
    FIGURES_DIR,
    RESULTS_DIR,
    configure_style,
    draw_msa_examples_figure,
    load_export_meta,
    load_gold,
)


def main() -> None:
    configure_style()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    columns, gold_rows = load_gold()
    export_meta = load_export_meta()
    draw_msa_examples_figure(columns, gold_rows, export_meta)

    print("wrote=paper/figures/fig05_msa_examples.pdf")


if __name__ == "__main__":
    main()
