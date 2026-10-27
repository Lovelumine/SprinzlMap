#!/usr/bin/env python3
"""Generate Figure 3: Agent-guided recovery of a local coordinate conflict."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import patches


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import fig03_agent_guided_recovery_core as figure  # noqa: E402


OUT_DIR = ROOT / "paper/figures"
OUT_PNG = OUT_DIR / "fig03_agent_guided_recovery.png"
OUT_PDF = OUT_DIR / "fig03_agent_guided_recovery.pdf"
TEMPLATE_TSV = ROOT / "data/tRNAs_flat.tsv"

ACCENT = "#287A78"
ACCENT_FACE = "#E5F2F0"
DIFFERENCE = "#C77916"
DIFFERENCE_FACE = "#FBE8C4"
SEPARATOR = "#D0D5DD"
PAIR_CANONICAL = "#5F6F83"
PAIR_WOBBLE = "#4D78A8"
UNPAIRED = "#B66A10"
OBSERVED_T_PAIRS = [("49", "65"), ("50", "64")]
UNPAIRED_T_SLOTS = [("51", "63"), ("52", "62"), ("53", "61")]


def panel_letter(ax: plt.Axes, letter: str) -> None:
    ax.text(
        0.0,
        1.025,
        letter,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8.5,
        weight="bold",
        color="#111111",
    )


def load_template_row(template_name: str) -> dict[str, str]:
    with TEMPLATE_TSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("seqname") == template_name:
                return row
    raise KeyError(f"Template not found: {template_name}")


def draw_stage_arrow(ax: plt.Axes, x0: float, x1: float, y: float) -> None:
    ax.annotate(
        "",
        xy=(x1, y),
        xytext=(x0, y),
        xycoords=ax.transAxes,
        arrowprops={
            "arrowstyle": "-|>",
            "color": "#8793A3",
            "lw": 1.0,
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )


def draw_conflict_observation(ax: plt.Axes) -> None:
    ax.text(
        0.02,
        0.79,
        r"Target · $\it{P.\ marinus}$ tRNA$^{\mathrm{Arg(CCU)}}$",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.5,
        color="#344054",
    )

    canonical_x = [0.035, 0.069, 0.103]
    insertion_x = [0.143, 0.177, 0.211]
    y_node = 0.46
    cell_width = 0.029
    cell_height = 0.115
    for x, label in zip(canonical_x, ("51", "52", "53")):
        ax.text(
            x,
            0.59,
            label,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=5.1,
            color=figure.MUTED,
        )
        ax.add_patch(
            patches.Rectangle(
                (x - cell_width / 2, y_node - cell_height / 2),
                cell_width,
                cell_height,
                transform=ax.transAxes,
                facecolor="#FBFCFD",
                edgecolor=figure.ERROR,
                linewidth=1.0,
            )
        )
        ax.text(
            x,
            y_node,
            "–",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=5.4,
            color=figure.ERROR,
        )

    ax.plot(
        [0.123, 0.123],
        [0.385, 0.62],
        transform=ax.transAxes,
        color=SEPARATOR,
        linewidth=1.0,
    )
    ax.text(
        sum(insertion_x) / len(insertion_x),
        0.59,
        "53i1–53i3",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=5.1,
        color=figure.ERROR,
        weight="bold",
    )
    for x in insertion_x:
        ax.add_patch(
            patches.Rectangle(
                (x - cell_width / 2, y_node - cell_height / 2),
                cell_width,
                cell_height,
                transform=ax.transAxes,
                facecolor=figure.ERROR_FACE,
                edgecolor=figure.ERROR,
                linewidth=1.0,
            )
        )
        ax.text(
            x,
            y_node,
            "C",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=5.0,
            color=figure.ERROR,
            weight="bold",
        )

def draw_agent_action(ax: plt.Axes) -> None:
    draw_stage_arrow(ax, 0.232, 0.355, 0.48)
    ax.text(
        0.294,
        0.62,
        "Agent-selected realignment",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=5.1,
        color=ACCENT,
        weight="bold",
    )


def draw_reference_alignment(
    ax: plt.Axes,
    template_row: dict[str, str],
    target_by_label: dict[str, str],
) -> None:
    ax.text(
        0.370,
        0.79,
        r"Reference · $\it{P.\ marinus}$ CCMP1375 tRNA$^{\mathrm{Arg(CCU)}}$",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.5,
        color="#344054",
    )
    ax.text(
        0.985,
        0.79,
        "69/74 nt identical",
        transform=ax.transAxes,
        ha="right",
        va="center",
        fontsize=5.2,
        color=figure.MUTED,
    )

    labels = [str(value) for value in range(49, 66)]
    reference = [figure.normalize_base(template_row.get(label, "-")) for label in labels]
    target = [figure.normalize_base(target_by_label.get(label, "-")) for label in labels]
    mismatches = {i for i, (ref, query) in enumerate(zip(reference, target)) if ref != query}
    if mismatches != {2, 3, 4, 9, 10}:
        raise AssertionError(f"Unexpected template differences: {mismatches}")

    x_start = 0.455
    x_end = 0.985
    cell_width = (x_end - x_start) / len(labels)
    row_y = {"Sprinzl": 0.58, "Reference": 0.43, "Target": 0.28}
    for row_name, y in row_y.items():
        ax.text(
            0.440,
            y,
            row_name,
            transform=ax.transAxes,
            ha="right",
            va="center",
            fontsize=5.1,
            color=figure.MUTED if row_name == "Sprinzl" else figure.INK,
        )

    for index, label in enumerate(labels):
        x = x_start + index * cell_width
        ax.add_patch(
            patches.Rectangle(
                (x, row_y["Sprinzl"] - 0.047),
                cell_width * 0.94,
                0.094,
                transform=ax.transAxes,
                facecolor="#E8F3EE",
                edgecolor="white",
                linewidth=1.0,
            )
        )
        ax.text(
            x + cell_width * 0.47,
            row_y["Sprinzl"],
            label,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=5.0,
            color="#475467",
            weight="bold" if label in {"51", "52", "53"} else "normal",
        )
        for row_name, bases in (("Reference", reference), ("Target", target)):
            is_difference = index in mismatches
            face = DIFFERENCE_FACE if is_difference else "#F5F7F9"
            edge = DIFFERENCE if is_difference else "white"
            ax.add_patch(
                patches.Rectangle(
                    (x, row_y[row_name] - 0.052),
                    cell_width * 0.94,
                    0.104,
                    transform=ax.transAxes,
                    facecolor=face,
                    edgecolor=edge,
                    linewidth=1.0,
                )
            )
            ax.text(
                x + cell_width * 0.47,
                row_y[row_name],
                bases[index],
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=5.1,
                color=DIFFERENCE if is_difference else figure.INK,
                weight="bold" if is_difference else "normal",
            )

    bracket_x0 = x_start + 2 * cell_width
    bracket_x1 = x_start + 5 * cell_width - cell_width * 0.06
    ax.plot(
        [bracket_x0, bracket_x0, bracket_x1, bracket_x1],
        [0.16, 0.13, 0.13, 0.16],
        transform=ax.transAxes,
        color=ACCENT,
        linewidth=1.0,
    )
    ax.text(
        (bracket_x0 + bracket_x1) / 2,
        0.075,
        "51–53 correspondence",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=5.2,
        color=ACCENT,
        weight="bold",
    )


def draw_retrieval_panel(
    ax: plt.Axes,
    cm_case: dict,
    agent_case: dict,
    gold_rows: list[dict],
) -> None:
    del cm_case
    ax.axis("off")
    panel_letter(ax, "a")
    template_name = str(agent_case.get("template_name") or "")
    if template_name != "procMari_MARINUS_CCMP137_chr.trna22-ArgCCT":
        raise AssertionError(f"Unexpected selected template: {template_name}")
    template_row = load_template_row(template_name)
    target_by_label = {
        str(row["label"]): str(row["base"])
        for row in gold_rows
    }
    draw_conflict_observation(ax)
    draw_agent_action(ax)
    draw_reference_alignment(ax, template_row, target_by_label)


def draw_t_state(
    ax: plt.Axes,
    *,
    case: dict,
    area: tuple[float, float, float, float],
    title: str,
    positions: dict[str, tuple[float, float]],
    before: bool,
) -> None:
    bases = {
        str(label): figure.normalize_base(base)
        for label, base in zip(case["labels"], case["target"])
    }
    actual_path = [
        str(row["label"])
        for row in figure.residue_rows(case)
        if figure.is_local_t_label(str(row["label"]))
    ]
    insertions = (
        [label for label in actual_path if figure.INSERTION_RE.fullmatch(label)]
        if before
        else []
    )
    points = figure.local_t_positions(ax, positions, area, insertions)

    for left, right in zip(figure.T_LABELS, figure.T_LABELS[1:]):
        x1, y1 = points[left]
        x2, y2 = points[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            transform=ax.transAxes,
            color="#E3E8EF",
            linewidth=1.0,
            linestyle=(0, (2.0, 2.0)),
            zorder=0,
        )
    for left, right in zip(actual_path, actual_path[1:]):
        x1, y1 = points[left]
        x2, y2 = points[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            transform=ax.transAxes,
            color="#B8C3D1",
            linewidth=1.05,
            zorder=1,
        )
    for pair_index, (left, right) in enumerate(OBSERVED_T_PAIRS):
        x1, y1 = points[left]
        x2, y2 = points[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            transform=ax.transAxes,
            color=PAIR_CANONICAL if pair_index == 0 else PAIR_WOBBLE,
            linewidth=1.1,
            linestyle="-" if pair_index == 0 else (0, (2.0, 1.8)),
            zorder=1,
        )

    if not before:
        for left, right in UNPAIRED_T_SLOTS:
            x1, y1 = points[left]
            x2, y2 = points[right]
            dx = x2 - x1
            dy = y2 - y1
            ax.plot(
                [x1, x1 + 0.36 * dx],
                [y1, y1 + 0.36 * dy],
                transform=ax.transAxes,
                color=UNPAIRED,
                linewidth=1.0,
                zorder=1,
            )
            ax.plot(
                [x1 + 0.64 * dx, x2],
                [y1 + 0.64 * dy, y2],
                transform=ax.transAxes,
                color=UNPAIRED,
                linewidth=1.0,
                zorder=1,
            )
            ax.text(
                x1 + 0.50 * dx,
                y1 + 0.50 * dy,
                "×",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=5.0,
                color=UNPAIRED,
                weight="bold",
                zorder=3,
            )

    affected = (
        {*figure.CORRECTED_T_LABELS, *insertions}
        if before
        else set(figure.CORRECTED_T_LABELS)
    )
    state_color = figure.ERROR if before else ACCENT
    for label in list(figure.T_LABELS) + insertions:
        x, y = points[label]
        base = bases.get(label, "-")
        ax.scatter(
            [x],
            [y],
            s=142,
            transform=ax.transAxes,
            facecolor=figure.original.BASE_COLORS.get(base, "#FBFCFD"),
            edgecolor=state_color if label in affected else "#667085",
            linewidth=1.05 if label in affected else 1.0,
            zorder=4,
            clip_on=False,
        )
        ax.text(
            x,
            y,
            base,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=6.2,
            color=figure.INK if base != "-" else figure.MUTED,
            zorder=5,
        )
        ax.text(
            x,
            y - 0.039,
            label,
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=5.0,
            color=state_color if label in affected else figure.MUTED,
            weight="bold" if label in affected else "normal",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.84, "pad": 0.08},
            zorder=6,
        )

    x0, y0, _, height = area
    ax.text(
        x0 - 0.055,
        y0 + height / 2,
        title,
        transform=ax.transAxes,
        ha="right",
        va="center",
        fontsize=5.7,
        color=figure.INK,
    )


def draw_local_correction(
    ax: plt.Axes,
    cm_case: dict,
    agent_case: dict,
    positions: dict[str, tuple[float, float]],
) -> None:
    ax.axis("off")
    panel_letter(ax, "b")
    ax.plot(
        [0.18, 0.22],
        [0.93, 0.93],
        transform=ax.transAxes,
        color=PAIR_CANONICAL,
        linewidth=1.1,
    )
    ax.text(
        0.235,
        0.93,
        "G–C",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.0,
        color=figure.MUTED,
    )
    ax.plot(
        [0.35, 0.39],
        [0.93, 0.93],
        transform=ax.transAxes,
        color=PAIR_WOBBLE,
        linewidth=1.1,
        linestyle=(0, (2.0, 1.8)),
    )
    ax.text(
        0.405,
        0.93,
        "U–G",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.0,
        color=figure.MUTED,
    )
    ax.text(
        0.55,
        0.93,
        "×  C/C ×3 unpaired",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.0,
        color=UNPAIRED,
    )
    draw_t_state(
        ax,
        case=cm_case,
        area=(0.14, 0.62, 0.70, 0.23),
        title="Initial projection",
        positions=positions,
        before=True,
    )
    ax.annotate(
        "",
        xy=(0.50, 0.405),
        xytext=(0.50, 0.525),
        xycoords=ax.transAxes,
        arrowprops={"arrowstyle": "-|>", "color": "#5F6F83", "lw": 1.0},
    )
    ax.text(
        0.525,
        0.458,
        "Template-guided realignment",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.2,
        color="#344054",
        weight="bold",
    )
    draw_t_state(
        ax,
        case=agent_case,
        area=(0.14, 0.10, 0.70, 0.23),
        title="Validated result",
        positions=positions,
        before=False,
    )


def normalize_structure_panel(ax: plt.Axes) -> None:
    for line in ax.lines:
        line.set_linewidth(max(1.0, float(line.get_linewidth())))
    for patch in ax.patches:
        width = float(patch.get_linewidth())
        if width > 0:
            patch.set_linewidth(max(1.0, width))
    for collection in ax.collections:
        widths = collection.get_linewidths()
        if len(widths):
            collection.set_linewidths([max(1.0, float(width)) for width in widths])
    for label in ax.texts:
        if label.get_text() == "c":
            label.set_fontsize(8.5)
        else:
            label.set_fontsize(max(5.2, float(label.get_fontsize())))


def make_structure_case(agent_case: dict) -> dict:
    display_case = figure.make_structure_case(agent_case)
    display_case["_full_structural_pairs"] = [
        pair
        for pair in display_case["_full_structural_pairs"]
        if pair not in UNPAIRED_T_SLOTS
    ]
    return display_case


def load_figure_inputs() -> tuple[dict, dict, list[dict], dict, dict]:
    cm_case = figure.load_case(figure.CM_CASE_PATH)
    agent_case = figure.rerun_current_reference_candidate(
        figure.load_case(figure.AGENT_CASE_PATH)
    )
    gold_rows = figure.load_gold_rows(figure.SEQNAME)
    figure.validate_cases(cm_case, agent_case, gold_rows)
    canonical_positions = figure.original.load_sprinzl_positions()
    structure_case = make_structure_case(agent_case)
    return cm_case, agent_case, gold_rows, canonical_positions, structure_case


def save_figure(
    cm_case: dict,
    agent_case: dict,
    gold_rows: list[dict],
    canonical_positions: dict,
    structure_case: dict,
) -> None:
    fig = plt.figure(figsize=(7.2, 6.15), constrained_layout=False)
    grid = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.0, 2.70],
        width_ratios=[0.96, 1.42],
        hspace=0.18,
        wspace=0.16,
    )
    ax_a = fig.add_subplot(grid[0, :])
    ax_b = fig.add_subplot(grid[1, 0])
    ax_c = fig.add_subplot(grid[1, 1])
    draw_retrieval_panel(ax_a, cm_case, agent_case, gold_rows)
    draw_local_correction(
        ax_b,
        cm_case,
        agent_case,
        canonical_positions,
    )
    figure.draw_final_structure(
        ax_c,
        structure_case,
        canonical_positions,
        gold_rows,
        cm_case,
    )
    normalize_structure_panel(ax_c)
    fig.subplots_adjust(top=0.97, bottom=0.052, left=0.065, right=0.985)
    fig.savefig(OUT_PNG, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_PDF, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    figure.configure_style()
    plt.rcParams.update(
        {
            "mathtext.fontset": "custom",
            "mathtext.rm": "Arial",
            "mathtext.it": "Arial:italic",
            "mathtext.bf": "Arial:bold",
            "mathtext.default": "regular",
            "font.cursive": ["Arial"],
        }
    )
    figure.PASS = ACCENT
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inputs = load_figure_inputs()
    save_figure(*inputs)
    print(OUT_PNG)
    print(OUT_PDF)


if __name__ == "__main__":
    main()
