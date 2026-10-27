#!/usr/bin/env python3
"""Generate the auditable workbench figure from real coordinate objects."""
from __future__ import annotations
import csv
import json
import math
from pathlib import Path
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, patches
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import cloverleaf_layout as CL  # noqa: E402
from make_fig07_sup_trna_hotspots import (  # noqa: E402
    chain_order,
    merge_insertion_columns,
    read_frame as read_perpos_frame,
)

ROOT = Path(__file__).resolve().parents[3]
PAPER = ROOT / "research/paper"
RESULTS = PAPER / "results/ensure_perpos"
FIGURES = PAPER / "figures"
INK, MUTED, GRID = "#344054", "#667085", "#D0D5DD"
BLUE, BLUE_FACE = "#4D78A8", "#DCEAF5"
TEAL, TEAL_FACE = "#287A78", "#E5F2F0"
ORANGE, ORANGE_FACE = "#C77916", "#FBE8C4"
RED, RED_FACE = "#C43D34", "#F8D9D6"
PALE = "#F5F7F9"
READTHROUGH_RESULTS = PAPER / "results/fig06_readthrough.tsv"
A1T5_MAP = PAPER / "results/fig06_a1t5_sprinzl.tsv"


def setup():
    for p in (Path("/home/ouyangzhuo/.local/share/fonts/aylm_fonts/Arial.TTF"), Path("/home/ouyangzhuo/.local/share/fonts/aylm_fonts/Arialbd.TTF")):
        if p.exists(): font_manager.fontManager.addfont(str(p))
    plt.rcParams.update({"font.family":"Arial", "font.sans-serif":["Arial","DejaVu Sans"], "font.size":7,
                         "pdf.fonttype":42, "ps.fonttype":42, "svg.fonttype":"none", "figure.facecolor":"white"})


def text(ax, x, y, s, size=5.5, color=INK, weight="normal", ha="left", va="center", **kw):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, zorder=5, **kw)


def heading(ax, letter, title="", subtitle=""):
    ax.axis("off")
    text(ax, 0, 1.025, letter, size=9.5, color="#111", weight="bold", va="bottom")
    if title: text(ax, .045, 1.025, title, size=7.6, weight="bold", va="bottom")
    if subtitle: text(ax, .045, .985, subtitle, size=5.7, color=MUTED, va="bottom")


def cell(ax, x, y, w, h, s, face=PALE, edge="white", color=INK, weight="normal", lw=.7):
    ax.add_patch(patches.Rectangle((x,y),w,h, transform=ax.transAxes, facecolor=face, edgecolor=edge, linewidth=lw, zorder=2))
    text(ax, x+w/2, y+h/2, s, size=5.4, color=color, weight=weight, ha="center")


def clover(ax, cx, cy, scale, *, anticodon=True, highlight=()):
    # The parent axes are wide; use an equal-aspect inset so nucleotide nodes
    # remain circular in the exported figure.
    inset = ax.inset_axes(
        [cx - 0.52 * scale, cy - 0.52 * scale, 1.04 * scale, 1.04 * scale],
        transform=ax.transAxes,
    )
    inset.set_xlim(-.42, .42)
    inset.set_ylim(-.42, .42)
    inset.set_aspect("equal")
    inset.axis("off")
    inset.patch.set_alpha(0)
    paths = [
        ([(0,-.22),(0,-.14),(0,-.06),(0,.02),(0,.10),(0,.18),(0,.25)], BLUE_FACE, (1,2) if anticodon else ()),
        ([(-.05,.04),(-.12,.08),(-.20,.11),(-.27,.09),(-.32,.03),(-.32,-.04),(-.26,-.10),(-.19,-.10),(-.13,-.07)], TEAL_FACE, (2,3) if anticodon else ()),
        ([(.05,.04),(.12,.08),(.20,.11),(.27,.09),(.32,.03),(.32,-.04),(.26,-.10),(.19,-.10),(.13,-.07)], BLUE_FACE, ()),
        ([(-.02,.14),(-.07,.20),(-.09,.27),(-.05,.33),(.02,.35),(.08,.32),(.09,.26),(.07,.20),(.02,.14)], ORANGE_FACE, ()),
        ([(-.02,-.10),(-.06,-.17),(-.08,-.24),(-.04,-.30),(.03,-.31),(.08,-.26),(.08,-.19),(.04,-.13),(0,-.10)], RED_FACE, ()),
    ]
    for points, fill, hi in paths:
        xy = points
        inset.plot([x for x,_ in xy],[y for _,y in xy], color=GRID, lw=.85, zorder=1)
        for i,(x,y) in enumerate(xy):
            inset.add_patch(patches.Circle((x,y), .016, facecolor=fill if i in hi else "white",
                                           edgecolor=fill if i in hi else "#AAB7C7", lw=.65, zorder=2))
    for hx, hy, label in highlight:
        inset.add_patch(patches.Circle((hx, hy), .045, facecolor=RED, edgecolor="white",
                                       lw=.7, zorder=4))
        inset.text(hx - .05, hy + .04, label, fontsize=5.0, color=RED, ha="right", va="bottom")


