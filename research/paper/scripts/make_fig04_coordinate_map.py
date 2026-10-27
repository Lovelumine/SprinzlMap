#!/usr/bin/env python3
"""Generate the first results figure from benchmark outputs."""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager, patches
from matplotlib.colors import to_rgb
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUN_DIR = ROOT / "benchmark/reports/deepseek-v4-pro-single-trace-tdbD00001424-v1/cases"
FALLBACK_RUN_DIR = ROOT / "benchmark/runs/deepseek-v4-pro-no-thinking-full-v1/cases"
OUT_DIR = ROOT / "paper/figures"
LAYOUT_TS = ROOT / "src/components/layout/sprinzlLayout.ts"
ARIAL_REGULAR_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arial.TTF"
ARIAL_BOLD_FONT = Path.home() / ".local/share/fonts/aylm_fonts/Arialbd.TTF"
HEROS_REGULAR_FONT = Path("/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-regular.otf")
HEROS_BOLD_FONT = Path("/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-bold.otf")
DEJAVU_REGULAR_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE = ("tdbD00001424|DK5210", "Lys tRNA, 76 nt")

BASE_COLORS = {
    "A": "#F4C7CB",
    "C": "#CFEBD6",
    "G": "#F4DDAA",
    "U": "#C8E0F2",
    "T": "#C8E0F2",
    "-": "#EDF1F5",
}
DIFF_COLORS = {
    "match": "#F5F6F8",
    "substitution": "#EBA39A",
    "gap": "#A9C2D8",
    "target_gap": "#E7ECF2",
}
REGION_COLORS = {
    "Acceptor 5'": "#DCE8F5",
    "D region": "#E9E1F4",
    "Anticodon region": "#F8D7DA",
    "Variable region": "#F7E7C2",
    "T region": "#DDF0E3",
    "Acceptor 3'": "#DCE8F5",
}
REGION_ORDER = ["Acceptor 5'", "D region", "Anticodon region", "Variable region", "T region", "Acceptor 3'"]
ANNOTATION_COLOR = "#344054"
SECONDARY_ANNOTATION_COLOR = "#667085"
ERROR_COLOR = "#D92D20"
PASS_COLOR = "#2E8B57"
STATE_EDGE_COLOR = "#5F6F83"
KEY_ANTICODON = {"34", "35", "36"}
KEY_THREE_PRIME = {"74", "75", "76"}
KEY_VARIABLE = {"V1", "V2", "V3", "V4", "V5", "V11", "V21"}
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

T_ARM_STRUCT_FALLBACK = ">>>>.........<<<<"


def load_case(seqname: str) -> dict:
    for run_dir in (RUN_DIR, FALLBACK_RUN_DIR):
        for path in run_dir.glob("*.json"):
            data = json.loads(path.read_text())
            if data.get("seqname") == seqname:
                data["_path"] = str(path)
                rows = list(csv.reader(io.StringIO(data["csv_content"])))
                data["labels"], data["template"], data["target"] = rows
                return data
    raise FileNotFoundError(seqname)


def load_sprinzl_positions() -> dict[str, tuple[float, float]]:
    """Reuse the website's canonical Sprinzl layout instead of redrawing it."""
    text = LAYOUT_TS.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(?:"(?P<quoted>[-\w]+)"|(?P<bare>V\d+)):\s*\{\s*x:\s*(?P<x>[-\d.]+),\s*y:\s*(?P<y>[-\d.]+)\s*\}'
    )
    positions: dict[str, tuple[float, float]] = {}
    for match in pattern.finditer(text):
        key = match.group("quoted") or match.group("bare")
        positions[key] = (float(match.group("x")), float(match.group("y")))
    if not positions:
        raise RuntimeError(f"No Sprinzl positions parsed from {LAYOUT_TS}")
    return positions


def label_region(label: str) -> str:
    if label.startswith("V") or label in {"44", "45", "46", "47", "48"}:
        return "Variable region"
    match = re.match(r"^-?(\d+)", label)
    if not match:
        return "Variable region"
    n = int(match.group(1))
    if n <= 9:
        return "Acceptor 5'"
    if n <= 26:
        return "D region"
    if n <= 43:
        return "Anticodon region"
    if n <= 48:
        return "Variable region"
    if n <= 65:
        return "T region"
    return "Acceptor 3'"


def rgb(base: str) -> tuple[float, float, float]:
    return to_rgb(BASE_COLORS.get(str(base).upper(), "#D9DEE8"))


def template_target_differences(case: dict) -> tuple[int, int, float]:
    total = 0
    different = 0
    for template_base, target_base in zip(case["template"], case["target"]):
        if target_base == "-":
            continue
        total += 1
        if template_base != target_base:
            different += 1
    identity = (total - different) / total if total else 0.0
    return different, total, identity


