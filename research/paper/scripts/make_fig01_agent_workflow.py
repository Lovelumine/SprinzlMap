#!/usr/bin/env python3
"""Generate Figure 6 as a publication-quality vector workflow schematic."""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, patches


ROOT = Path(__file__).resolve().parents[2]
FIGURES_DIR = ROOT / "paper/figures"

TEXT = "#142033"
SUBTLE_TEXT = "#53677F"
BLUE = "#173B6C"
GREEN = "#138A5A"
ORANGE = "#C96A1D"
PALE = "#F6F9FD"
PALE_LINE = "#D9E3F0"
GRID = "#DCE7F2"
WINDOW_COL0 = 12
WINDOW_COL1 = 14
TABLE_COLS = 18
REGION_COLORS = ["#E6EEF7", "#EEE7F7", "#F8E7E7", "#F7EED2", "#E4F1E8", "#E6EEF7"]
TEMPLATE_TSV = ROOT / "data/tRNAs_flat.tsv"
LAYOUT_TS = ROOT / "src/components/layout/sprinzlLayout.ts"
FALLBACK_TEMPLATE_RECORDS = 169_256
ARIAL_REGULAR_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arial.TTF"
ARIAL_BOLD_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arialbd.TTF"
HEROS_REGULAR_FONT = Path("/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-regular.otf")
HEROS_BOLD_FONT = Path("/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-bold.otf")
DEJAVU_REGULAR_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REGULAR_FONT = next(path for path in (ARIAL_REGULAR_FONT, HEROS_REGULAR_FONT, DEJAVU_REGULAR_FONT) if path.exists())
BOLD_FONT = next(path for path in (ARIAL_BOLD_FONT, HEROS_BOLD_FONT, DEJAVU_BOLD_FONT, REGULAR_FONT) if path.exists())
# Figure 5 uses four text tiers only: panel labels, module titles,
# body/arrow labels, and compact table/helper labels.
PANEL_LABEL_SIZE = 10.0
MODULE_TITLE_SIZE = 5.95
LABEL_SIZE = 4.15
MICRO_LABEL_SIZE = 3.35
ARROW_LABEL_SIZE = LABEL_SIZE
MAIN_ARROW_LW = 0.92
LOOP_ARROW_LW = 0.96
PAIR_LINKS = [
    ("1", "72"),
    ("2", "71"),
    ("3", "70"),
    ("4", "69"),
    ("5", "68"),
    ("6", "67"),
    ("7", "66"),
    ("10", "25"),
    ("11", "24"),
    ("12", "23"),
    ("13", "22"),
    ("27", "43"),
    ("28", "42"),
    ("29", "41"),
    ("30", "40"),
    ("31", "39"),
    ("49", "65"),
    ("50", "64"),
    ("51", "63"),
    ("52", "62"),
]
KEY_ANTICODON = {"34", "35", "36"}
KEY_THREE_PRIME = {"73", "74", "75", "76"}
KEY_VARIABLE = {"V1", "V2", "V3", "V4", "V5", "V11", "V21"}


def load_sprinzl_positions() -> dict[str, tuple[float, float]]:
    text_data = LAYOUT_TS.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(?:"(?P<quoted>[-\w]+)"|(?P<bare>V\d+)):\s*\{\s*x:\s*(?P<x>[-\d.]+),\s*y:\s*(?P<y>[-\d.]+)\s*\}'
    )
    positions: dict[str, tuple[float, float]] = {}
    for match in pattern.finditer(text_data):
        key = match.group("quoted") or match.group("bare")
        positions[key] = (float(match.group("x")), float(match.group("y")))
    if not positions:
        raise RuntimeError(f"No Sprinzl positions parsed from {LAYOUT_TS}")
    return positions


def template_record_count() -> int:
    if not TEMPLATE_TSV.exists():
        return FALLBACK_TEMPLATE_RECORDS
    with TEMPLATE_TSV.open("r", encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def configure_style() -> None:
    for path in (
        ARIAL_REGULAR_FONT,
        ARIAL_BOLD_FONT,
        HEROS_REGULAR_FONT,
        HEROS_BOLD_FONT,
        DEJAVU_REGULAR_FONT,
        DEJAVU_BOLD_FONT,
    ):
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "TeX Gyre Heros", "DejaVu Sans"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.unicode_minus": False,
        }
    )


def rounded(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fc: str = "white",
    ec: str = PALE_LINE,
    lw: float = 0.85,
    r: float = 0.8,
    shadow: bool = False,
    z: int = 5,
) -> None:
    if shadow:
        ax.add_patch(
            patches.FancyBboxPatch(
                (x + 0.55, y - 0.55),
                w,
                h,
                boxstyle=f"round,pad=0,rounding_size={r}",
                facecolor="#AAB6C7",
                edgecolor="none",
                alpha=0.12,
                zorder=z - 2,
            )
        )
    ax.add_patch(
        patches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={r}",
            facecolor=fc,
            edgecolor=ec,
            linewidth=lw,
            zorder=z,
        )
    )


def text(
    ax: plt.Axes,
    x: float,
    y: float,
    s: str,
    *,
    size: float,
    color: str = TEXT,
    weight: str = "regular",
    ha: str = "center",
    va: str = "center",
    z: int = 40,
) -> None:
    font_path = BOLD_FONT if weight == "bold" and BOLD_FONT.exists() else REGULAR_FONT
    font = font_manager.FontProperties(fname=str(font_path), size=size) if font_path.exists() else None
    ax.text(x, y, s, fontproperties=font, color=color, ha=ha, va=va, zorder=z)