def panel_a(ax):
    heading(ax, "a")
    cols = ["27", "28", "31", "35", "39", "42", "43", "69", "74", "75", "76"]
    rows = [("parent", set()), ("827", {"35", "69", "74", "75", "76"}),
            ("831", {"27", "28", "31", "35", "39", "42", "43", "74", "75", "76"}),
            ("826", {"27", "28", "31", "35", "39", "42", "43", "69", "74", "75", "76"})]
    anticodon_cols = set(cols[:7])
    x0, y0, cw, rh = .055, .66, .058, .135
    for j, pos in enumerate(cols):
        ac = pos in anticodon_cols; acc = pos == "69"; cca = pos in {"74", "75", "76"}
        face = TEAL_FACE if ac else RED_FACE if acc else ORANGE_FACE
        edge = TEAL if ac else RED if acc else ORANGE
        cell(ax, x0 + j * cw, y0, cw - .006, .09, pos, face=face, edge=edge, color=edge, weight="bold")
    for i, (name, changed) in enumerate(rows):
        y = y0 - (i + 1) * rh
        text(ax, x0 - .015, y + .045, name, size=6.4, ha="right",
             weight="bold" if i == 0 else "normal")
        for j, pos in enumerate(cols):
            if i == 0:
                cell(ax, x0 + j * cw, y, cw - .006, .09, "–", face=PALE, edge="white", color=MUTED)
            elif pos in changed:
                ac = pos in anticodon_cols
                acc = pos == "69"
                face, edge = ((TEAL_FACE, TEAL) if ac else (RED_FACE, RED) if acc else (ORANGE_FACE, ORANGE))
                cell(ax, x0 + j * cw, y, cw - .006, .09, "×", face=face, edge=edge, color=edge, weight="bold")
            else:
                cell(ax, x0 + j * cw, y, cw - .006, .09, "·", face="white", edge=GRID, color=MUTED)
    legend = (("27–43", "anticodon arm", TEAL), ("69", "acceptor stem", RED), ("74–76", "CCA tail", ORANGE))
    for k, (label, desc, colour) in enumerate(legend):
        y = .62 - k * .14
        ax.add_patch(patches.Rectangle((.715, y), .019, .075, transform=ax.transAxes,
                                       facecolor=colour, edgecolor="none"))
        text(ax, .742, y + .055, label, size=5.8, color=colour, weight="bold")
        text(ax, .742, y + .015, desc, size=5.4, color=MUTED)