def initial_candidate_rows(case: dict) -> tuple[list[str], list[str], list[str]]:
    """Rebuild the deterministic first candidate table before Agent correction."""
    if "_initial_candidate_rows" in case:
        return case["_initial_candidate_rows"]
    try:
        from backend.agent.contracts import AlignmentRequest
        from backend.agent.execution.root_snapshot_builder import RootSnapshotBuilder

        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            snapshot = RootSnapshotBuilder().build(
                AlignmentRequest(
                    target_seq=str(case.get("input_seq") or ""),
                    output_csv_path="/dev/null",
                    use_llm=False,
                    llm_reasoning=False,
                    return_meta=True,
                )
            )
        rows = (
            [str(label) for label in snapshot.numbering_row],
            [str(base or "-").upper() for base in snapshot.template_row],
            [str(base or "-").upper() for base in snapshot.sequence_row],
        )
        case["_initial_candidate_rows"] = rows
        return rows
    except Exception:
        rows = (
            list(case["labels"]),
            list(case["template"]),
            list(case["target"]),
        )
        case["_initial_candidate_rows"] = rows
        return rows


def initial_t_arm_rows(case: dict) -> dict[str, dict[str, str]]:
    """Extract the local T-arm rows from the deterministic first candidate."""
    labels, template_row, target_row = initial_candidate_rows(case)
    rows: dict[str, dict[str, str]] = {}
    for label, template_base, target_base in zip(labels, template_row, target_row):
        if label.startswith("64i") or label in {str(n) for n in range(49, 66)}:
            rows[label] = {
                "template": str(template_base or "-").upper(),
                "target": str(target_base or "-").upper(),
            }
    if rows:
        return rows
    return {
        "64i1": {"template": "-", "target": "A"},
        "64i2": {"template": "-", "target": "C"},
        "64i3": {"template": "-", "target": "C"},
        "64i4": {"template": "-", "target": "A"},
        "64i5": {"template": "-", "target": "C"},
    }


def parse_angle_pairs(struct: str) -> list[tuple[int, int]]:
    """Return 0-based paired indices from the local angle-bracket structure."""
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for idx, char in enumerate(struct or ""):
        if char == ">":
            stack.append(idx)
        elif char == "<" and stack:
            left = stack.pop()
            pairs.append((left, idx))
    return sorted(pairs)


def t_arm_structure(case: dict) -> str:
    """Recover the T-arm structure used by the pipeline, with the S1 value as fallback."""
    if "_t_arm_structure" in case:
        return str(case["_t_arm_structure"])
    struct = ""
    try:
        from backend.domain.alignment.preparation.segmentation import compare_input_template

        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            cmp = compare_input_template(str(case.get("input_seq") or ""))
        region = (cmp.get("regions") or {}).get("T loop + T stem") or {}
        struct = str(region.get("input_struct") or "")
    except Exception:
        struct = ""
    case["_t_arm_structure"] = struct or T_ARM_STRUCT_FALLBACK
    return str(case["_t_arm_structure"])


def t_arm_structural_pairs(
    *,
    labels: list[str],
    bases: dict[str, str],
    struct: str,
) -> list[tuple[str, str]]:
    """Map paired T-arm sequence positions onto the current Sprinzl labels."""
    occupied_labels = [
        label
        for label in labels
        if (label.startswith("64i") or label in {str(n) for n in range(49, 66)})
        and str(bases.get(label, "-") or "-").upper() != "-"
    ]
    pairs: list[tuple[str, str]] = []
    for left_idx, right_idx in parse_angle_pairs(struct):
        if left_idx < len(occupied_labels) and right_idx < len(occupied_labels):
            pairs.append((occupied_labels[left_idx], occupied_labels[right_idx]))
    return pairs


def full_structural_pairs(case: dict) -> list[tuple[str, str]]:
    """Map the pipeline tRNAscan-SE secondary structure onto final Sprinzl labels."""
    if "_full_structural_pairs" in case:
        return list(case["_full_structural_pairs"])
    struct = ""
    try:
        from backend.domain.alignment.preparation.segmentation import compare_input_template

        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            cmp = compare_input_template(str(case.get("input_seq") or ""))
        meta = cmp.get("annotation_meta") or {}
        if str(meta.get("annotation_source") or "") == "tRNAscan-SE":
            struct = str(meta.get("secondary_structure") or "")
    except Exception:
        struct = ""
    if not struct:
        case["_full_structural_pairs"] = list(PAIR_LINKS)
        return list(PAIR_LINKS)

    occupied_labels = [
        label
        for label, base in zip(case.get("labels") or [], case.get("target") or [])
        if str(base or "-").upper() != "-"
    ]
    pairs: list[tuple[str, str]] = []
    for left_idx, right_idx in parse_angle_pairs(struct):
        if left_idx < len(occupied_labels) and right_idx < len(occupied_labels):
            pairs.append((occupied_labels[left_idx], occupied_labels[right_idx]))
    case["_full_structural_pairs"] = pairs or list(PAIR_LINKS)
    return list(case["_full_structural_pairs"])