def badge(ax: plt.Axes, x: float, y: float, n: int, *, color: str = BLUE, r: float = 1.45) -> None:
    ax.add_patch(patches.Circle((x, y), r, facecolor=color, edgecolor="white", linewidth=0.7, zorder=55))
    fs = 6.0 if r >= 1.2 else 4.0
    text(ax, x, y - 0.02, str(n), size=fs, color="white", weight="bold", z=56)


def panel_label(ax: plt.Axes, x: float, y: float, letter: str) -> None:
    font = font_manager.FontProperties(fname=str(BOLD_FONT), size=PANEL_LABEL_SIZE)
    ax.text(x, y, letter, fontproperties=font, color=TEXT, ha="left", va="bottom", zorder=60)


def arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = BLUE,
    lw: float = MAIN_ARROW_LW,
    label: str | None = None,
    label_xy: tuple[float, float] | None = None,
    z: int = 30,
) -> None:
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops=dict(
            arrowstyle="-|>",
            color=color,
            lw=lw,
            shrinkA=0,
            shrinkB=0,
            mutation_scale=max(5.0, lw * 5.8),
            capstyle="round",
            joinstyle="round",
        ),
        zorder=z,
    )
    if label:
        lx, ly = label_xy or ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + 2.2)
        text(ax, lx, ly, label, size=ARROW_LABEL_SIZE, color=color, weight="regular", z=z + 2)


def elbow_arrow(
    ax: plt.Axes,
    points: list[tuple[float, float]],
    *,
    color: str = ORANGE,
    lw: float = LOOP_ARROW_LW,
    label: str | None = None,
    label_xy: tuple[float, float] | None = None,
) -> None:
    for a, b in zip(points[:-2], points[1:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=color, lw=lw, solid_capstyle="round", zorder=28)
    ax.annotate(
        "",
        xy=points[-1],
        xytext=points[-2],
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, mutation_scale=max(5.8, lw * 6.3), shrinkA=0, shrinkB=0),
        zorder=29,
    )
    if label:
        lx, ly = label_xy or points[1]
        text(ax, lx, ly, label, size=ARROW_LABEL_SIZE, color=color, weight="regular", z=35)


def sequence_strip(ax: plt.Axes, x: float, y: float, w: float, h: float = 2.55) -> None:
    bases = ["G", "C", "G", "G", "G", "U", "A", "G", "...", "G", "U", "C", "C", "A"]
    rounded(ax, x, y, w, h, fc="white", ec=PALE_LINE, lw=0.34, r=0.08, z=12)
    pad = 0.28
    cell_w = (w - 2 * pad) / len(bases)
    for i, base in enumerate(bases):
        cx = x + pad + (i + 0.5) * cell_w
        text(ax, cx, y + h / 2, base, size=MICRO_LABEL_SIZE, color=TEXT, z=13)


def source_chip(ax: plt.Axes, x: float, y: float, label: str, *, color: str) -> None:
    rounded(ax, x, y, 5.4, 2.15, fc="white", ec=color, lw=0.75, r=0.55, z=13)
    text(ax, x + 2.7, y + 1.08, label, size=LABEL_SIZE, color=color, weight="bold", z=14)