def panel_b(ax):
    """Recompute ENSURE's per-position alignment variation in Sprinzl coordinates.

    Only non-matching events are plotted.  Including the approximately 78,000
    matched positions on the same linear scale would hide the variation that this
    panel is intended to expose.  Insertion columns are merged onto their anchor
    because their ordinal names are not comparable across parent-variant pairs.
    """
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    pair_info = summary["pair_consistency"]["map_edits"]
    heading(ax,"b")
    ax.set_axis_on()
    raw = read_perpos_frame(RESULTS / "perpos_sprinzlmap_edits.tsv")
    merged, order, _ = merge_insertion_columns(raw, chain_order(raw))
    if sum(values["mismatch"] for values in merged.values()) != int(pair_info["substitutions"]):
        raise RuntimeError("per-position substitutions disagree with summary.json")
    columns = [
        column for column in order
        if merged[column]["mismatch"] + merged[column]["insertion"] + merged[column]["deletion"] > 0
    ]
    x = np.arange(len(columns), dtype=float)
    bottoms = np.zeros(len(columns), dtype=float)
    series = (
        ("mismatch", TEAL, "substitution"),
        ("insertion", ORANGE, "insertion"),
        ("deletion", RED, "deletion"),
    )
    for key, colour, label in series:
        values = np.asarray([merged[column][key] for column in columns], dtype=float)
        ax.bar(x, values, bottom=bottoms, width=.78, color=colour,
               edgecolor="none", linewidth=0, label=label, zorder=2)
        bottoms += values

    conserved = [columns.index(position) for position in ("18", "19") if position in columns]
    if conserved:
        ax.axvspan(min(conserved) - .5, max(conserved) + .5, color="#EEF0F3", alpha=.9, zorder=0)
    anticodon = [columns.index(position) for position in ("34", "35", "36") if position in columns]
    if anticodon:
        left = min(anticodon) - .5
        right = max(anticodon) + .5
        ax.axvspan(left, right, color="#EEF0F3", alpha=.9, zorder=0)

    wanted = {"1", "10", "20", "30", "40", "50", "60", "70", "V11", "V21", "76"}
    labelled = [index for index, column in enumerate(columns) if column in wanted]
    ax.set_xticks(labelled)
    ax.set_xticklabels([columns[index] for index in labelled], rotation=0,
                       fontsize=5.6, color=INK)
    ax.set_xlim(-.8, len(columns) - .2)
    ax.set_ylim(0, bottoms.max() * 1.10)
    ax.set_ylabel("variation events", fontsize=6.2, labelpad=3)
    ax.set_xlabel("Sprinzl position", fontsize=6.2, labelpad=2)
    ax.tick_params(axis="both", labelsize=5.5, length=2, pad=1.5)
    ax.grid(axis="y", color=GRID, lw=.45)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    # Keep the key inside the upper-right whitespace. Placing it above the axes
    # made the first handle cover the y tick at 600 on the rendered page.
    legend = ax.legend(
        loc="upper right", bbox_to_anchor=(0.995, 0.995), ncol=3,
        frameon=True, fontsize=5.6, handlelength=1.1, columnspacing=1.0,
        handletextpad=.35, borderaxespad=0,
    )
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_edgecolor("none")
    legend.get_frame().set_alpha(0.94)
    legend.set_zorder(10)