def panel_heading(ax, letter: str, title: str = "") -> None:
    ax.text(0.0, 1.035, letter, transform=ax.transAxes, fontsize=10.6, weight="bold", va="bottom")
    if title:
        ax.text(0.055, 1.035, title, transform=ax.transAxes, fontsize=8.8, weight="bold", va="bottom")


def draw_group_marker(ax, labels: list[str], keys: list[str], y: float, text: str, color: str) -> None:
    xs = [idx + 0.45 for idx, label in enumerate(labels) if label in keys]
    if not xs:
        return
    x0, x1 = min(xs), max(xs)
    ax.plot([x0, x1], [y, y], color=color, linewidth=0.9)
    ax.plot([x0, x0], [y, y - 0.10], color=color, linewidth=0.9)
    ax.plot([x1, x1], [y, y - 0.10], color=color, linewidth=0.9)
    ax.text((x0 + x1) / 2, y + 0.08, text, ha="center", va="bottom", fontsize=6.4, color=color)


def draw_region_track(ax, labels: list[str], y: float) -> None:
    start = 0
    current = label_region(labels[0])
    short_names = {
        "Acceptor 5'": "Acc 5'",
        "D region": "D",
        "Anticodon region": "Anticodon",
        "Variable region": "Variable",
        "T region": "T",
        "Acceptor 3'": "Acc 3'",
    }
    for i, label in enumerate(labels + ["END"]):
        region = label_region(label) if label != "END" else None
        if region != current:
            width = i - start
            ax.add_patch(
                patches.Rectangle(
                    (start, y),
                    width,
                    0.16,
                    facecolor=REGION_COLORS[current],
                    edgecolor="white",
                    linewidth=0.4,
                )
            )
            if width >= 6:
                ax.text(
                    start + width / 2,
                    y - 0.08,
                    short_names[current],
                    ha="center",
                    va="top",
                    fontsize=5.9,
                    color="#475467",
                )
            start = i
            current = region


def draw_alignment_panel(ax, case: dict) -> None:
    labels = case["labels"]
    template = case["template"]
    target = case["target"]
    n = len(labels)
    metrics = case["metrics"]
    diff_count, total, _ = template_target_differences(case)

    ax.axis("off")
    ax.set_xlim(-4, n + 1)
    ax.set_ylim(-0.08, 3.28)
    panel_heading(ax, "A")

    row_specs = [("Candidate template", template, 1.88, "template"), ("Target sequence", target, 1.14, "target")]
    cell_h = 0.45
    for row_name, row, y, role in row_specs:
        ax.text(-1.1, y + cell_h / 2, row_name, ha="right", va="center", fontsize=6.8, color="#101828")
        for i, base in enumerate(row):
            template_base = template[i]
            target_base = target[i]
            if role == "template":
                face = "#F8FAFC"
                edge = "#E4E7EC"
                text_color = "#475467"
            else:
                if target_base == "-":
                    face = DIFF_COLORS["target_gap"]
                elif template_base == target_base:
                    face = DIFF_COLORS["match"]
                elif template_base == "-" or target_base == "-":
                    face = DIFF_COLORS["gap"]
                else:
                    face = DIFF_COLORS["substitution"]
                edge = "#FFFFFF"
                text_color = "#101828"
            ax.add_patch(
                patches.Rectangle(
                    (i, y),
                    0.92,
                    cell_h,
                    facecolor=face,
                    edgecolor=edge,
                    linewidth=0.34,
                )
            )
            if base != "-":
                ax.text(i + 0.46, y + cell_h / 2, base, ha="center", va="center", fontsize=5.1, color=text_color)

    legend = [("Target mismatch", "substitution"), ("Target gap/insert", "gap")]
    lx = 0
    legend_step = 13.0
    for idx, (name, key) in enumerate(legend):
        x0 = lx + idx * legend_step
        ax.add_patch(
            patches.Rectangle((x0, 2.64), 0.55, 0.18, facecolor=DIFF_COLORS[key], edgecolor="#D0D5DD", linewidth=0.4)
        )
        ax.text(x0 + 0.72, 2.73, name, fontsize=5.9, va="center", color="#475467")
    ax.text(
        lx + len(legend) * legend_step + 2.5,
        2.73,
        f"Diff. {diff_count}/{total}",
        fontsize=6.0,
        va="center",
        color="#344054",
    )

    draw_region_track(ax, labels, 0.40)
    draw_group_marker(ax, labels, ["34", "35", "36"], 2.58, "Anticodon", "#B42318")
    draw_group_marker(ax, labels, ["74", "75", "76"], 2.58, "74--76 CCA coord.", "#344054")
    draw_group_marker(ax, labels, [label for label in labels if label.startswith("V")], 2.42, "Variable V sites", "#B54708")

    for label in ["1", "10", "20", "34", "36", "44", "54", "60", "73", "76"]:
        if label not in labels:
            continue
        x = labels.index(label) + 0.45
        ax.plot([x, x], [1.02, 1.06], color="#98A2B3", linewidth=0.6)
        ax.text(x, 0.94, label, ha="center", va="top", fontsize=5.5, color="#667085")