def template_library(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    template_records = template_record_count()
    approx_template_k = int(round(template_records / 10_000) * 10)

    bar_x = x + 0.26
    bar_y = y + 0.98
    bar_w = w - 1.58
    bar_h = 3.48

    cols = 8
    rows = 2
    cell_w = bar_w / cols
    cell_h = bar_h / rows
    for dx, dy, alpha, lw in ((1.65, -1.16, 0.58, 0.48), (1.12, -0.78, 0.70, 0.44), (0.58, -0.40, 0.82, 0.40)):
        ax.add_patch(
            patches.Rectangle(
                (bar_x + dx, bar_y + dy),
                bar_w,
                bar_h,
                facecolor="#F8FBFF",
                edgecolor="#C8D7E7",
                linewidth=lw,
                alpha=alpha,
                zorder=11,
            )
        )
        ax.plot([bar_x + dx + 0.42, bar_x + dx + bar_w - 0.42], [bar_y + dy + bar_h * 0.50, bar_y + dy + bar_h * 0.50], color="#D7E2EF", lw=0.28, alpha=alpha, zorder=12)
    column_faces = ["#F4E2D3", "#F4E2D3", "#F4E2D3", "white", "#EAF0F8", "#E3F0EA", "#E3F0EA", "#E3F0EA"]
    for r in range(rows):
        for c in range(cols):
            face = "#F9FBFD" if r == 1 else column_faces[c]
            ax.add_patch(
                patches.Rectangle(
                    (bar_x + c * cell_w, bar_y + r * cell_h),
                    cell_w,
                    cell_h,
                    facecolor=face,
                    edgecolor=GRID,
                    linewidth=0.30,
                    zorder=14,
                )
            )
    ax.add_patch(patches.Rectangle((bar_x, bar_y), bar_w, bar_h, facecolor="none", edgecolor=GREEN, linewidth=0.48, zorder=15))
    numbers = ["34", "35", "36", "...", "73", "74", "75", "76"]
    bases = ["G", "C", "A", "...", "A", "C", "C", "A"]
    for c, label in enumerate(numbers):
        text(ax, bar_x + (c + 0.5) * cell_w, bar_y + cell_h * 1.5, label, size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, z=17)
    for c, base in enumerate(bases):
        text(ax, bar_x + (c + 0.5) * cell_w, bar_y + cell_h * 0.5, base, size=MICRO_LABEL_SIZE, color=BLUE, z=17)
    guide_y = y + h - 0.30
    text(ax, bar_x, guide_y, f"~{approx_template_k}k templates", size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, ha="left", z=17)
    text(ax, bar_x + bar_w, guide_y, "nearest template", size=MICRO_LABEL_SIZE, color=GREEN, ha="right", z=17)
    ax.annotate(
        "",
        xy=(bar_x + bar_w - 6.8, guide_y),
        xytext=(bar_x + 7.1, guide_y),
        arrowprops=dict(arrowstyle="-|>", color=PALE_LINE, lw=0.44, mutation_scale=4.5, shrinkA=0, shrinkB=0),
        zorder=16,
    )


def input_template_source(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    content_x = x + 2.05
    content_w = w - 4.1
    sequence_strip(ax, content_x, y + h - 9.10, content_w, 2.85)
    template_library(ax, content_x, y + 3.15, content_w, 6.05)


def mini_table(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    rows, cols = 3, TABLE_COLS
    cw, rh = w / cols, h / rows
    region_spans = [(0, 3), (3, 6), (6, 9), (9, 11), (11, 15), (15, 18)]
    for (a, b), face in zip(region_spans, REGION_COLORS):
        ax.add_patch(
            patches.Rectangle(
                (x + a * cw, y),
                (b - a) * cw,
                h,
                facecolor=face,
                edgecolor="none",
                alpha=0.88,
                zorder=11,
            )
        )
    for i in range(cols + 1):
        lw = 0.80 if i in (0, cols) else 0.56
        ax.plot([x + i * cw, x + i * cw], [y, y + h], color=GRID, lw=lw, zorder=12)
    for j in range(rows + 1):
        ax.plot([x, x + w], [y + j * rh, y + j * rh], color=GRID, lw=0.68, zorder=12)
    for j, label in enumerate(["Target", "Template", "Coord."]):
        text(ax, x - 0.95, y + (j + 0.5) * rh, label, size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, ha="right", z=13)
    for a, _ in region_spans[1:]:
        xx = x + a * cw
        ax.plot([xx, xx], [y, y + h], color="white", lw=1.18, zorder=13)
    for (a, b), label in zip(region_spans, ["Acc 5'", "D", "Anti", "V", "T", "Acc 3'"]):
        text(ax, x + (a + b) * cw / 2, y + h + 0.78, label, size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, z=13)
    x0 = x + cw * WINDOW_COL0
    x1 = x + cw * WINDOW_COL1
    dash = (0, (2.8, 1.8))
    ax.plot([x0, x1], [y + h, y + h], color=ORANGE, lw=0.82, linestyle=dash, dash_capstyle="butt", zorder=14)
    ax.plot([x0, x1], [y, y], color=ORANGE, lw=0.82, linestyle=dash, dash_capstyle="butt", zorder=14)
    ax.plot([x0, x0], [y, y + h], color=ORANGE, lw=0.82, linestyle=dash, dash_capstyle="butt", zorder=14)
    ax.plot([x1, x1], [y, y + h], color=ORANGE, lw=0.82, linestyle=dash, dash_capstyle="butt", zorder=14)


def mini_trna_scan(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    positions = load_sprinzl_positions()
    ordered_labels = [
        "-1",
        *[str(i) for i in range(1, 18)],
        "17a",
        "18",
        "19",
        "20",
        "20a",
        "20b",
        *[str(i) for i in range(21, 46)],
        "V11",
        "V12",
        "V13",
        "V14",
        "V15",
        "V16",
        "V17",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "V27",
        "V26",
        "V25",
        "V24",
        "V23",
        "V22",
        "V21",
        *[str(i) for i in range(46, 77)],
    ]
    labels = [lab for lab in ordered_labels if lab in positions]
    xs = [positions[lab][0] for lab in labels]
    ys = [positions[lab][1] for lab in labels]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    scale = min(w / (max_x - min_x), h / (max_y - min_y))
    draw_w = (max_x - min_x) * scale
    draw_h = (max_y - min_y) * scale
    ox = x + (w - draw_w) / 2
    oy = y + (h - draw_h) / 2

    def map_pos(label: str) -> tuple[float, float]:
        px, py = positions[label]
        return ox + (px - min_x) * scale, oy + (max_y - py) * scale

    def draw_path(path_labels: list[str], color: str, *, lw: float, alpha: float, z: int) -> None:
        pts = [map_pos(lab) for lab in path_labels if lab in positions]
        if len(pts) < 2:
            return
        ax.plot(
            [pt[0] for pt in pts],
            [pt[1] for pt in pts],
            color=color,
            lw=lw,
            alpha=alpha,
            solid_capstyle="round",
            solid_joinstyle="round",
            zorder=z,
        )

    for left, right in PAIR_LINKS:
        if left in positions and right in positions:
            x1, y1 = map_pos(left)
            x2, y2 = map_pos(right)
            ax.plot([x1, x2], [y1, y2], color="#B8C6D7", lw=0.34, alpha=0.80, linestyle=(0, (1.2, 1.4)), zorder=12)

    pale_points = [map_pos(lab) for lab in labels]
    ax.scatter(
        [pt[0] for pt in pale_points],
        [pt[1] for pt in pale_points],
        s=2.15,
        facecolor="#D2DCE9",
        edgecolor="none",
        alpha=0.80,
        zorder=13,
    )

    def mark_points(path_labels: list[str], color: str, *, size: float = 3.2) -> None:
        pts = [map_pos(lab) for lab in path_labels if lab in positions]
        if not pts:
            return
        ax.scatter(
            [pt[0] for pt in pts],
            [pt[1] for pt in pts],
            s=size,
            facecolor=color,
            edgecolor="white",
            linewidth=0.08,
            zorder=16,
        )

    mark_points(["34", "35", "36"], ORANGE, size=3.25)
    mark_points(["73"], BLUE, size=3.35)
    mark_points(["74", "75", "76"], GREEN, size=3.25)
    mark_points(["V1", "V3", "V5"], ORANGE, size=3.05)


def rule_scan_list(ax: plt.Axes, x: float, y: float) -> None:
    items = [
        ("Order/length", GREEN),
        ("Coord./insert", BLUE),
        ("Anti/CCA", GREEN),
        ("Pairing", BLUE),
        ("Region rules", ORANGE),
    ]
    for i, (label, color) in enumerate(items):
        yy = y - i * 1.58
        ax.add_patch(
            patches.Circle(
                (x, yy),
                0.105,
                facecolor=color,
                edgecolor="none",
                zorder=17,
            )
        )
        text(ax, x + 0.42, yy, label, size=MICRO_LABEL_SIZE, color=BLUE, ha="left", z=17)


def output_summary(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    label_x = x + 0.55
    visual_x = x + 5.20
    visual_w = w - 5.55
    title_y = y + h - 1.05
    grid_y = y + h - 5.28
    grid_h = 2.68
    row2_cy = y + 2.78
    row3_cy = y + 0.98

    for yy in (y + 3.96, y + 1.82):
        ax.plot([x + 0.15, x + w - 0.15], [yy, yy], color=PALE_LINE, lw=0.32, alpha=0.80, zorder=12)

    cols = ["34", "35", "36", "...", "73", "74", "75", "76"]
    bases = ["G", "C", "A", "...", "A", "C", "C", "A"]
    text(ax, label_x, title_y, "Sprinzl coordinate table", size=MICRO_LABEL_SIZE, color=GREEN, ha="left", z=15)
    grid_x = x + 0.92
    grid_w = w - 1.84
    cw = grid_w / len(cols)
    rh = grid_h / 2
    for r, row in enumerate((cols, bases)):
        for c, value in enumerate(row):
            if r == 0:
                face = "#F8FBFF"
                color = SUBTLE_TEXT
            elif c < 3:
                face = "#F4E2D3"
                color = BLUE
            elif c >= 4:
                face = "#E3F0EA"
                color = BLUE
            else:
                face = "white"
                color = SUBTLE_TEXT
            ax.add_patch(
                patches.Rectangle(
                    (grid_x + c * cw, grid_y + (1 - r) * rh),
                    cw,
                    rh,
                    facecolor=face,
                    edgecolor=GRID,
                    linewidth=0.28,
                    zorder=13,
                )
            )
            text(ax, grid_x + (c + 0.5) * cw, grid_y + (1.5 - r) * rh, value, size=MICRO_LABEL_SIZE, color=color, z=14)

    text(ax, label_x, row2_cy, "Warnings", size=MICRO_LABEL_SIZE, color=ORANGE, ha="left", z=14)
    ax.plot([visual_x + 0.25, x + w - 0.35], [row2_cy + 0.16, row2_cy + 0.16], color=ORANGE, lw=0.42, alpha=0.72, zorder=13)
    ax.plot([visual_x + 0.25, visual_x + visual_w * 0.62], [row2_cy - 0.36, row2_cy - 0.36], color=PALE_LINE, lw=0.38, zorder=13)

    text(ax, label_x, row3_cy, "Trace", size=MICRO_LABEL_SIZE, color=BLUE, ha="left", z=14)
    ax.plot([visual_x + 0.25, x + w - 0.35], [row3_cy + 0.16, row3_cy + 0.16], color=BLUE, lw=0.42, alpha=0.72, zorder=13)
    ax.plot([visual_x + 0.25, visual_x + visual_w * 0.70], [row3_cy - 0.36, row3_cy - 0.36], color=PALE_LINE, lw=0.38, zorder=13)


def top_module(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    *,
    color: str = BLUE,
) -> None:
    rounded(ax, x, y, w, h, ec=color, lw=0.68, r=0.85, shadow=False)
    text(ax, x + 3.4, y + h - 3.1, title, size=MODULE_TITLE_SIZE, color=color, weight="bold", ha="left")


def draw_top_flow(ax: plt.Axes) -> None:
    rounded(ax, 7, 49.5, 146, 31.0, fc=PALE, ec=PALE_LINE, lw=0.55, r=0.95, z=0)
    panel_label(ax, 10.8, 76.75, "A")

    card_w, card_h, y = 27.0, 20.0, 53.4
    xs = [13.0, 49.7, 86.4, 123.1]
    titles = ["Input & templates", "Candidate table", "Rule scan", "Output"]
    for i, (x, title) in enumerate(zip(xs, titles), start=1):
        top_module(ax, x, y, card_w, card_h, title, color=GREEN if i == 4 else BLUE)

    input_template_source(ax, xs[0], y, card_w, card_h)

    table_label_w = 3.35
    table_w = 17.40
    table_x = xs[1] + (card_w - table_label_w - table_w) / 2 + table_label_w
    table_y = y + 5.20
    table_h = 8.10
    mini_table(ax, table_x, table_y, table_w, table_h)
    text(
        ax,
        table_x + table_w * (WINDOW_COL0 + WINDOW_COL1) / (2 * TABLE_COLS),
        y + 2.15,
        "local window",
        size=LABEL_SIZE,
        color=ORANGE,
        weight="regular",
        z=13,
    )

    mini_trna_scan(ax, xs[2] + 2.35, y + 2.85, 13.20, 10.10)
    rule_scan_list(ax, xs[2] + 18.35, y + 12.05)

    output_summary(ax, xs[3] + 2.95, y + 3.55, card_w - 5.90, 10.80)

    arrow(
        ax,
        (xs[0] + card_w + 1.3, y + card_h / 2),
        (xs[1] - 1.3, y + card_h / 2),
        color=BLUE,
        label="Segmented\nalignment",
        label_xy=((xs[0] + card_w + xs[1]) / 2 + 0.65, y + card_h / 2 - 3.0),
    )
    arrow(ax, (xs[1] + card_w + 3.0, y + card_h / 2), (xs[2] - 3.0, y + card_h / 2), color=BLUE)
    arrow(
        ax,
        (xs[2] + card_w + 3.0, y + card_h / 2),
        (xs[3] - 3.0, y + card_h / 2),
        color=GREEN,
        label="pass",
        label_xy=(118.8, y + card_h / 2 + 2.7),
    )

    elbow_arrow(
        ax,
        [(xs[2] + card_w / 2, y), (xs[2] + card_w / 2, 47.4), (28.5, 47.4), (28.5, 35.6)],
        color=ORANGE,
    )
    text(ax, 66.9, 45.55, "failed: extract local window", size=ARROW_LABEL_SIZE, color=ORANGE, weight="regular", z=35)


def local_context(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    rounded(ax, x, y, w, h, ec=ORANGE, lw=0.68, r=0.85, shadow=False)
    text(ax, x + 3.4, y + h - 3.3, "Local conflict window", size=MODULE_TITLE_SIZE, color=ORANGE, weight="bold", ha="left")

    positions = load_sprinzl_positions()
    region_labels = [
        "49",
        "50",
        "51",
        "52",
        "53",
        "54",
        "55",
        "56",
        "57",
        "58",
        "59",
        "60",
        "61",
        "62",
        "63",
        "64",
        "65",
    ]
    region_labels = [lab for lab in region_labels if lab in positions]
    xs = [positions[lab][0] for lab in region_labels]
    ys_raw = [positions[lab][1] for lab in region_labels]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys_raw), max(ys_raw)
    draw_x, draw_y, draw_w, draw_h = x + 5.3, y + 5.85, 20.2, 10.0
    scale = min(draw_w / (max_x - min_x), draw_h / (max_y - min_y))
    actual_w = (max_x - min_x) * scale
    actual_h = (max_y - min_y) * scale
    ox = draw_x + (draw_w - actual_w) / 2
    oy = draw_y + (draw_h - actual_h) / 2

    def map_region(label: str) -> tuple[float, float]:
        px, py = positions[label]
        return ox + (px - min_x) * scale, oy + (max_y - py) * scale

    pts = [map_region(label) for label in region_labels]
    ax.plot(
        [pt[0] for pt in pts],
        [pt[1] for pt in pts],
        color=PALE_LINE,
        lw=0.58,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=12,
    )
    for left, right in [("49", "65"), ("50", "64"), ("51", "63"), ("52", "62")]:
        x1, y1 = map_region(left)
        x2, y2 = map_region(right)
        ax.plot([x1, x2], [y1, y2], color=PALE_LINE, lw=0.36, alpha=0.72, zorder=13)

    def insertion_axis_point(parent: str, next_label: str, rank: int) -> tuple[float, float]:
        x_parent, y_parent = map_region(parent)
        x_next, y_next = map_region(next_label)
        dx = x_next - x_parent
        dy = y_next - y_parent
        dist = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux = -dy / dist
        uy = dx / dist
        if uy < 0:
            ux, uy = -ux, -uy
        xm = (x_parent + x_next) / 2
        ym = (y_parent + y_next) / 2
        return xm + ux * 0.86 * rank, ym + uy * 0.86 * rank

    insertion_pts = {f"64i{rank}": insertion_axis_point("64", "65", rank) for rank in range(1, 4)}
    x64, y64 = map_region("64")
    x65, y65 = map_region("65")
    first_x, first_y = insertion_pts["64i1"]
    ax.plot([(x64 + x65) / 2, first_x], [(y64 + y65) / 2, first_y], color=SUBTLE_TEXT, lw=0.34, alpha=0.55, linestyle=(0, (1.2, 1.6)), zorder=13)

    target_points = {label: map_region(label) for label in region_labels}
    target_points.update(insertion_pts)
    for left, right in [("52", "65"), ("53", "64i3"), ("54", "64i2"), ("57", "64i1")]:
        if left in target_points and right in target_points:
            x1, y1 = target_points[left]
            x2, y2 = target_points[right]
            ax.plot([x1, x2], [y1, y2], color=ORANGE, lw=0.56, alpha=0.82, zorder=14)

    node_r = 0.42
    for label in region_labels:
        cx, cy = map_region(label)
        ax.add_patch(
            patches.Circle(
                (cx, cy),
                node_r * 0.74,
                facecolor="white",
                edgecolor=PALE_LINE,
                linewidth=0.42,
                zorder=15,
            )
        )

    for cx, cy in insertion_pts.values():
        ax.add_patch(
            patches.Circle(
                (cx, cy),
                node_r,
                facecolor="#FFF7ED",
                edgecolor=ORANGE,
                linewidth=0.45,
                zorder=18,
            )
        )

    text(ax, x + 6.05, y + 3.25, "insertions", size=MICRO_LABEL_SIZE, color=ORANGE, ha="left", z=14)
    text(ax, x + 16.35, y + 3.25, "shifted pairs", size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, ha="left", z=14)


def tile_icon(ax: plt.Axes, kind: str, cx: float, cy: float, s: float, *, color: str = BLUE) -> None:
    lw = 0.48
    pale_lw = 0.36
    node_r = 0.042 * s

    def line(points: list[tuple[float, float]], *, c: str = BLUE, width: float = lw, alpha: float = 1.0, z: int = 20) -> None:
        ax.plot(
            [p[0] for p in points],
            [p[1] for p in points],
            color=c,
            lw=width,
            alpha=alpha,
            solid_capstyle="round",
            solid_joinstyle="round",
            zorder=z,
        )

    def node(px: float, py: float, *, c: str = BLUE, fill: str = "white", r: float = node_r, z: int = 21) -> None:
        ax.add_patch(patches.Circle((px, py), r, facecolor=fill, edgecolor=c, linewidth=lw, zorder=z))

    def tiny_arrow(a: tuple[float, float], b: tuple[float, float], *, c: str = BLUE) -> None:
        ax.add_patch(
            patches.FancyArrowPatch(
                a,
                b,
                arrowstyle="-|>",
                mutation_scale=3.2,
                linewidth=lw,
                color=c,
                shrinkA=0,
                shrinkB=0,
                zorder=22,
            )
        )

    def pale_window() -> None:
        xs = [cx - 0.36 * s, cx - 0.18 * s, cx, cx + 0.18 * s, cx + 0.36 * s]
        for px in xs:
            line([(px, cy - 0.22 * s), (px, cy + 0.18 * s)], c=PALE_LINE, width=pale_lw, alpha=0.88, z=18)
        line([(xs[0], cy - 0.22 * s), (xs[-1], cy - 0.22 * s)], c=PALE_LINE, width=pale_lw, alpha=0.88, z=18)
        line([(xs[0], cy - 0.02 * s), (xs[-1], cy - 0.02 * s)], c=PALE_LINE, width=pale_lw, alpha=0.88, z=18)
        line([(xs[0], cy + 0.18 * s), (xs[-1], cy + 0.18 * s)], c=PALE_LINE, width=pale_lw, alpha=0.88, z=18)

    if kind == "template":
        row_w = 0.62 * s
        row_h = 0.105 * s
        for i, yy in enumerate([cy + 0.20 * s, cy, cy - 0.20 * s]):
            ec = GREEN if i == 1 else PALE_LINE
            fc = "#ECF8F2" if i == 1 else "white"
            ax.add_patch(
                patches.Rectangle(
                    (cx - row_w / 2, yy - row_h / 2),
                    row_w,
                    row_h,
                    facecolor=fc,
                    edgecolor=ec,
                    linewidth=lw if i == 1 else pale_lw,
                    zorder=20,
                )
            )
            for k in range(3):
                x0 = cx - row_w * 0.34 + k * row_w * 0.27
                line([(x0, yy), (x0 + row_w * 0.15, yy)], c=ec if i == 1 else PALE_LINE, width=pale_lw, z=21)
        tiny_arrow((cx + row_w * 0.58, cy), (cx + row_w * 0.80, cy), c=GREEN)
    elif kind == "boundary":
        pale_window()
        line([(cx - 0.08 * s, cy - 0.25 * s), (cx - 0.08 * s, cy + 0.22 * s)], c=ORANGE, width=lw, z=21)
        line([(cx + 0.08 * s, cy - 0.25 * s), (cx + 0.08 * s, cy + 0.22 * s)], c=ORANGE, width=lw, z=21)
        tiny_arrow((cx - 0.31 * s, cy + 0.30 * s), (cx - 0.12 * s, cy + 0.30 * s), c=ORANGE)
        tiny_arrow((cx + 0.31 * s, cy + 0.30 * s), (cx + 0.12 * s, cy + 0.30 * s), c=ORANGE)
    elif kind == "structure":
        for px in [cx - 0.27 * s, cx - 0.09 * s, cx + 0.09 * s, cx + 0.27 * s]:
            node(px, cy + 0.17 * s, c=BLUE)
            node(px, cy - 0.18 * s, c=BLUE)
            line([(px, cy + 0.10 * s), (px, cy - 0.11 * s)], c=PALE_LINE, width=pale_lw, z=19)
        line([(cx - 0.32 * s, cy + 0.27 * s), (cx + 0.32 * s, cy + 0.27 * s)], c=ORANGE, width=lw, z=21)
    elif kind == "prior":
        pale_window()
        line([(cx - 0.36 * s, cy + 0.28 * s), (cx + 0.36 * s, cy + 0.28 * s)], c=GREEN, width=lw, z=21)
        tiny_arrow((cx + 0.02 * s, cy + 0.28 * s), (cx + 0.02 * s, cy + 0.08 * s), c=GREEN)
        line([(cx - 0.30 * s, cy - 0.18 * s), (cx - 0.06 * s, cy - 0.02 * s), (cx + 0.16 * s, cy - 0.02 * s), (cx + 0.34 * s, cy + 0.14 * s)], c=BLUE, width=lw, z=21)
    elif kind == "dp":
        pale_window()
        line(
            [(cx - 0.32 * s, cy - 0.18 * s), (cx - 0.12 * s, cy - 0.02 * s), (cx + 0.08 * s, cy - 0.02 * s), (cx + 0.32 * s, cy + 0.18 * s)],
            c=BLUE,
            width=lw,
            z=21,
        )
    elif kind == "variable":
        positions = load_sprinzl_positions()
        variable_path = [
            "44",
            "45",
            "V11",
            "V12",
            "V13",
            "V14",
            "V15",
            "V16",
            "V17",
            "V1",
            "V2",
            "V3",
            "V4",
            "V5",
            "V27",
            "V26",
            "V25",
            "V24",
            "V23",
            "V22",
            "V21",
            "46",
            "47",
            "48",
        ]
        labels = [label for label in variable_path if label in positions]
        xs = [positions[label][0] for label in labels]
        ys = [positions[label][1] for label in labels]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        scale = min(1.04 * s / (max_x - min_x), 0.88 * s / (max_y - min_y))
        draw_w = (max_x - min_x) * scale
        draw_h = (max_y - min_y) * scale
        ox = cx - draw_w / 2
        oy = cy - draw_h / 2

        def map_var(label: str) -> tuple[float, float]:
            px, py = positions[label]
            return ox + (px - min_x) * scale, oy + (max_y - py) * scale

        mapped = [map_var(label) for label in labels]
        line(mapped, c=PALE_LINE, width=pale_lw, alpha=0.95, z=18)
        for left, right in [
            ("V11", "V21"),
            ("V12", "V22"),
            ("V13", "V23"),
            ("V14", "V24"),
            ("V15", "V25"),
            ("V16", "V26"),
            ("V17", "V27"),
        ]:
            if left in positions and right in positions:
                line([map_var(left), map_var(right)], c=PALE_LINE, width=pale_lw, alpha=0.78, z=18)
        for label in labels:
            px, py = map_var(label)
            node(px, py, c=PALE_LINE, fill="white", r=0.027 * s, z=19)
        for left, right in [("V12", "V22"), ("V13", "V23"), ("V14", "V24")]:
            if left in positions and right in positions:
                line([map_var(left), map_var(right)], c=GREEN, width=lw * 0.86, alpha=0.95, z=21)
        for label in ["V1", "V2", "V3", "V4", "V5", "44", "45", "46", "47", "48"]:
            if label in positions:
                px, py = map_var(label)
                accent = GREEN if label.startswith("V") else BLUE
                node(px, py, c=accent, fill="white", r=0.033 * s, z=22)
    elif kind == "anticodon":
        line([(cx - 0.34 * s, cy + 0.16 * s), (cx + 0.34 * s, cy + 0.16 * s)], c=PALE_LINE, width=pale_lw, z=18)
        for px in [cx - 0.18 * s, cx, cx + 0.18 * s]:
            line([(px, cy + 0.16 * s), (px, cy - 0.05 * s)], c=ORANGE, width=lw, z=20)
            node(px, cy - 0.12 * s, c=ORANGE, fill="#FFF7ED", r=0.052 * s)
        line([(cx - 0.28 * s, cy - 0.30 * s), (cx + 0.28 * s, cy - 0.30 * s)], c=BLUE, width=lw, z=21)
    elif kind == "compact":
        line([(cx - 0.38 * s, cy + 0.12 * s), (cx + 0.38 * s, cy + 0.12 * s)], c=PALE_LINE, width=pale_lw, z=18)
        for px in [cx - 0.26 * s, cx, cx + 0.26 * s]:
            node(px, cy + 0.12 * s, c=BLUE, r=0.040 * s)
        for px in [cx - 0.12 * s, cx + 0.12 * s]:
            node(px, cy - 0.14 * s, c=ORANGE, fill="#FFF7ED", r=0.045 * s)
        tiny_arrow((cx - 0.12 * s, cy - 0.07 * s), (cx - 0.03 * s, cy + 0.07 * s), c=ORANGE)
        tiny_arrow((cx + 0.12 * s, cy - 0.07 * s), (cx + 0.03 * s, cy + 0.07 * s), c=ORANGE)
    elif kind == "route":
        pts = [(cx - 0.36 * s, cy), (cx, cy), (cx + 0.34 * s, cy + 0.22 * s), (cx + 0.34 * s, cy - 0.22 * s)]
        line([pts[0], pts[1], pts[2]], c=GREEN, width=lw, z=20)
        line([pts[1], pts[3]], c=GREEN, width=lw, z=20)
        for px, py in pts:
            node(px, py, c=GREEN, fill="white")


def action_library(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    rounded(ax, x, y, w, h, ec=BLUE, lw=0.72, r=0.85, shadow=False)
    text(ax, x + w / 2, y + h - 3.0, "Agent Action Library", size=MODULE_TITLE_SIZE, color=BLUE, weight="bold")
    text(ax, x + w / 2, y + h - 5.4, "Planner and specialists propose bounded candidates", size=LABEL_SIZE, color=SUBTLE_TEXT)

    items = [
        ("Template\nrerank", "template", BLUE),
        ("Boundary\nrefine", "boundary", ORANGE),
        ("Structure-first\ncheck", "structure", ORANGE),
        ("Template-guided\ncheck", "prior", BLUE),
        ("Segment DP\nrerun", "dp", BLUE),
        ("Variable-region\ncleanup", "variable", BLUE),
        ("Anticodon\n34-36", "anticodon", BLUE),
        ("Insertion\ncleanup", "compact", ORANGE),
        ("Case\nrouting", "route", GREEN),
    ]
    gap = 1.55
    tile_w = (w - 8.0 - 2 * gap) / 3
    tile_h = (h - 9.6 - 2 * gap) / 3
    x0 = x + 4.0
    y0 = y + 2.75
    for idx, (title, kind, color) in enumerate(items, start=1):
        row = 2 - (idx - 1) // 3
        col = (idx - 1) % 3
        tx = x0 + col * (tile_w + gap)
        ty = y0 + row * (tile_h + gap)
        rounded(ax, tx, ty, tile_w, tile_h, fc="white", ec=PALE_LINE, lw=0.45, r=0.35, shadow=False, z=14)
        icon_y = ty + tile_h * (0.68 if kind == "variable" else 0.60)
        icon_s = 3.05 if kind == "variable" else 3.5
        tile_icon(ax, kind, tx + tile_w / 2, icon_y, icon_s, color=color)
        text(ax, tx + tile_w / 2, ty + 1.20, title, size=LABEL_SIZE, color=TEXT, weight="regular")


def validation(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    rounded(ax, x, y, w, h, ec=GREEN, lw=0.68, r=0.85, shadow=False)
    text(ax, x + 3.4, y + h - 3.3, "Local validation", size=MODULE_TITLE_SIZE, color=GREEN, weight="bold", ha="left")
    text(ax, x + 3.45, y + h - 5.25, "merge candidate, then recheck", size=MICRO_LABEL_SIZE, color=SUBTLE_TEXT, ha="left")

    rows = [
        ("Global rules", ("order", "length", "coord.")),
        ("Region rules", ("insert", "pairing", "anti/CCA")),
    ]
    for row, (group, labels) in enumerate(rows):
        yy = y + h - 8.4 - row * 4.55
        ax.plot([x + 3.7, x + 5.0], [yy, yy], color=GREEN, lw=0.82, solid_capstyle="round", zorder=16)
        text(ax, x + 5.55, yy, group, size=MICRO_LABEL_SIZE, color=TEXT, ha="left")
        lx = x + 4.1
        for col, lab in enumerate(labels):
            px = lx + col * 6.2
            ax.add_patch(patches.Circle((px, yy - 2.05), 0.20, facecolor=GREEN, edgecolor="none", zorder=16))
            text(ax, px + 0.55, yy - 2.05, lab, size=MICRO_LABEL_SIZE, color=TEXT, ha="left")


def draw_bottom_loop(ax: plt.Axes) -> None:
    rounded(ax, 7, 5.4, 146, 38.6, fc=PALE, ec=PALE_LINE, lw=0.55, r=0.95, z=0)
    panel_label(ax, 10.8, 40.65, "B")

    local_context(ax, 13, 15.0, 31, 20.6)
    action_library(ax, 52.0, 7.9, 58.5, 33.4)
    validation(ax, 121.0, 15.0, 26, 20.6)

    arrow(ax, (45.4, 25.3), (50.0, 25.3), color=BLUE, lw=LOOP_ARROW_LW)
    arrow(ax, (112.0, 25.3), (119.0, 25.3), color=BLUE, lw=LOOP_ARROW_LW, label="candidate", label_xy=(115.4, 27.5))
    elbow_arrow(
        ax,
        [(134.0, 35.8), (134.0, 45.7), (114.0, 45.7), (114.0, 53.4)],
        color=GREEN,
        lw=0.72,
        label="accepted: merge & rescan",
        label_xy=(124.0, 47.6),
    )
    arrow(ax, (120.0, 18.5), (112.0, 18.5), color=ORANGE, lw=0.72, label="reject", label_xy=(116.0, 20.4), z=33)


def main() -> None:
    configure_style()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(7.2, 4.55))
    ax.set_xlim(0, 160)
    ax.set_ylim(0, 90)
    ax.axis("off")

    draw_top_flow(ax)
    draw_bottom_loop(ax)

    fig.savefig(FIGURES_DIR / "fig01_agent_workflow.pdf", bbox_inches="tight", pad_inches=0.04)
    fig.savefig(FIGURES_DIR / "fig01_agent_workflow.svg", bbox_inches="tight", pad_inches=0.04)
    fig.savefig(FIGURES_DIR / "fig01_agent_workflow.png", dpi=900, bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)
    print("wrote=paper/figures/fig01_agent_workflow.pdf")


if __name__ == "__main__":
    main()
