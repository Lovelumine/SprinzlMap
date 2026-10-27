#!/usr/bin/env python3
"""Ablation figure for the 2,530-sequence manuscript cohort.

Every number comes from stored runs in MySQL and MinIO, exported by
``benchmark/scripts/summarize_ablation_ladder.py`` (one row per rung of the
ladder) and ``benchmark/scripts/summarize_flow_controls.py`` (the paired,
case-by-case contrast).  The figure script reads those tables only, so it can be
re-run on any machine that can reach the store.

Panels
------
A  how far each rung of the ladder gets, and what it costs in wall-clock
B  where the language-model planner changes coordinates, on the cloverleaf
C  which sequences move: per-sequence accuracy change against the deterministic
   control (most sequences are untouched; the planner repairs a minority)
D  accuracy without verification: the model-only control against the guarded
   rungs, in the accuracy / rule-pass plane
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from make_revised_results import (  # noqa: E402
    FIGURES_DIR,
    RESULTS_DIR,
    configure_style,
    panel_label,
)

LADDER_TSV = RESULTS_DIR / "ablation_ladder_2530.tsv"
RAW_LLM_JSON = RESULTS_DIR / "ablation_ladder_2530_raw_llm.json"
SUMMARY_TSV = RESULTS_DIR / "agent_ablation_2530_summary.tsv"
POSITION_TSV = RESULTS_DIR / "agent_ablation_2530_position_delta.tsv"
CASE_TSV = RESULTS_DIR / "agent_ablation_2530_case_changes.tsv"

# Panel A shows the ladder from the weakest rung to the system as reported in
# the manuscript; the paired contrast in B and C is planner versus the
# deterministic control with the same structural evidence.
LADDER_ORDER = ["cm", "harness-cm-root", "harness-retrieval-dp", "fixed-planner", "agent-planner"]
RUNG_LABELS = {
    "cm": "Covariance-model alignment",
    "harness-cm-root": "+ rule check, no planner",
    "harness-retrieval-dp": "+ rule check, no CM evidence",
    "fixed-planner": "+ deterministic planner",
    "agent-planner": "+ language-model planner",
}

BASELINE_COLOR = "#9AA1A8"
AGENT_COLOR = "#2E6DA4"
RESIDUE_COLOR = "#2E6DA4"
EXACT_COLOR = "#B4453A"
DELTA_POS = "#2E6DA4"
DELTA_NEG = "#B4453A"
DELTA_MID = "#F4F1EC"
TEXT_DARK = "#1D2126"
TEXT_MUTED = "#616A73"

_NUMERIC = {
    "residue_label_accuracy", "exact_match_rate", "d_accuracy", "variable_accuracy",
    "anticodon_accuracy", "n73_accuracy", "cca_tail_accuracy", "legality_pass_rate",
    "total", "full_correct", "no_agent_correct", "full_accuracy", "no_agent_accuracy",
    "delta_accuracy", "label_total", "full_label_accuracy", "no_agent_label_accuracy",
    "delta_label_accuracy",
}


def load_table(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = list(
        csv.DictReader(path.open(newline="", encoding="utf-8"), delimiter="\t")
    )
    for row in rows:
        for key in list(row):
            if key in _NUMERIC and row[key] not in ("", None):
                try:
                    row[key] = float(row[key])
                except ValueError:
                    pass
    return rows


def pct(value: Any) -> float:
    return 100.0 * float(value)


def draw_ladder(ax: Any, ladder: list[dict[str, Any]]) -> None:
    """Rung-by-rung progress: structural evidence, then the rule check, then the planner."""
    panel_label(ax, "A")
    scored = [
        row for row in ladder
        if str(row.get("residue_accuracy") or "").replace(".", "").isdigit()
        and str(row.get("exact_sequence_accuracy") or "").replace(".", "").isdigit()
    ]
    rungs = [r for rung in LADDER_ORDER if (r := next((x for x in scored if x["rung"] == rung), None))]
    if not rungs:
        raise ValueError("no complete rungs in the ladder table yet")
    y_positions = np.arange(len(rungs))[::-1]

    for y, row in zip(y_positions, rungs):
        residue = pct(row["residue_accuracy"])
        exact = pct(row["exact_sequence_accuracy"])
        ax.plot([exact, residue], [y, y], color="#D5DAE0", linewidth=1.1, zorder=1)
        ax.scatter([residue], [y], s=26, marker="o", color=RESIDUE_COLOR,
                   edgecolor="white", linewidth=0.5, zorder=3)
        ax.scatter([exact], [y], s=26, marker="s", color=EXACT_COLOR,
                   edgecolor="white", linewidth=0.5, zorder=3)
        ax.text(residue + 1.4, y + 0.13, f"{residue:.2f}", fontsize=5.4, ha="left",
                va="center", color=RESIDUE_COLOR)
        exact_ha, exact_dx = ("right", -1.4) if exact > 25 else ("left", 1.9)
        ax.text(exact + exact_dx, y + 0.13, f"{exact:.2f}", fontsize=5.4, ha=exact_ha,
                va="center", color=EXACT_COLOR)
        seconds = row.get("median_elapsed_sec") or ""
        calls = int(float(row.get("planner_llm_calls") or 0))
        cost = f"{float(seconds):.1f} s" if seconds else "-"
        ax.text(residue + 1.4, y - 0.22, f"{cost}" + (f" · {calls:,} calls" if calls else ""),
                fontsize=4.9, ha="left", va="center", color=TEXT_MUTED)

    ax.set_xlim(0, 108)
    ax.set_ylim(-0.62, len(rungs) - 0.38)
    ax.set_yticks(y_positions)
    ax.set_yticklabels([RUNG_LABELS[r["rung"]] for r in rungs], fontsize=5.7)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Accuracy (%)", fontsize=6.1, labelpad=3)
    ax.grid(axis="x", color="#E7EAED", linewidth=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=5.6, pad=1.6)
    ax.scatter([], [], s=26, marker="o", color=RESIDUE_COLOR, label="per residue")
    ax.scatter([], [], s=26, marker="s", color=EXACT_COLOR, label="whole sequence")
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=2, fontsize=5.4,
              handletextpad=0.3, columnspacing=1.0, borderaxespad=0, frameon=False,
              markerscale=0.9)


def draw_accuracy_without_verification(ax: Any, ladder: list[dict[str, Any]],
                                      raw: dict[str, Any]) -> None:
    """Accuracy is not the same statement as a legal coordinate table.

    The model-only control reaches a high per-residue number on the sequences it
    answers, yet its label sets almost never satisfy the hard coordinate rules
    that every guarded rung is required to pass.
    """
    panel_label(ax, "D")
    guarded = next((r for r in ladder if r["rung"] == "agent-planner"), None)
    if guarded is None or not raw.get("usable"):
        raise ValueError("panel D needs the main run and the model-only control")

    metrics = [
        ("residue", 100.0 * float(raw["residue_accuracy_usable"]),
         100.0 * float(guarded["residue_accuracy"])),
        ("sequence", 100.0 * float(raw["exact_rate_usable"] or 0.0),
         100.0 * float(guarded["exact_sequence_accuracy"])),
        ("rules", 100.0 * float(raw["legality_rate_usable"] or 0.0),
         100.0 * float(guarded["legality_pass_rate"])),
    ]
    unchecked = "#C0713C"
    width = 0.34
    for index, (label, model_only, pipeline) in enumerate(metrics):
        x = index * 1.0
        ax.bar(x - width / 2, model_only, width=width, color=unchecked, edgecolor="none", zorder=2)
        ax.bar(x + width / 2, pipeline, width=width, color=AGENT_COLOR, edgecolor="none", zorder=2)
        ax.text(x - width / 2, model_only + 2.0, f"{model_only:.1f}", ha="center", va="bottom",
                fontsize=5.2, color=unchecked)
        ax.text(x + width / 2, pipeline + 2.0, f"{pipeline:.1f}", ha="center", va="bottom",
                fontsize=5.2, color=AGENT_COLOR)
        ax.text(x, -5.0, label, ha="center", va="top", fontsize=5.4, color=TEXT_DARK)

    ax.set_xlim(-0.62, len(metrics) - 0.38)
    ax.set_ylim(0, 112)
    ax.set_xticks([])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Accuracy / rules passed (%)", fontsize=6.0, labelpad=3)
    ax.text(0.5, -14.0, "residue = per-residue accuracy   sequence = whole-sequence exact"
            "   rules = hard coordinate rules",
            transform=ax.transAxes, ha="center", va="top", fontsize=4.8, color=TEXT_MUTED)
    ax.grid(axis="y", color="#E7EAED", linewidth=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(labelsize=5.6, pad=1.6)
    ax.bar(0, 0, color=unchecked, label=f"model only (n={int(raw['usable']):,})")
    ax.bar(0, 0, color=AGENT_COLOR, label="SprinzlMap")
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.005), ncol=2, fontsize=5.4,
              handletextpad=0.3, columnspacing=1.0, borderaxespad=0, frameon=False)


def draw_position_delta(ax: Any, position_rows: list[dict[str, Any]]) -> None:
    """Where the planner moves coordinates, position by position.

    The cloverleaf map of Figure 2 shows absolute accuracy; this panel shows the
    paired change against the deterministic control, restricted to positions with
    enough reference residues for a percentage to mean anything.  Positions that
    carry only a handful of insertion columns are listed by hand instead, because
    their relative change looks large but rests on single-digit counts.
    """
    panel_label(ax, "B")
    considered = [
        row for row in position_rows
        if int(row.get("total") or 0) >= 50 and abs(float(row["delta_accuracy"])) > 1e-4
    ]
    considered.sort(key=lambda row: abs(float(row["delta_accuracy"])), reverse=True)
    shown = considered[:12][::-1]
    if not shown:
        raise ValueError("no position carries a resolvable change")

    x_lo = -11.0
    y_positions = np.arange(len(shown))
    for y, row in zip(y_positions, shown):
        delta = 100.0 * float(row["delta_accuracy"])
        color = DELTA_POS if delta > 0 else DELTA_NEG
        ax.plot([0, delta], [y, y], color="#D5DAE0", linewidth=0.9, zorder=1)
        ax.scatter([delta], [y], s=30, color=color, edgecolor="white", linewidth=0.5, zorder=3)
        ax.text(delta + (1.1 if delta > 0 else -1.1), y, f"{delta:+.2f}", fontsize=5.3,
                ha="left" if delta > 0 else "right", va="center", color=color)
        ax.text(-1.5, y, str(row["position"]), fontsize=5.6, ha="right", va="center",
                color=TEXT_DARK)
        ax.text(x_lo - 0.5, y, f"{int(row['total']):,}", fontsize=4.8, ha="right",
                va="center", color=TEXT_MUTED)

    ax.axvline(0, color="#8A939D", linewidth=0.6, zorder=2)
    ax.set_xlim(x_lo, 30)
    ax.set_ylim(-0.7, len(shown) - 0.3)
    ax.set_yticks([])
    ax.set_xticks([-10, 0, 10, 20, 30])
    ax.set_xlabel("Δ per-residue accuracy (pp)", fontsize=6.1, labelpad=3)
    ax.grid(axis="x", color="#E7EAED", linewidth=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=5.6, pad=1.6)
    ax.text(x_lo, len(shown) - 0.15, "position      n", fontsize=5.0, ha="left",
            va="bottom", color=TEXT_MUTED)


def draw_accuracy_without_verification(ax: Any, ladder: list[dict[str, Any]],
                                      raw: dict[str, Any]) -> None:
    """Accuracy is not the same statement as a legal coordinate table.

    The model-only control reaches a high per-residue number on the sequences it
    answers, yet its label sets almost never satisfy the hard coordinate rules
    that every guarded rung is required to pass.
    """
    panel_label(ax, "D")
    guarded = next((r for r in ladder if r["rung"] == "agent-planner"), None)
    if guarded is None or not raw.get("usable"):
        raise ValueError("panel D needs the main run and the model-only control")

    metrics = [
        ("per-residue\naccuracy", 100.0 * float(raw["residue_accuracy_usable"]),
         100.0 * float(guarded["residue_accuracy"])),
        ("whole-sequence\nexact", 100.0 * float(raw["exact_rate_usable"] or 0.0),
         100.0 * float(guarded["exact_sequence_accuracy"])),
        ("hard coordinate\nrules passed", 100.0 * float(raw["legality_rate_usable"] or 0.0),
         100.0 * float(guarded["legality_pass_rate"])),
    ]
    unchecked = "#C0713C"
    width = 0.34
    for index, (label, model_only, pipeline) in enumerate(metrics):
        x = index * 1.0
        ax.bar(x - width / 2, model_only, width=width, color=unchecked, edgecolor="none", zorder=2)
        ax.bar(x + width / 2, pipeline, width=width, color=AGENT_COLOR, edgecolor="none", zorder=2)
        ax.text(x - width / 2, model_only + 2.0, f"{model_only:.1f}", ha="center", va="bottom",
                fontsize=5.2, color=unchecked)
        ax.text(x + width / 2, pipeline + 2.0, f"{pipeline:.1f}", ha="center", va="bottom",
                fontsize=5.2, color=AGENT_COLOR)
        ax.text(x, -6.0, label, ha="center", va="top", fontsize=5.3, color=TEXT_DARK,
                linespacing=1.35)

    ax.set_xlim(-0.62, len(metrics) - 0.38)
    ax.set_ylim(0, 112)
    ax.set_xticks([])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Rate (%)", fontsize=6.1, labelpad=3)
    ax.grid(axis="y", color="#E7EAED", linewidth=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(labelsize=5.6, pad=1.6)
    ax.bar(0, 0, color=unchecked, label=f"model only (n={int(raw['usable']):,})")
    ax.bar(0, 0, color=AGENT_COLOR, label="SprinzlMap")
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.005), ncol=2, fontsize=5.4,
              handletextpad=0.3, columnspacing=1.0, borderaxespad=0, frameon=False)


def draw_cloverleaf(ax: Any, position_rows: list[dict[str, Any]]) -> None:
    """Per-position delta on the cloverleaf; dot area tracks reference coverage."""
    panel_label(ax, "B")
    positions = load_sprinzl_positions()
    rows = {str(r["position"]): r for r in position_rows}
    cmap = LinearSegmentedColormap.from_list(
        "agent_delta", [DELTA_NEG, DELTA_MID, DELTA_POS]
    )
    # Weight by reference coverage so a handful of one-residue insertion columns
    # cannot dominate the scale; fall back to the plain max when weights are absent.
    weighted = [
        (abs(100 * float(r["delta_accuracy"])), max(1, int(r.get("total") or 1)))
        for r in position_rows if r.get("delta_accuracy") not in ("", None)
    ]
    weighted.sort()
    target = 0.90 * sum(w for _v, w in weighted)
    running = 0
    p90 = weighted[-1][0] if weighted else 2.0
    for value, weight in weighted:
        running += weight
        if running >= target:
            p90 = value
            break
    max_abs = float(min(max(p90, 2.0), 8.0))
    norm = TwoSlopeNorm(vcenter=0.0, vmin=-max_abs, vmax=max_abs)
    ax.set_aspect("equal")
    ax.axis("off")

    for left, right in PAIR_LINKS:
        if left in positions and right in positions:
            (x1, y1), (x2, y2) = positions[left], positions[right]
            ax.plot([x1, x2], [y1, y2], color="#D7DDE4", linewidth=0.70,
                    linestyle=(0, (2, 2)), zorder=0)

    visible = [label for label in SPRINZL_POSITIONS if label in positions]
    for left, right in zip(visible, visible[1:]):
        (x1, y1), (x2, y2) = positions[left], positions[right]
        ax.plot([x1, x2], [y1, y2], color="#E4E9EF", linewidth=0.50, zorder=0)

    totals = [int(rows.get(label, {}).get("total") or 0) for label in visible]
    peak = max(totals) if totals else 1

    for label in visible:
        x, y = positions[label]
        row = rows.get(label, {})
        total = int(row.get("total") or 0)
        delta = row.get("delta_accuracy")
        if total == 0 or delta in ("", None):
            face, edge = "#EFF3F7", "#D7DDE4"
        else:
            face, edge = cmap(norm(100 * float(delta))), "#FFFFFF"
        # sqrt keeps small-coverage positions visible while the busy sites read heavier
        radius = 4.6 + 3.6 * float(np.sqrt(total / peak)) if total else 3.4
        ax.add_patch(patches.Circle((x, y), radius=radius, facecolor=face,
                                    edgecolor=edge, linewidth=0.55, zorder=2))

    for label, (dx, dy) in POSITION_LABEL_OFFSETS.items():
        if label in positions:
            x, y = positions[label]
            ax.text(x + dx, y + dy, label, fontsize=6.4, ha="center", va="center",
                    color="#4A5560", fontfamily="Arial")

    tail = [positions[l] for l in ("74", "75", "76") if l in positions]
    if tail:
        xs = [p[0] for p in tail]
        y_bar = min(p[1] for p in tail) - 26
        ax.plot([min(xs) - 6, max(xs) + 6], [y_bar, y_bar], color="#8A939D", linewidth=0.7)
        ax.plot([min(xs) - 6] * 2, [y_bar, y_bar + 7], color="#8A939D", linewidth=0.7)
        ax.plot([max(xs) + 6] * 2, [y_bar, y_bar + 7], color="#8A939D", linewidth=0.7)
        ax.text((min(xs) + max(xs)) / 2, y_bar - 13, "74–76 CCA", fontsize=6.0,
                ha="center", va="center", color="#4A5560")

    # Explicit limits so the legend strip below cannot stretch the panel.
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    x_lo, x_hi = min(xs) - 30, max(xs) + 66
    y_lo, y_hi = min(ys) - 52, max(ys) + 128
    ax.set_xlim(x_lo, x_hi)
    # inverted like the source layout: the 3' CCA tail sits at the top, anticodon below
    ax.set_ylim(y_hi, y_lo)

    # compact horizontal colour bar drawn as discrete swatches (imshow would
    # silently widen the axes limits and blow up the saved bbox)
    bar_w, bar_h = (x_hi - x_lo) * 0.40, 11.0
    bar_x = x_lo + (x_hi - x_lo) * 0.07
    bar_y = y_hi - 46.0
    steps = 48
    for step in range(steps):
        frac = step / (steps - 1)
        value = max_abs - 2 * max_abs * frac
        ax.add_patch(
            patches.Rectangle(
                (bar_x + bar_w * step / steps, bar_y),
                bar_w / steps + 0.35,
                bar_h,
                facecolor=cmap(norm(value)),
                edgecolor="none",
                linewidth=0,
                zorder=3,
            )
        )
    ax.add_patch(patches.Rectangle((bar_x, bar_y), bar_w, bar_h, fill=False,
                                   edgecolor="#C6CDD5", linewidth=0.5, zorder=4))
    ax.text(bar_x - 6, bar_y + bar_h / 2, f"−{max_abs:.0f}", fontsize=5.3,
            ha="right", va="center", color=TEXT_MUTED)
    ax.text(bar_x + bar_w + 6, bar_y + bar_h / 2, f"+{max_abs:.0f}", fontsize=5.3,
            ha="left", va="center", color=TEXT_MUTED)
    # y grows downward here (inverted axis), so anchor the caption by its top edge
    ax.text(bar_x + bar_w / 2, bar_y + bar_h + 6, "Δ accuracy (pp)", fontsize=5.5,
            ha="center", va="top", color=TEXT_MUTED)



def draw_delta_distribution(ax: Any, case_rows: list[dict[str, Any]]) -> None:
    """Per-sequence accuracy change: most stay put, a hard minority is repaired."""
    panel_label(ax, "C")
    deltas = np.array([100 * float(r["delta_label_accuracy"]) for r in case_rows])
    bins = np.arange(-102.5, 105.0, 5.0)  # bin centred on 0 so "unchanged" reads grey
    counts, edges = np.histogram(deltas, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    colors = [DELTA_NEG if c < 0 else (AGENT_COLOR if c > 0 else BASELINE_COLOR)
              for c in centers]
    ax.bar(centers, counts, width=4.4, color=colors, edgecolor="white", linewidth=0.3)

    unchanged = int(np.sum(np.abs(deltas) < 0.001))
    improved = int(np.sum(deltas > 0.001))
    worsened = int(np.sum(deltas < -0.001))
    ax.set_yscale("log")
    ax.set_ylim(0.7, max(counts) * 3.2)
    ax.set_xlim(-105, 105)
    ax.set_xticks([-100, -50, 0, 50, 100])
    ax.set_yticks([1, 10, 100, 1000])
    ax.set_yticklabels(["1", "10", "100", "1 000"])
    ax.set_xlabel("Per-sequence Δ accuracy (pp)", fontsize=6.1, labelpad=3)
    ax.set_ylabel("Sequences", fontsize=6.1, labelpad=3)
    ax.grid(axis="y", color="#E7EAED", linewidth=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=5.6, pad=1.6)

    ax.annotate(
        f"{unchanged:,} unchanged",
        xy=(0, unchanged), xytext=(-96, unchanged * 0.55),
        fontsize=5.4, color=TEXT_MUTED, ha="left", va="center",
        arrowprops={"arrowstyle": "-", "color": "#9AA1A8", "lw": 0.5},
    )
    ax.text(0.98, 0.60, f"improved  {improved:,}\nregressed  {worsened:,}",
            transform=ax.transAxes, ha="right", va="top", fontsize=5.5,
            color=TEXT_DARK, linespacing=1.5)




def main() -> None:
    for path in (LADDER_TSV, POSITION_TSV, CASE_TSV, RAW_LLM_JSON):
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} is missing; export the ladder and paired tables first"
            )

    ladder = load_table(LADDER_TSV)
    position_rows = load_table(POSITION_TSV)
    case_rows = load_table(CASE_TSV)
    raw = json.loads(RAW_LLM_JSON.read_text(encoding="utf-8"))

    configure_style()
    # configure_style() targets SimSun for CJK prose; this figure is entirely
    # Latin, so pin the family to the paper's Arial standard.
    plt.rcParams["font.family"] = "Arial"
    fig = plt.figure(figsize=(7.4, 3.05))
    gs = fig.add_gridspec(
        1, 4, width_ratios=[1.16, 0.92, 0.84, 0.98], wspace=0.44,
        left=0.115, right=0.985, top=0.895, bottom=0.165,
    )
    draw_ladder(fig.add_subplot(gs[0, 0]), ladder)
    draw_position_delta(fig.add_subplot(gs[0, 1]), position_rows)
    draw_delta_distribution(fig.add_subplot(gs[0, 2]), case_rows)
    draw_accuracy_without_verification(fig.add_subplot(gs[0, 3]), ladder, raw)

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES_DIR / "fig03_agent_ablation.pdf", bbox_inches="tight",
                pad_inches=0.02)
    fig.savefig(FIGURES_DIR / "fig03_agent_ablation.png", dpi=300,
                bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print(f"wrote={FIGURES_DIR / 'fig03_agent_ablation.pdf'}")


if __name__ == "__main__":
    main()