def panel_c(ax):
    """D arm of A1T5 drawn at its canonical Sprinzl coordinates.

    Positions come from the shared web layout, so the arm keeps the standard
    cloverleaf orientation: the D loop faces left and the 10--13 / 22--25 stem
    faces right.  Junction residues 9 and 26 are kept as faded stubs to show
    where the arm rejoins the rest of the molecule.  Standard positions that
    A1T5 does not occupy remain as light placeholders, all unchanged residues
    share one neutral fill, and only the engineered positions carry red.
    """
    heading(ax, "c")
    bases = {}
    with A1T5_MAP.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            bases[row["sprinzl"]] = row["base"].replace("T", "U")

    loop_labels = ["14", "15", "16", "17", "17a", "18", "19", "20", "20a", "20b", "21"]
    labels = ["9", "10", "11", "12", "13", *loop_labels, "22", "23", "24", "25", "26"]
    backbone = ["9", "10", "11", "12", "13", *loop_labels, "22", "23", "24", "25", "26"]
    pair_links = [("10", "25"), ("11", "24"), ("12", "23"), ("13", "22")]
    junction = {"9", "26"}
    # Direction of the rest of the molecule, taken from the neighbouring
    # standard positions so the truncated bonds stay on the canonical frame.
    continues = {"9": "8", "26": "27"}

    points = {label: CL.CANONICAL[label] for label in labels}
    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    # Leave room for the position numbers placed outside the terminal stem
    # residues; otherwise the labels visually merge with the base circles.
    pad = 20
    x_lo, x_hi = min(xs) - pad, max(xs) + pad
    y_lo, y_hi = min(ys) - pad, max(ys) + pad
    data_w, data_h = x_hi - x_lo, y_hi - y_lo
    aspect = data_w / data_h

    # Node radii and type sizes are derived from the 25-unit lattice spacing of
    # the layout, so the drawing keeps the same proportions at any panel size.
    panel_pos = ax.get_position()
    fig_w, fig_h = ax.get_figure().get_size_inches()
    slot_w, slot_h = panel_pos.width * fig_w, panel_pos.height * fig_h
    # Reserve a band at the bottom of the panel for the variant name so the
    # labels can never collide with the loop residues.
    label_band, heading_band = 0.155, 0.018
    band_h_frac = 1.0 - label_band - heading_band
    arm_w = min(slot_w * 0.47, slot_h * band_h_frac * aspect)
    arm_h = arm_w / aspect
    fw, fh = arm_w / slot_w, arm_h / slot_h
    cy = label_band + band_h_frac / 2
    unit_pt = arm_w * 72 / data_w
    node_r = 9.6
    stub_r = 6.4
    base_font = max(4.0, round(0.565 * 2 * node_r * unit_pt, 2))
    label_font = max(3.8, round(base_font * 0.94, 2))

    neutral_face, neutral_edge, neutral_text = "#E9EDF3", "#98A4B2", "#33404F"
    faint_edge = "#D2D9E1"
    faint_number = "#7A8794"
    edited_face, edited_edge = "#C0392B", "#96271D"
    loop_cx = sum(points[name][0] for name in loop_labels) / len(loop_labels)
    loop_cy = sum(points[name][1] for name in loop_labels) / len(loop_labels)

    for cx, edits, name in ((0.255, {"18"}, "M1 (18C)"),
                            (0.745, {"18", "19"}, "M2 (18C + 19C)")):
        variant_bases = dict(bases)
        variant_bases.update({position: "C" for position in edits})
        inset = ax.inset_axes([cx - fw / 2, cy - fh / 2, fw, fh])
        inset.set_xlim(x_lo, x_hi); inset.set_ylim(y_lo, y_hi)
        inset.set_aspect("equal"); inset.axis("off")
        inset.patch.set_alpha(0)

        for left, right in zip(backbone, backbone[1:]):
            is_junction = left in junction or right in junction
            inset.plot(*zip(points[left], points[right]), color="#C3CBD5",
                       lw=1.0 if not is_junction else 0.9,
                       alpha=0.42 if is_junction else 0.78, zorder=1)
        for label, onward in continues.items():
            x, y = points[label]
            nx, ny = CL.CANONICAL[onward]
            vx, vy = nx - x, ny - y
            norm = math.hypot(vx, vy) or 1.0
            reach = 13.0
            inset.plot([x + vx / norm * stub_r, x + vx / norm * (stub_r + reach)],
                       [y + vy / norm * stub_r, y + vy / norm * (stub_r + reach)],
                       color="#C3CBD5", lw=0.9, alpha=0.30, zorder=1)
        for left, right in pair_links:
            inset.plot(*zip(points[left], points[right]), color="#CBD3DC",
                       lw=0.85, alpha=0.85, zorder=1)

        for label in labels:
            x, y = points[label]
            present = label in bases
            edited = label in edits
            is_stub = label in junction
            radius = stub_r if is_stub else node_r
            if edited:
                # A three-step halo gives the red marks a soft fade instead of
                # a hard ring, without adding another categorical colour.
                for extra, alpha in ((5.2, 0.06), (3.0, 0.12), (1.2, 0.22)):
                    inset.add_patch(patches.Circle((x, y), radius + extra,
                                                   facecolor="#E9A79F",
                                                   edgecolor="none", alpha=alpha,
                                                   zorder=2))
                face, edge, alpha = edited_face, edited_edge, 1.0
            elif is_stub:
                face, edge, alpha = "white", faint_edge, 0.55
            elif present:
                face, edge, alpha = neutral_face, "#A8B2BF", 0.98
            else:
                face, edge, alpha = "white", faint_edge, 0.75
            inset.add_patch(patches.Circle((x, y), radius, facecolor=face,
                                           edgecolor=edge,
                                           linewidth=0.85 if edited else 0.6,
                                           alpha=alpha, zorder=3))

            if edited:
                inset.text(x, y, variant_bases[label], fontsize=base_font,
                           color="white", ha="center", va="center",
                           weight="bold", zorder=5)
            elif present and not is_stub:
                inset.text(x, y, bases[label], fontsize=base_font,
                           color=neutral_text, ha="center", va="center", zorder=5)
            elif present and is_stub:
                inset.text(x, y, bases[label], fontsize=base_font * 0.88,
                           color="#AEB7C2", ha="center", va="center", zorder=5)
            else:
                inset.text(x, y, "\u00b7", fontsize=label_font * 1.6,
                           color="#CBD3DC", ha="center", va="center", zorder=5)

            # Position numbers sit outside the ring: radially for the loop, and
            # straight above or below for the stem rows.
            if label in loop_labels:
                vx, vy = x - loop_cx, y - loop_cy
                norm = math.hypot(vx, vy) or 1.0
                offset = radius + (13.2 if edited else 9.2)
                lx, ly = x + vx / norm * offset, y + vy / norm * offset
            else:
                above = y > loop_cy
                lx = x + (5.0 if is_stub else 0.0)
                stem_offset = 12.6 if is_stub else 16.5
                ly = y + (stem_offset if above else -stem_offset)
            inset.text(lx, ly, label, fontsize=label_font,
                       color=edited_edge if edited else faint_number,
                       ha="center", va="center",
                       weight="bold" if edited else "normal", zorder=6)
        ax.text(cx, label_band * 0.42, name, size=6.8, ha="center", color=INK,
                weight="bold")