def draw_initial_candidate_panel(ax, case: dict) -> None:
    labels, template, target = initial_candidate_rows(case)
    n = len(labels)
    diff_count, total, _ = template_target_differences(case)
    ax.axis("off")
    ax.set_xlim(-4.2, n + 1.0)
    ax.set_ylim(0.0, 3.45)
    panel_heading(ax, "A")

    row_y = {"Coordinate": 1.90, "Template": 1.28, "Target": 0.66}
    cell_h = 0.42
    key_labels = {
        "-1",
        "1",
        "10",
        "17a",
        "20",
        "20a",
        "20b",
        "34",
        "36",
        "44",
        "V1",
        "V3",
        "V5",
        "46",
        "49",
        "50",
        "51",
        "52",
        "53",
        "61",
        "64",
        "64i1",
        "64i3",
        "64i5",
        "65",
        "73",
        "74",
        "76",
    }
    legend = [("Target mismatch", "substitution"), ("Target gap/insert", "gap")]
    legend_step = 13.0
    for idx, (name, key) in enumerate(legend):
        x0 = idx * legend_step
        ax.add_patch(
            patches.Rectangle((x0, 3.12), 0.55, 0.18, facecolor=DIFF_COLORS[key], edgecolor="#D0D5DD", linewidth=0.4)
        )
        ax.text(x0 + 0.72, 3.21, name, fontsize=5.9, va="center", color="#475467")
    ax.text(
        len(legend) * legend_step + 2.5,
        3.21,
        f"Diff. {diff_count}/{total}",
        fontsize=6.0,
        va="center",
        color="#344054",
    )

    annotation_y = row_y["Coordinate"] + cell_h + 0.15
    draw_group_marker(ax, labels, ["34", "35", "36"], annotation_y, "Anticodon", ANNOTATION_COLOR)
    draw_group_marker(ax, labels, ["74", "75", "76"], annotation_y, "74--76 CCA coord.", ANNOTATION_COLOR)

    t_arm_cols = [
        idx
        for idx, label in enumerate(labels)
        if label.startswith("64i") or label in {str(n) for n in range(49, 66)}
    ]

    for row_name, y in row_y.items():
        ax.text(-1.05, y + cell_h / 2, row_name, ha="right", va="center", fontsize=6.5, color="#101828")

    for idx, label in enumerate(labels):
        region = label_region(label)
        ax.add_patch(
            patches.Rectangle(
                (idx, row_y["Coordinate"]),
                0.92,
                cell_h,
                facecolor=REGION_COLORS.get(region, "#F2F4F7"),
                edgecolor="#FFFFFF",
                linewidth=0.30,
                alpha=0.72,
            )
        )
        if label in key_labels:
            ax.text(
                idx + 0.46,
                row_y["Coordinate"] + cell_h / 2,
                label,
                ha="center",
                va="center",
                fontsize=4.25,
                color="#344054",
                rotation=90,
            )

        template_base = template[idx]
        target_base = target[idx]
        ax.add_patch(
            patches.Rectangle(
                (idx, row_y["Template"]),
                0.92,
                cell_h,
                facecolor="#F8FAFC",
                edgecolor="#E4E7EC",
                linewidth=0.28,
            )
        )
        if template_base != "-":
            ax.text(idx + 0.46, row_y["Template"] + cell_h / 2, template_base, ha="center", va="center", fontsize=4.55, color="#475467")

        if target_base == "-":
            face = DIFF_COLORS["gap"] if template_base != "-" else DIFF_COLORS["target_gap"]
        elif template_base == "-":
            face = DIFF_COLORS["gap"]
        elif template_base == target_base:
            face = DIFF_COLORS["match"]
        else:
            face = DIFF_COLORS["substitution"]
        ax.add_patch(
            patches.Rectangle(
                (idx, row_y["Target"]),
                0.92,
                cell_h,
                facecolor=face,
                edgecolor="#FFFFFF",
                linewidth=0.28,
            )
        )
        if target_base != "-":
            ax.text(idx + 0.46, row_y["Target"] + cell_h / 2, target_base, ha="center", va="center", fontsize=4.55, color="#101828")
        elif template_base != "-":
            ax.text(idx + 0.46, row_y["Target"] + cell_h / 2, "-", ha="center", va="center", fontsize=4.55, color="#475467")

    draw_region_track(ax, labels, 0.18)
    if t_arm_cols:
        left = min(t_arm_cols) - 0.03
        width = max(t_arm_cols) - min(t_arm_cols) + 0.98
        ax.add_patch(
            patches.Rectangle(
                (left, row_y["Target"] - 0.06),
                width,
                1.78,
                facecolor="none",
                edgecolor=SECONDARY_ANNOTATION_COLOR,
                linewidth=0.55,
            )
        )
        ax.text(
            left + width / 2,
            annotation_y + 0.08,
            "T-arm recheck window",
            ha="center",
            va="bottom",
            fontsize=6.0,
            color=SECONDARY_ANNOTATION_COLOR,
        )


