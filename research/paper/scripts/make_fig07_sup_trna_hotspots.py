#!/usr/bin/env python3
"""Figure: where engineered suppressor tRNAs are changed, in Sprinzl coordinates.

The two upper panels use the same visual grammar as the ENSURE record page, so a
reader who has seen the web view recognises the picture immediately: one stacked
column per alignment position, four series (match, mismatch, insertion, deletion),
and the columns laid out in Sprinzl chain order, which is what the page does now:
1 to 76 as the skeleton, an insertion column directly after the column it hangs
from, and the variable-arm V columns between 45 and 46.  Insertion columns are
merged per anchor (``37i1 … 37i24`` become one ``37i`` column), which is also how
the ranked and cloverleaf views below count them, so one rule runs through the
whole figure.  Panel a is the table ENSURE published before this analysis; panel
b is the same cohort re-numbered by SprinzlMap, which is the table the page
publishes now.

Panel c ranks the positions by how often engineering touches them, which is the
part the stacked view compresses into a thin cap.  Panel d paints the SprinzlMap
counts onto the cloverleaf layout used in Figures 3-4, where the columns that
belong to a loop sit where the residue is.

Inputs are written by ``benchmark/scripts/ensure_perpos_sprinzlmap.py``; the
script calls no model and reads no run directory.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib import patheffects as pe

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import cloverleaf_layout as layout  # noqa: E402
sys.path.insert(0, str(ROOT / "APP"))
from backend.domain.alignment.sprinzl_coordinates import order_key  # noqa: E402

PAPER = HERE.parent
RESULTS = PAPER / "results/ensure_perpos"
FIGURES = PAPER / "figures"

# Series colours follow the ENSURE web chart.
MATCH = "#5470C6"
MISMATCH = "#91CC75"
INSERTION = "#FAC858"
DELETION = "#EE6666"
SERIES = ("match", "mismatch", "insertion", "deletion")
SERIES_COLOUR = {"match": MATCH, "mismatch": MISMATCH, "insertion": INSERTION, "deletion": DELETION}

NAVY = "#1F5A73"
LIGHT_BLUE = "#B8C5CC"
REVIEW = "#C8924B"
INK = "#26333B"
MUTED = "#66737C"
GRID = "#E0E6F1"

INSERTION_RE = re.compile(r"^(?P<anchor>.+)i\d+$")


def merge_insertion_columns(frame: dict, order: list[str]):
    """Collapse each anchor's insertion columns into one ``<anchor>i`` column.

    ``order`` is already in chain order, and an insertion label sorts directly
    after its anchor, so the merged column is emitted where the first member of
    that insertion family sat.  Returns the merged counts, the merged order and,
    for the caption, how many columns each merged label replaced.
    """
    merged: dict[str, dict[str, int]] = {}
    merged_order: list[str] = []
    replaced: dict[str, int] = {}
    for column in order:
        m = INSERTION_RE.match(column)
        if not m:
            if any(frame[column][k] for k in SERIES):
                merged[column] = frame[column]
                merged_order.append(column)
            continue
        label = f"{m.group('anchor')}i"
        if label in merged:
            continue
        family = [c for c in order if INSERTION_RE.match(c) and INSERTION_RE.match(c).group("anchor") == m.group("anchor")]
        totals = {k: sum(frame[c][k] for c in family) for k in SERIES}
        if not any(totals.values()):
            continue
        merged[label] = totals
        replaced[label] = len(family)
        merged_order.append(label)
    return merged, merged_order, replaced


# Molecule-level bands used under the ranked view.
REGION_BANDS = [
    ("acceptor stem", "1", "7"), ("D arm", "10", "25"), ("acceptor stem 5'", "7", "1"),
    ("anticodon arm", "27", "43"), ("variable", "44", "V5"), ("T arm", "49", "65"),
    ("3' acceptor", "66", "76"),
]


def chain_key(label: str):
    """Sort key from the shared Sprinzl frame; labels outside the frame last."""
    try:
        return (0, *order_key(label))
    except ValueError:
        return (1, 0, 0, 0)


def chain_order(positions) -> list[str]:
    """Order position labels the way the molecule is read: 5' to 3' along the
    Sprinzl path.  Same rule the ENSURE page now applies, shared with the
    backend so figure and site cannot drift apart.  Labels outside the Sprinzl
    frame keep their own text order at the end."""
    unique = list(dict.fromkeys(positions))
    return sorted(unique, key=lambda p: (chain_key(p), p))


def column_sort_key(column: str):
    m = re.match(r"^(V?)(-?\d+)([a-z]?)(?:i(\d+))?$", column)
    if not m:
        return (2, 0, column, 0)
    prefix, num, letter, ins = m.groups()
    return (1, int(num), letter or "", int(ins or 0)) if prefix else (0, int(num), letter or "", int(ins or 0))


def style() -> str:
    family = "DejaVu Sans"
    for path in (
        Path.home() / ".local/share/fonts/aylm_fonts/Arial.TTF",
        Path("/usr/share/fonts/truetype/mstrcorefonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ):
        if path.exists():
            font_manager.fontManager.addfont(str(path))
            family = font_manager.FontProperties(fname=str(path)).get_name()
            break
    plt.rcParams.update({
        "font.family": family,
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7.3,
        "legend.fontsize": 7,
        "axes.linewidth": 0.55,
        "axes.edgecolor": INK,
        "text.color": INK,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "figure.facecolor": "white",
    })
    return family


def read_frame(path: Path) -> dict[str, dict[str, int]]:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return {str(r["position"]): {k: int(r[k] or 0) for k in SERIES} for r in rows}


def fold_to_anchor(counts: Counter) -> Counter:
    out: Counter = Counter()
    for column, value in counts.items():
        m = INSERTION_RE.match(column)
        out[m.group("anchor") if m else column] += value
    return out


def read_counts(path: Path, field: str = "mismatch") -> Counter:
    return fold_to_anchor(Counter({p: v[field] for p, v in read_frame(path).items()}))


def nice_step(top: float) -> float:
    raw = top / 5
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if raw <= step:
            return float(step)
    return float(step)


def contiguous_blocks(columns: list[str], keep) -> list[tuple[int, int]]:
    """Index ranges of maximal runs of columns that ``keep`` accepts."""
    spans: list[tuple[int, int]] = []
    start = None
    for index, column in enumerate(columns):
        if keep(column):
            start = index if start is None else start
        elif start is not None:
            spans.append((start, index - 1))
            start = None
    if start is not None:
        spans.append((start, len(columns) - 1))
    return spans


def site_panel(ax, frame: dict[str, dict[str, int]], *, order: list[str],
               heading: str, subtitle: str, band: tuple[str, str, str] | None = None,
               y_top: float | None = None) -> None:
    """Draw one column per alignment position, stacked as in the ENSURE web chart.

    ``y_top`` lets the two charts share one scale, so equal bar heights mean equal
    counts in panel a and panel b.  Columns whose four series are all zero are left
    out: an empty column says how a numbering names loop residues, not what a
    position does.
    """
    columns = [p for p in order if any(frame[p][k] for k in SERIES)]
    x = np.arange(len(columns), dtype=float)
    bottoms = np.zeros(len(columns))
    for key in SERIES:
        values = np.array([frame[c][key] for c in columns], dtype=float)
        ax.bar(x, values, 0.74, bottom=bottoms, color=SERIES_COLOUR[key],
               edgecolor="none", linewidth=0, label=key)
        bottoms += values

    stacked = float(bottoms.max()) if len(columns) else 0.0
    # One step, taken from the taller of the two panels, is reused by both.
    step = nice_step(max(stacked, y_top or 0.0))
    ceiling = step * float(np.ceil(max(stacked, y_top or 0.0) / step))
    ax.set_ylim(0, ceiling * 1.14)
    ticks = np.arange(0, ceiling + step / 2, step)
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{int(t):,}" for t in ticks])
    ax.grid(axis="y", color=GRID, lw=0.55)
    ax.set_axisbelow(True)

    # Labelling every column would print 150 unreadable strings, and about a
    # third of the columns are loop positions with nothing to compare.  The axis
    # therefore carries the decade landmarks of the canonical frame, the ends of
    # the chain, the two ends of each variable-arm block, and any merged
    # insertion column that carries a real signal.  A landmark is dropped when
    # one of the named columns sits beside it, which keeps the rotated text from
    # touching.
    numeric = [i for i, c in enumerate(columns) if c.isdigit()]
    named: list[tuple[int, int]] = []
    for first, last in contiguous_blocks(columns, lambda c: c.startswith("V")):
        named += [(1, first), (1, last)]
    named += [(3, i) for i, c in enumerate(columns)
              if c.endswith("i") and not c.isdigit()
              and (frame[c]["insertion"] + frame[c]["deletion"]) >= 100]
    if numeric:
        named += [(2, numeric[0]), (2, numeric[-1])]
    kept: list[int] = []
    for _, i in sorted(named, key=lambda pr: (-pr[0], pr[1])):
        if all(abs(i - j) >= 2 for j in kept):
            kept.append(i)
    if numeric:
        landmarks = [i for i in numeric if int(columns[i]) % 5 == 0]
        for i in landmarks:
            if all(abs(i - j) >= 3 for j in kept):
                kept.append(i)
    labelled = np.array(sorted(set(kept)), dtype=float)
    ax.set_xticks(labelled)
    ax.set_xticklabels([columns[int(i)] for i in labelled], rotation=45,
                       ha="right", fontsize=5.6, color="#4A5A63")
    ax.tick_params(axis="x", length=1.6, pad=1.2)
    ax.tick_params(axis="y", length=1.6, pad=1.5)
    ax.set_xlim(-0.9, len(columns) - 0.1)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#333333")

    # Axis names sit at the ends of the axes, as they do in the web chart.
    ax.text(0.0, 1.005, "count", transform=ax.transAxes, fontsize=6.4,
            color=MUTED, va="bottom", ha="left")
    ax.text(1.004, 0.0, "position", transform=ax.transAxes, fontsize=6.4,
            color=MUTED, va="center", ha="left")

    if band:
        first, last, note = band
        if first in columns and last in columns:
            i0, i1 = columns.index(first), columns.index(last)
            ax.add_patch(Rectangle((i0 - 0.5, 0), i1 - i0 + 1, ceiling,
                                   facecolor=REVIEW, alpha=0.11, edgecolor="none", zorder=0))
            local = max(bottoms[i0:i1 + 1], default=ceiling * 0.5)
            ax.text((i0 + i1) / 2, ceiling * 1.045, note, ha="center", va="bottom",
                    fontsize=6.2, color=REVIEW)
            ax.plot([i0 - 0.5, i0 - 0.5, i1 + 0.5, i1 + 0.5],
                    [local, ceiling * 1.025, ceiling * 1.025, local],
                    lw=0.5, color=REVIEW, alpha=0.85, zorder=5)

    ax.text(0.0, 1.155, heading, transform=ax.transAxes, fontsize=7.6,
            fontweight="bold", color=INK, va="bottom", ha="left")
    ax.text(1.0, 1.155, subtitle, transform=ax.transAxes, fontsize=6.4,
            color=MUTED, va="bottom", ha="right")

    handles, _ = ax.get_legend_handles_labels()
    ax.legend(handles, list(SERIES), loc="upper center", bbox_to_anchor=(0.5, 1.10),
              ncol=4, frameon=False, handlelength=1.0, handleheight=0.78,
              columnspacing=1.3, handletextpad=0.45, fontsize=6.2)


def label(ax, y, top, ens, spr, peak) -> None:
    """Write both counts of a position on one baseline, left to right."""
    renderer = ax.figure.canvas.get_renderer()
    x_lo, x_hi = ax.get_xlim()
    per_pixel = (x_hi - x_lo) / ax.get_window_extent().width
    for yy, p in zip(y, top):
        e, s = ens.get(p, 0), spr.get(p, 0)
        x = max(e, s) + peak * 0.022
        first = ax.text(x, yy, f"{e:,}", va="center", ha="left", fontsize=5.9, color=MUTED)
        width = first.get_window_extent(renderer=renderer).width
        ax.text(x + (width + 3.4) * per_pixel, yy, f"{s:,}", va="center", ha="left",
                fontsize=5.9, color=NAVY)


def ranked_panel(ax, ensure: Counter, sprinzl: Counter) -> None:
    """Variation only, ranked by how often engineering touches the position."""
    ens = Counter({p: v for p, v in ensure.items() if p != "-1"})
    spr = Counter({p: v for p, v in sprinzl.items() if p != "-1"})
    ranked = sorted(((p, ens.get(p, 0) + spr.get(p, 0)) for p in set(ens) | set(spr)),
                    key=lambda kv: (-kv[1], column_sort_key(kv[0])))
    top = list(reversed([p for p, total in ranked[:14] if total > 0]))
    y = np.arange(len(top), dtype=float)
    height = 0.36
    ax.barh(y + height / 2, [ens.get(p, 0) for p in top], height, color=LIGHT_BLUE,
            edgecolor="none", label="ENSURE coordinates")
    ax.barh(y - height / 2, [spr.get(p, 0) for p in top], height, color=NAVY,
            edgecolor="none", label="SprinzlMap coordinates")
    peak = max([ens.get(p, 0) for p in top] + [spr.get(p, 0) for p in top] + [1])
    ax.set_yticks(y)
    ax.set_yticklabels(top, fontsize=6.4)
    ax.set_xlim(0, peak * 1.30)
    # One baseline per position: two numbers on the same row would sit a third of a
    # row apart, and the ranks here differ by single digits, so those labels collide.
    # The ENSURE count and the SprinzlMap count are written side by side to the right
    # of the longer bar, in the colour of their bar.
    label(ax, y, top, ens, spr, peak)
    step = nice_step(peak)
    ax.set_xticks(np.arange(0, peak + step / 2, step))
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, lw=0.55)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.set_xlabel("pairs whose two members differ at this position", fontsize=6.6, labelpad=1.5)
    ax.legend(loc="lower right", frameon=False, fontsize=6.2, handlelength=1.0)
    ax.set_title("positions ranked by how often they change", fontsize=6.6, color=MUTED,
                 loc="left", pad=3.0)


def cloverleaf_panel(ax, counts: Counter) -> None:
    """Shade the same cloverleaf layout used in Figures 3-4 by how often each
    position changes.  Numbers stay in panel c, which keeps the structure legible."""
    positions = layout.CANONICAL
    backbone = layout.backbone_order()
    xs = [positions[p][0] for p in backbone]
    ys = [positions[p][1] for p in backbone]
    values = np.array([counts.get(p, 0) for p in backbone], dtype=float)
    peak = max(values.max(), 1.0)

    ax.plot(xs, ys, lw=0.7, color=MUTED, solid_capstyle="round", zorder=1)
    drawn = {p: np.array(positions[p]) for p in positions}
    for a, b in layout.PAIR_LINKS:
        if a in drawn and b in drawn:
            ax.plot(*zip(drawn[a], drawn[b]), lw=0.5, color="#BFC8CD", zorder=1)

    radius = 6.9
    for p, (x0, y0), value in zip(backbone, zip(xs, ys), values):
        shade = 0.0 if value == 0 else 0.20 + 0.80 * (value / peak) ** 0.55
        colour = "#F2F4F5" if value == 0 else matplotlib.colormaps["YlOrBr"](shade)
        edge = "#9AA6AC" if value == 0 else "#8A6A3A"
        ax.add_patch(plt.Circle((x0, y0), radius, facecolor=colour, edgecolor=edge,
                                lw=0.35, zorder=2))
        if value >= 0.45 * peak:
            ax.add_patch(plt.Circle((x0, y0), radius + 1.6, facecolor="none",
                                    edgecolor=REVIEW, lw=0.7, zorder=3))

    for p in ("1", "72", "73", "34", "36", "44", "46", "27", "43", "49", "65"):
        if p in positions:
            x0, y0 = positions[p]
            ax.text(x0, y0 + radius + 3.2, p, ha="center", va="bottom",
                    fontsize=5.3, color=MUTED, zorder=3)

    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(min(xs) - 28, max(xs) + 28)
    ax.set_ylim(min(ys) - 24, max(ys) + 28)
    bar = ax.inset_axes((0.70, 0.03, 0.20, 0.028))
    bar.imshow(np.linspace(0, 1, 256).reshape(1, -1), aspect="auto",
               cmap="YlOrBr", vmin=0, vmax=peak)
    bar.set_xticks([0, peak])
    bar.set_xticklabels(["0", f"{int(peak):,}"], fontsize=5.4)
    bar.set_yticks([])
    for spine in bar.spines.values():
        spine.set_linewidth(0.4)
        spine.set_color(MUTED)
    ax.text(0.70, 0.075, "pairs changed at the position", transform=ax.transAxes,
            fontsize=5.4, color=MUTED, va="bottom", ha="left")


def panel_heading(ax, letter: str) -> None:
    ax.text(-0.035, 1.16, letter, transform=ax.transAxes, fontsize=10,
            fontweight="bold", color=INK, va="top", ha="left")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=RESULTS)
    ap.add_argument("--out-prefix", default="fig07_sup_tRNA_hotspots")
    args = ap.parse_args()

    style()
    ensure_raw = read_frame(args.results_dir / "perpos_ensure_current.tsv")
    # The published view: inserted strings inside a gap are aligned with each
    # other before the layout is re-indexed, which is what the ENSURE table holds.
    map_raw = read_frame(args.results_dir / "perpos_sprinzlmap_edits.tsv")
    # Panels c and d fold insertions onto the canonical column they hang from,
    # so they read the unmerged frame; only a and b draw the merged column.
    map_counts = fold_to_anchor(Counter({p: v["mismatch"] for p, v in map_raw.items()}))
    summary = json.loads((args.results_dir / "summary.json").read_text(encoding="utf-8"))

    pairs = {v: summary.get("pairs_used_by_view", {}).get(v, 0)
             for v in ("map", "ensure_js", "ensure_edits", "map_edits")}
    ensure_frame, order_ensure, repl_ensure = merge_insertion_columns(
        ensure_raw, chain_order(ensure_raw))
    map_frame, order_map, repl_map = merge_insertion_columns(
        map_raw, chain_order(map_raw))
    live_ensure, live_map = len(order_ensure), len(order_map)

    FIGURES.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(7.3, 7.5))
    grid = fig.add_gridspec(3, 2, width_ratios=[1.15, 1.0], height_ratios=[1.0, 1.0, 0.86],
                            left=0.062, right=0.952, top=0.955, bottom=0.055,
                            hspace=0.72, wspace=0.22)
    ax_a = fig.add_subplot(grid[0, :])
    ax_b = fig.add_subplot(grid[1, :])
    ax_c = fig.add_subplot(grid[2, 0])
    ax_d = fig.add_subplot(grid[2, 1])

    stacked_a = max(sum(ensure_frame[c][k] for k in SERIES) for c in order_ensure)
    stacked_b = max(sum(map_frame[c][k] for k in SERIES) for c in order_map)
    shared_top = max(stacked_a, stacked_b)

    site_panel(ax_a, ensure_frame, order=order_ensure, y_top=shared_top,
               heading="ENSURE published per-position counts",
               subtitle=f"ENSURE published table \u00b7 {live_ensure:,} columns shown "
                        f"\u00b7 insertions merged per anchor")
    site_panel(ax_b, map_frame, order=order_map, y_top=shared_top,
               heading="SprinzlMap re-numbering of the same cohort",
               subtitle=f"{pairs['map_edits']:,} pairs re-numbered by SprinzlMap "
                        f"\u00b7 {live_map:,} columns shown"
                        f" \u00b7 insertions merged per anchor",
               band=("34", "36", "anticodon positions"))

    ens_all = fold_to_anchor(Counter({p: v["mismatch"] for p, v in ensure_raw.items()}))
    ranked_panel(ax_c, ens_all, map_counts)
    cloverleaf_panel(ax_d, fold_to_anchor(Counter({p: v["mismatch"] + v["insertion"] + v["deletion"]
                                                   for p, v in map_raw.items()})))

    for ax, letter in ((ax_a, "a"), (ax_b, "b"), (ax_c, "c"), (ax_d, "d")):
        panel_heading(ax, letter)

    for ext in ("pdf", "svg", "png"):
        fig.savefig(FIGURES / f"{args.out_prefix}.{ext}",
                    dpi=300 if ext == "png" else None, facecolor="white")
    print(f"wrote {FIGURES}/{args.out_prefix}.[pdf|svg|png]")
    print(f"columns plotted after merging insertions: ENSURE {live_ensure}, SprinzlMap {live_map}")
    print("merged insertion columns:",
          {k: v for k, v in sorted(repl_ensure.items(), key=lambda kv: -kv[1])[:5]},
          {k: v for k, v in sorted(repl_map.items(), key=lambda kv: -kv[1])[:5]})
    print(f"pairs: {pairs}")


if __name__ == "__main__":
    main()