def panel_d(ax):
    """Horizontal box plots of the per-well readthrough ratios.

    Only the interquartile box and its median are drawn; the four wells behind
    each box are plotted individually, so no whiskers or error bars are needed.
    Mutant constructs reuse the red of the edited residues in panel c.
    """
    heading(ax, "d")
    ax.set_axis_on()
    data = {}
    with READTHROUGH_RESULTS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            data.setdefault(row["group"], []).append(float(row["ratio_1e3"]))
    order = ["A1T5", "M1", "M2"]
    if set(data) != set(order) or any(len(data[name]) != 4 for name in order):
        raise RuntimeError("fig06_readthrough.tsv must hold four wells for A1T5, M1 and M2")
    style = {"A1T5": (INK, "#EAECF0"), "M1": (RED, RED_FACE), "M2": (RED, RED_FACE)}

    for index, name in enumerate(order):
        vals = np.asarray(data[name], dtype=float)
        edge, face = style[name]
        box = ax.boxplot(
            [vals], positions=[index], widths=.40, vert=False, patch_artist=True,
            showfliers=False, zorder=2,
        )
        box["boxes"][0].set(facecolor=face, edgecolor=edge, linewidth=1.0)
        box["medians"][0].set(color=edge, linewidth=1.8)
        for artist in (*box["whiskers"], *box["caps"]):
            artist.set_visible(False)
        # Fixed offsets keep the four wells ordered without random jitter.
        offsets = np.array([-.115, -.038, .038, .115])
        ax.scatter(vals, index + offsets, s=13, facecolor="white",
                   edgecolor=edge, linewidth=.9, zorder=4)

    ax.set_xlim(-0.055, 0.80)
    ax.set_xticks([0, 0.25, 0.5, 0.75])
    ax.set_ylim(len(order) - 0.5, -0.5)
    ax.set_yticks(range(len(order)), order)
    ax.set_xlabel("readthrough ($10^{-3}$)", fontsize=6.0, labelpad=2)
    ax.tick_params(axis="both", labelsize=5.6, length=2, pad=1.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def main():
    setup(); FIGURES.mkdir(parents=True,exist_ok=True)
    fig=plt.figure(figsize=(6.25,5.05),facecolor="white")
    # The two rows have different needs: the D-arm pair wants width, the
    # readthrough boxes need room for the axis and the group names.
    gs = fig.add_gridspec(
        2, 1, height_ratios=[.94, 1.06],
        left=.055, right=.985, top=.955, bottom=.09, hspace=.30,
    )
    gs_top = gs[0, 0].subgridspec(1, 2, width_ratios=[.92, 1.08], wspace=.16)
    gs_bot = gs[1, 0].subgridspec(1, 2, width_ratios=[1.58, .42], wspace=.10)
    aa = fig.add_subplot(gs_top[0, 0]); bb = fig.add_subplot(gs_top[0, 1])
    cc = fig.add_subplot(gs_bot[0, 0]); dd = fig.add_subplot(gs_bot[0, 1])
    for ax in (aa,bb,cc,dd): ax.set_xlim(0,1); ax.set_ylim(0,1)
    panel_a(aa); panel_b(bb); panel_c(cc); panel_d(dd)
    fig.savefig(FIGURES/"fig06_runtime_workbench.pdf",bbox_inches="tight",facecolor="white"); fig.savefig(FIGURES/"fig06_runtime_workbench.png",dpi=600,bbox_inches="tight",facecolor="white"); plt.close(fig)
    print(FIGURES/"fig06_runtime_workbench.pdf"); print(FIGURES/"fig06_runtime_workbench.png")

if __name__=="__main__": main()