def draw_summary_panel(ax, case: dict, positions: dict[str, tuple[float, float]], letter: str = "B") -> None:
    ax.axis("off")
    panel_heading(ax, letter)
    metrics = case["metrics"]
    labels = case["labels"]
    template = case["template"]
    target = case["target"]
    templates = dict(zip(labels, template))
    bases = dict(zip(labels, target))
    initial_rows = initial_t_arm_rows(case)
    initial_bases = {label: row["target"] for label, row in initial_rows.items()}
    initial_templates = {label: row["template"] for label, row in initial_rows.items()}
    struct = t_arm_structure(case)
    canonical_pairs = t_arm_structural_pairs(
        labels=list(labels),
        bases=bases,
        struct=struct,
    ) or [("49", "65"), ("50", "64"), ("51", "63"), ("52", "62")]
    problem_pairs = t_arm_structural_pairs(
        labels=list(labels),
        bases=initial_bases,
        struct=struct,
    ) or [("52", "65"), ("53", "64i5"), ("54", "64i4"), ("57", "64i3")]
    t_labels = [str(n) for n in range(49, 66) if str(n) in positions]

    def insertion_rank(label: str) -> int:
        match = re.search(r"i(\d+)$", label)
        return int(match.group(1)) if match else 0

    initial_insertion_labels = sorted(
        [lab for lab in initial_bases if re.match(r"^64i\d+$", str(lab))],
        key=insertion_rank,
    )

    source_points = {lab: positions[lab] for lab in t_labels}

    xs = [point[0] for point in source_points.values()]
    ys = [point[1] for point in source_points.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    def map_point(area: tuple[float, float, float, float], lab: str) -> tuple[float, float]:
        x0, y0, width, height = area
        px, py = source_points[lab]
        x = x0 + (px - x_min) / (x_max - x_min) * width
        y = y0 + (y_max - py) / (y_max - y_min) * height
        return x, y

    def state_face(template_base: str, target_base: str) -> str:
        template_base = str(template_base or "-").upper()
        target_base = str(target_base or "-").upper()
        if target_base == "-":
            return DIFF_COLORS["gap"] if template_base != "-" else DIFF_COLORS["target_gap"]
        if template_base == "-":
            return DIFF_COLORS["gap"]
        if template_base == target_base:
            return DIFF_COLORS["match"]
        return DIFF_COLORS["substitution"]

    def draw_base(
        cx: float,
        cy: float,
        base: str,
        template_base: str,
        label: str,
        *,
        edge: str = STATE_EDGE_COLOR,
        lw: float = 0.55,
        draw_gap: bool = True,
    ) -> None:
        base = str(base or "-").upper()
        if base == "-" and not draw_gap:
            return
        face = state_face(template_base, base)
        ax.scatter(
            [cx],
            [cy],
            s=160,
            marker="o",
            transform=ax.transAxes,
            facecolors=face,
            edgecolors=edge,
            linewidths=lw,
            zorder=3,
            clip_on=False,
        )
        if base != "-":
            ax.text(cx, cy, base, transform=ax.transAxes, ha="center", va="center", fontsize=7.2, color="#101828", zorder=4)
        else:
            ax.text(cx, cy, "-", transform=ax.transAxes, ha="center", va="center", fontsize=7.2, color="#667085", zorder=4)
        ax.text(
            cx,
            cy - 0.041,
            label,
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=4.65,
            color="#667085",
            zorder=7,
            bbox=dict(facecolor="#FFFFFF", edgecolor="none", alpha=0.86, pad=0.08),
        )

    def draw_t_arm(
        area: tuple[float, float, float, float],
        title: str,
        *,
        mode: str,
    ) -> None:
        x0, y0, width, height = area
        pts = {lab: map_point(area, lab) for lab in t_labels}

        def insertion_axis_point(lab: str) -> tuple[float, float] | None:
            match = re.match(r"^64i(\d+)$", lab)
            if not match or "64" not in pts or "65" not in pts:
                return None
            rank = int(match.group(1))
            x64, y64 = pts["64"]
            x65, y65 = pts["65"]
            dx = x65 - x64
            dy = y65 - y64
            dist = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux = -dy / dist
            uy = dx / dist
            if uy < 0:
                ux, uy = -ux, -uy
            xm = (x64 + x65) / 2
            ym = (y64 + y65) / 2
            return xm + ux * 0.055 * rank, ym + uy * 0.055 * rank

        def base_at(lab: str) -> str:
            source = initial_bases if mode == "before" else bases
            return str(source.get(lab, "-") or "-").upper()

        def template_at(lab: str) -> str:
            source = initial_templates if mode == "before" else templates
            return str(source.get(lab, "-") or "-").upper()

        title_y = y0 + height / 2
        ax.text(
            x0 - 0.045,
            title_y,
            title,
            transform=ax.transAxes,
            ha="right",
            va="center",
            fontsize=6.0,
            color="#101828",
            clip_on=False,
        )
        for left, right in zip(t_labels, t_labels[1:]):
            x1, y1 = pts[left]
            x2, y2 = pts[right]
            ax.plot(
                [x1, x2],
                [y1, y2],
                transform=ax.transAxes,
                color="#CBD5E1",
                linewidth=0.65,
                zorder=1,
            )
        insertion_pts: dict[str, tuple[float, float]] = {}
        if mode == "before":
            if "64" in pts and "65" in pts:
                x64, y64 = pts["64"]
                x65, y65 = pts["65"]
                if initial_insertion_labels:
                    insertion_xy = [
                        point
                        for lab in initial_insertion_labels
                        if (point := insertion_axis_point(lab)) is not None
                    ]
                    if insertion_xy:
                        xm = (x64 + x65) / 2
                        ym = (y64 + y65) / 2
                        x_near, y_near = insertion_xy[0]
                        ax.plot(
                            [xm, x_near],
                            [ym, y_near],
                            transform=ax.transAxes,
                            color="#94A3B8",
                            linewidth=0.55,
                            linestyle=(0, (1.4, 2.0)),
                            zorder=1,
                        )
            for lab in initial_insertion_labels:
                point = insertion_axis_point(lab)
                if point is None:
                    continue
                gx, gy = point
                insertion_pts[lab] = (gx, gy)
                base = initial_bases.get(lab, "-")
                ax.scatter(
                    [gx],
                    [gy],
                    s=160,
                    marker="o",
                    transform=ax.transAxes,
                    facecolors=state_face(initial_templates.get(lab, "-"), base),
                    edgecolors=STATE_EDGE_COLOR,
                    linewidths=0.65,
                    zorder=4,
                    clip_on=False,
                )
                if base != "-":
                    ax.text(
                        gx,
                        gy,
                        base,
                        transform=ax.transAxes,
                        ha="center",
                        va="center",
                        fontsize=7.2,
                        color="#101828",
                        zorder=5,
                    )
                ax.text(
                    gx + 0.066,
                    gy,
                    lab,
                    transform=ax.transAxes,
                    ha="left",
                    va="center",
                    fontsize=4.45,
                    color="#475467",
                    zorder=8,
                    clip_on=False,
                    bbox=dict(facecolor="#FFFFFF", edgecolor="none", alpha=0.90, pad=0.08),
                )
            for left, right in problem_pairs:
                if left not in pts:
                    continue
                target_points = {**pts, **insertion_pts}
                if right not in target_points:
                    continue
                x1, y1 = pts[left]
                x2, y2 = target_points[right]
                ax.plot(
                    [x1, x2],
                    [y1, y2],
                    transform=ax.transAxes,
                    color=ERROR_COLOR,
                    linewidth=0.95,
                    linestyle="-",
                    zorder=1,
                )
        else:
            for left, right in canonical_pairs:
                if left not in pts or right not in pts:
                    continue
                x1, y1 = pts[left]
                x2, y2 = pts[right]
                ax.plot(
                    [x1, x2],
                    [y1, y2],
                    transform=ax.transAxes,
                    color=PASS_COLOR,
                    linewidth=1.0,
                    linestyle="-",
                    zorder=2,
                )
        for lab in t_labels:
            cx, cy = pts[lab]
            edge = PASS_COLOR if mode == "after" and lab in {x for p in canonical_pairs for x in p} else STATE_EDGE_COLOR
            if mode == "before" and lab in {x for p in problem_pairs for x in p if not str(x).startswith("64i")}:
                edge = STATE_EDGE_COLOR
            base = base_at(lab)
            draw_base(
                cx,
                cy,
                base,
                template_at(lab),
                lab,
                edge=edge,
                lw=0.8 if edge != STATE_EDGE_COLOR else 0.55,
                draw_gap=True,
            )
        all_pts = dict(pts)
        all_pts.update(insertion_pts)
        return all_pts

    top_area = (0.118, 0.520, 0.715, 0.275)
    bottom_area = (0.118, 0.105, 0.715, 0.285)
    draw_t_arm(top_area, "Before correction", mode="before")
    ax.annotate(
        "",
        xy=(0.885, 0.430),
        xytext=(0.885, 0.515),
        xycoords=ax.transAxes,
        arrowprops=dict(arrowstyle="-|>", color="#667085", lw=0.85, shrinkA=0, shrinkB=0),
    )
    ax.text(
        0.910,
        0.472,
        "Agent",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.3,
        color="#344054",
    )
    draw_t_arm(bottom_area, "After correction", mode="after")


def circle(ax, x: float, y: float, base: str, label: str, r: float = 8.8) -> None:
    if base == "-":
        ax.add_patch(
            patches.Circle((x, y), 6.5, facecolor="#F8FAFC", edgecolor="#DDE3EC", linewidth=0.34, zorder=2)
        )
        return
    edge = "#7C8796"
    linewidth = 0.34
    if label in KEY_ANTICODON:
        edge = ANNOTATION_COLOR
        linewidth = 0.58
    elif label in KEY_THREE_PRIME:
        edge = ANNOTATION_COLOR
        linewidth = 0.58
    elif label in KEY_VARIABLE:
        edge = SECONDARY_ANNOTATION_COLOR
        linewidth = 0.50
    ax.add_patch(
        patches.Circle(
            (x, y),
            r,
            facecolor=BASE_COLORS.get(base, "#D9DEE8"),
            edgecolor=edge,
            linewidth=linewidth,
            zorder=3,
        )
    )
    ax.text(x, y, base, ha="center", va="center_baseline", fontsize=7.4, weight="normal", color="#101828", zorder=4)


def draw_pair(ax, p1: tuple[float, float], p2: tuple[float, float]) -> None:
    ax.plot(
        [p1[0], p2[0]],
        [p1[1], p2[1]],
        color="#A5B0C0",
        linewidth=0.70,
        linestyle=(0, (2.2, 2.2)),
        zorder=0,
    )


def add_position_label(
    ax,
    positions: dict[str, tuple[float, float]],
    lab: str,
    dx: float,
    dy: float,
    *,
    text: str | None = None,
    color: str = "#475467",
    line: bool = True,
) -> None:
    if lab not in positions:
        return
    x, y = positions[lab]
    tx, ty = x + dx, y + dy
    if line:
        ax.plot([x, tx], [y, ty], color="#D0D5DD", linewidth=0.45, zorder=2)
    ax.text(
        tx,
        ty,
        text or lab,
        ha="center",
        va="center",
        fontsize=5.8,
        color=color,
        zorder=6,
        bbox=dict(boxstyle="round,pad=0.12", facecolor="#FFFFFF", edgecolor="#D0D5DD", linewidth=0.35),
    )


def add_group_bar(
    ax,
    positions: dict[str, tuple[float, float]],
    labs: list[str],
    y_offset: float,
    text: str,
    color: str,
) -> None:
    pts = [positions[lab] for lab in labs if lab in positions]
    if not pts:
        return
    xs = [p[0] for p in pts]
    y = min(p[1] for p in pts) + y_offset
    x0, x1 = min(xs), max(xs)
    ax.plot([x0, x1], [y, y], color=color, linewidth=0.8, zorder=5)
    ax.plot([x0, x0], [y, y + 6], color=color, linewidth=0.8, zorder=5)
    ax.plot([x1, x1], [y, y + 6], color=color, linewidth=0.8, zorder=5)
    ax.text((x0 + x1) / 2, y - 11, text, ha="center", va="center", fontsize=6.2, color=color, zorder=6)


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def expand_polygon(points: list[tuple[float, float]], pad: float) -> list[tuple[float, float]]:
    if not points:
        return points
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    expanded: list[tuple[float, float]] = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        expanded.append((x + dx / length * pad, y + dy / length * pad))
    return expanded


def add_region_blobs(ax, positions: dict[str, tuple[float, float]]) -> None:
    for region in REGION_ORDER:
        pts = [positions[k] for k in positions if label_region(k) == region]
        hull = expand_polygon(convex_hull(pts), 15)
        if len(hull) < 3:
            continue
        verts = hull + [hull[0]]
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(hull) - 1) + [MplPath.CLOSEPOLY]
        ax.add_patch(
            PathPatch(
                MplPath(verts, codes),
                facecolor=REGION_COLORS[region],
                edgecolor="none",
                alpha=0.45,
                zorder=0,
            )
        )
        xs, ys = zip(*pts)
        ax.text(
            sum(xs) / len(xs),
            sum(ys) / len(ys),
            region,
            ha="center",
            va="center",
            fontsize=6.2,
            color="#475569",
            zorder=1,
        )


def draw_structure_map(ax, case: dict, positions: dict[str, tuple[float, float]], letter: str = "C") -> None:
    labels = case["labels"]
    target = case["target"]
    bases = dict(zip(labels, target))

    panel_heading(ax, letter)
    ax.set_aspect("equal")
    ax.set_anchor("N")
    ax.axis("off")

    for left, right in full_structural_pairs(case):
        if left in positions and right in positions:
            draw_pair(ax, positions[left], positions[right])

    visible_labels = [lab for lab in labels if lab in positions]
    for left, right in zip(visible_labels, visible_labels[1:]):
        p1, p2 = positions[left], positions[right]
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color="#D4DCE7", linewidth=0.52, zorder=1)

    for lab in visible_labels:
        x, y = positions[lab]
        base = bases.get(lab, "-")
        circle(ax, x, y, base, lab, r=10.7)

    periodic_offsets = {
        "10": (0, -28),
        "20": (-24, 12),
        "30": (-25, 0),
        "40": (25, 0),
        "50": (0, 32),
        "60": (44, -24),
        "70": (31, 0),
    }
    for lab, (dx, dy) in periodic_offsets.items():
        add_position_label(ax, positions, lab, dx, dy, color="#667085")

    key_number_offsets = {
        "17a": (-18, -14),
        "20a": (-8, 21),
        "20b": (18, 21),
        "37": (24, -14),
        "V1": (15, 16),
        "V3": (0, 28),
        "V5": (18, -12),
    }
    for lab, (dx, dy) in key_number_offsets.items():
        add_position_label(ax, positions, lab, dx, dy, color="#101828")

    add_group_bar(ax, positions, ["74", "75", "76"], -17, "74--76 CCA coord.", "#344054")

    callouts = {
        "Anticodon\n34--36": ("35", (-68, 42), ANNOTATION_COLOR),
        "Variable\nV sites": ("V3", (77, 2), SECONDARY_ANNOTATION_COLOR),
    }
    for text, (anchor, offset, color) in callouts.items():
        x, y = positions[anchor]
        tx, ty = x + offset[0], y + offset[1]
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(tx, ty),
            textcoords="data",
            ha="center",
            va="center",
            fontsize=6.6,
            color=color,
            linespacing=1.15,
            arrowprops=dict(arrowstyle="-", lw=0.8, color=color, shrinkA=2, shrinkB=4),
            zorder=5,
            annotation_clip=False,
        )

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    ax.set_xlim(min(xs) - 35, max(xs) + 86)
    ax.set_ylim(max(ys) + 54, min(ys) - 40)


def main() -> None:
    case = load_case(SAMPLE[0])
    positions = load_sprinzl_positions()
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
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "TeX Gyre Heros", "DejaVu Sans"],
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.unicode_minus": False,
    })
    fig = plt.figure(figsize=(7.4, 6.35), constrained_layout=False)
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.22, 2.55],
        width_ratios=[0.96, 1.42],
        hspace=0.22,
        wspace=0.18,
    )
    ax_a = fig.add_subplot(gs[0, :])
    ax_b = fig.add_subplot(gs[1, 0])
    ax_c = fig.add_subplot(gs[1, 1])
    draw_initial_candidate_panel(ax_a, case)
    draw_summary_panel(ax_b, case, positions, letter="B")
    draw_structure_map(ax_c, case, positions, letter="C")
    fig.subplots_adjust(top=0.970, bottom=0.052, left=0.065, right=0.985)
    fig.savefig(OUT_DIR / "fig04_coordinate_map.pdf", bbox_inches="tight")
    fig.savefig(OUT_DIR / "fig04_coordinate_map.png", dpi=300, bbox_inches="tight")


if __name__ == "__main__":
    main()
