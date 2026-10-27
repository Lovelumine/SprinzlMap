#!/usr/bin/env python3
"""Shared data and rendering primitives for Figure 3."""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, patches


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import make_fig04_coordinate_map as original  # noqa: E402
from backend.agent.contracts import AlignmentRequest  # noqa: E402
from backend.agent.execution.root_snapshot_builder import RootSnapshotBuilder  # noqa: E402
from backend.domain.alignment.sprinzl_coordinates import order_key  # noqa: E402


SEQNAME = "tdbD00002457|DR1800"
CM_CASE_PATH = (
    ROOT
    / "benchmark/runs/publication-v31-cm-development-478c00f/cases"
    / "5e6340b83f3c6bf6.json"
)
AGENT_CASE_PATH = (
    ROOT
    / "benchmark/runs/publication-v31-tokenplan-development-478c00f/cases"
    / "5e6340b83f3c6bf6.json"
)
GOLD_TSV = ROOT / "benchmark/gold/mature_benchmark.curated.tsv"

INK = "#20252B"
MUTED = "#667085"
ERROR = "#C43D34"
PASS = "#168A5B"
PAIR = "#AAB4C2"
NEUTRAL_FACE = "#F5F7F9"
ERROR_FACE = "#F8D9D6"
T_LABELS = [str(value) for value in range(49, 66)]
T_PAIRS = [
    ("49", "65"),
    ("50", "64"),
    ("51", "63"),
    ("52", "62"),
    ("53", "61"),
]
CORRECTED_T_LABELS = {"51", "52", "53"}
INSERTION_RE = re.compile(r"^(.+?)i(\d+)$")


def normalize_base(value: str) -> str:
    base = str(value or "-").strip().upper().replace("T", "U")
    return base if base in {"A", "C", "G", "U"} else "-"


def load_case(path: Path) -> dict:
    case = json.loads(path.read_text(encoding="utf-8"))
    rows = list(csv.reader(io.StringIO(str(case["csv_content"]))))
    if len(rows) != 3:
        raise ValueError(f"Expected three CSV rows in {path}")
    case["labels"], case["template"], case["target"] = rows
    return case


def rerun_current_reference_candidate(archived_case: dict) -> dict:
    replay = dict(archived_case)
    snapshot = RootSnapshotBuilder().build(
        AlignmentRequest(
            target_seq=str(archived_case["input_seq"]),
            output_csv_path="/dev/null",
            use_llm=False,
            llm_reasoning=False,
            template_name=str(archived_case["template_name"]),
            input_type="mature",
            domain_hint="bacteria",
            cca_status="unknown",
            intron_status="unknown",
            trna_class="canonical",
        )
    )
    rows = list(csv.reader(io.StringIO(snapshot.csv_content)))
    if len(rows) != 3:
        raise ValueError("Current reference-tool replay did not return three CSV rows")
    replay["csv_content"] = snapshot.csv_content
    replay["labels"], replay["template"], replay["target"] = rows

    repairs: list[dict] = []
    for block in list(snapshot.raw_meta.get("agent_blocks") or []):
        repairs.extend(
            list(
                dict(block.get("meta") or {}).get(
                    "deterministic_boundary_repairs"
                )
                or []
            )
        )
    replay["_current_replay"] = {
        "source": "current_deterministic_reference_tool",
        "template_name": snapshot.template_name,
        "repairs": repairs,
    }
    return replay


def residue_rows(case: dict) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for column, (label, base) in enumerate(zip(case["labels"], case["target"])):
        normalized = normalize_base(base)
        if normalized == "-":
            continue
        rows.append(
            {
                "input_index": len(rows) + 1,
                "column": column,
                "label": str(label),
                "base": normalized,
            }
        )
    return rows


def load_gold_rows(seqname: str) -> list[dict[str, str | int]]:
    with GOLD_TSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        for source_row in reader:
            if source_row and source_row[0] == seqname:
                rows: list[dict[str, str | int]] = []
                for label, base in zip(header[1:], source_row[1:]):
                    normalized = normalize_base(base)
                    if normalized == "-":
                        continue
                    rows.append(
                        {
                            "input_index": len(rows) + 1,
                            "label": str(label),
                            "base": normalized,
                        }
                    )
                return rows
    raise KeyError(f"Gold reference not found: {seqname}")


def mismatch_indices(
    predicted: list[dict[str, str | int]],
    reference: list[dict[str, str | int]],
) -> list[int]:
    return [
        index
        for index, (predicted_row, reference_row) in enumerate(
            zip(predicted, reference), start=1
        )
        if str(predicted_row["label"]) != str(reference_row["label"])
    ]


def validate_cases(cm_case: dict, agent_case: dict, gold_rows: list[dict]) -> None:
    if cm_case["seqname"] != SEQNAME or agent_case["seqname"] != SEQNAME:
        raise AssertionError("Unexpected case artifact")
    cm_rows = residue_rows(cm_case)
    agent_rows = residue_rows(agent_case)
    if not (len(cm_rows) == len(agent_rows) == len(gold_rows) == 74):
        raise AssertionError("Unexpected residue count")
    bases = [str(row["base"]) for row in gold_rows]
    if bases != [str(row["base"]) for row in cm_rows]:
        raise AssertionError("CM output did not preserve the input sequence")
    if bases != [str(row["base"]) for row in agent_rows]:
        raise AssertionError("Agent output did not preserve the input sequence")

    if mismatch_indices(cm_rows, gold_rows) != [52, 53, 54]:
        raise AssertionError("Unexpected CM error pattern")
    if mismatch_indices(agent_rows, gold_rows):
        raise AssertionError("Unexpected SprinzlMap error pattern")
    if cm_case["metrics"]["label_correct"] != 71:
        raise AssertionError("Unexpected CM score")
    if len(gold_rows) - len(mismatch_indices(agent_rows, gold_rows)) != 74:
        raise AssertionError("Unexpected current SprinzlMap score")

    planning_events = [
        event
        for event in agent_case["agent_trace"]["trace_events"]
        if event.get("stage") == "planning"
    ]
    if len(planning_events) != 1:
        raise AssertionError("Expected one MiMo planning event")
    decision = planning_events[0].get("agent_decision") or {}
    if not decision.get("llm_called") or decision.get("model") != "mimo-v2.5-pro":
        raise AssertionError("The selected case did not call MiMo")
    if decision.get("action_id") != "legacy_reference_specialist:1:align_legacy_reference":
        raise AssertionError("Unexpected MiMo tool decision")
    if agent_case["agent"].get("best_snapshot_id") != "step-0001":
        raise AssertionError("MiMo-selected candidate was not retained")


def configure_style() -> None:
    for path in (
        original.ARIAL_REGULAR_FONT,
        original.ARIAL_BOLD_FONT,
        original.HEROS_REGULAR_FONT,
        original.HEROS_BOLD_FONT,
    ):
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "TeX Gyre Heros", "DejaVu Sans"],
            "font.size": 6.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )


def panel_letter(ax: plt.Axes, letter: str) -> None:
    ax.text(
        0.0,
        1.035,
        letter,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.6,
        weight="bold",
        color="#111111",
    )


def draw_bracket(
    ax: plt.Axes,
    first_index: int,
    last_index: int,
    y: float,
    text: str,
    color: str,
) -> None:
    x0 = first_index - 1
    x1 = last_index - 0.08
    ax.plot([x0, x1], [y, y], color=color, linewidth=0.85)
    ax.plot([x0, x0], [y, y - 0.10], color=color, linewidth=0.85)
    ax.plot([x1, x1], [y, y - 0.10], color=color, linewidth=0.85)
    ax.text(
        (x0 + x1) / 2,
        y + 0.10,
        text,
        ha="center",
        va="bottom",
        fontsize=5.8,
        color=color,
        weight="bold",
    )


def draw_coordinate_status_panel(
    ax: plt.Axes,
    cm_case: dict,
    agent_case: dict,
    gold_rows: list[dict],
) -> None:
    cm_rows = residue_rows(cm_case)
    agent_rows = residue_rows(agent_case)
    cm_mismatches = set(mismatch_indices(cm_rows, gold_rows))
    agent_mismatches = set(mismatch_indices(agent_rows, gold_rows))
    gold_labels_by_index = {
        int(row["input_index"]): str(row["label"]) for row in gold_rows
    }
    cm_labels_by_index = {
        int(row["input_index"]): str(row["label"]) for row in cm_rows
    }
    agent_labels_by_index = {
        int(row["input_index"]): str(row["label"]) for row in agent_rows
    }
    cm_error_labels = {
        label
        for index in cm_mismatches
        for label in (
            gold_labels_by_index[index],
            cm_labels_by_index[index],
        )
    }
    agent_error_labels = {
        label
        for index in agent_mismatches
        for label in (
            gold_labels_by_index[index],
            agent_labels_by_index[index],
        )
    }
    cm_bases = {
        str(row["label"]): str(row["base"])
        for row in cm_rows
    }
    agent_bases = {
        str(row["label"]): str(row["base"])
        for row in agent_rows
    }
    labels = sorted(
        set(str(label) for label in agent_case["labels"]) | set(cm_bases),
        key=order_key,
    )
    n_columns = len(labels)
    focus_labels = {"51", "52", "53", "53i1", "53i2", "53i3"}
    t_region_columns = [
        column
        for column, label in enumerate(labels)
        if original.label_region(label) == "T region"
    ]

    ax.axis("off")
    ax.set_xlim(-5.8, n_columns + 1.0)
    ax.set_ylim(0.0, 3.75)
    panel_letter(ax, "a")

    row_y = {
        "Sprinzl coordinate": 2.08,
        "Initial projection": 1.40,
        "Validated result": 0.72,
    }
    cell_height = 0.45
    for row_name, y in row_y.items():
        ax.text(
            -1.0,
            y + cell_height / 2,
            row_name,
            ha="right",
            va="center",
            fontsize=6.1,
            color=INK,
        )

    for column, label in enumerate(labels):
        region_face = original.REGION_COLORS.get(
            original.label_region(label), "#F2F4F7"
        )
        ax.add_patch(
            patches.Rectangle(
                (column, row_y["Sprinzl coordinate"]),
                0.92,
                cell_height,
                facecolor=region_face,
                edgecolor="#FFFFFF",
                linewidth=0.28,
                alpha=0.78,
            )
        )
        ax.text(
            column + 0.46,
            row_y["Sprinzl coordinate"] + cell_height / 2,
            label,
            ha="center",
            va="center",
            rotation=90,
            fontsize=3.25 if len(label) <= 3 else 2.80,
            color="#344054",
            weight="bold" if label in focus_labels else "normal",
        )

        for row_name, bases, error_labels in (
            ("Initial projection", cm_bases, cm_error_labels),
            ("Validated result", agent_bases, agent_error_labels),
        ):
            base = normalize_base(bases.get(label, "-"))
            occupied = base != "-"
            is_error = label in error_labels
            face = ERROR_FACE if is_error else (
                NEUTRAL_FACE if occupied else "#FAFBFC"
            )
            ax.add_patch(
                patches.Rectangle(
                    (column, row_y[row_name]),
                    0.92,
                    cell_height,
                    facecolor=face,
                    edgecolor=ERROR if is_error else "#FFFFFF",
                    linewidth=0.48 if is_error else 0.28,
                )
            )
            ax.text(
                column + 0.46,
                row_y[row_name] + cell_height / 2,
                base,
                ha="center",
                va="center",
                fontsize=3.85,
                color=(
                    ERROR
                    if is_error
                    else (INK if occupied else "#98A2B3")
                ),
                weight="bold" if is_error else "normal",
            )

    first_t_column = min(t_region_columns)
    last_t_column = max(t_region_columns)
    marker_y = row_y["Sprinzl coordinate"] + cell_height + 0.14
    original.draw_group_marker(
        ax,
        labels,
        ["34", "35", "36"],
        marker_y,
        "Anticodon",
        "#344054",
    )
    window_left = first_t_column - 0.06
    window_bottom = row_y["Validated result"] - 0.06
    window_width = last_t_column - first_t_column + 1.04
    window_top = marker_y - 0.06
    ax.add_patch(
        patches.Rectangle(
            (window_left, window_bottom),
            window_width,
            window_top - window_bottom,
            facecolor="none",
            edgecolor="#667085",
            linewidth=0.62,
        )
    )
    ax.text(
        (first_t_column + last_t_column + 0.92) / 2,
        marker_y + 0.08,
        "T-arm evaluation window",
        ha="center",
        va="bottom",
        fontsize=5.8,
        color="#667085",
    )
    original.draw_group_marker(
        ax,
        labels,
        ["74", "75", "76"],
        marker_y,
        "74--76 CCA coord.",
        "#344054",
    )

    original.draw_region_track(ax, labels, 0.20)


def local_t_positions(
    ax: plt.Axes,
    positions: dict[str, tuple[float, float]],
    area: tuple[float, float, float, float],
    insertion_labels: list[str],
) -> dict[str, tuple[float, float]]:
    x0, y0, width, height = area
    source = {label: positions[label] for label in T_LABELS}
    xs = [point[0] for point in source.values()]
    ys = [point[1] for point in source.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    mapped: dict[str, tuple[float, float]] = {}
    for label, (x, y) in source.items():
        mapped[label] = (
            x0 + (x - x_min) / (x_max - x_min) * width,
            y0 + (y_max - y) / (y_max - y_min) * height,
        )

    loop_center = (
        sum(mapped[label][0] for label in ("54", "55", "56", "57", "58", "59", "60")) / 7,
        sum(mapped[label][1] for label in ("54", "55", "56", "57", "58", "59", "60")) / 7,
    )
    axes_box = ax.get_window_extent()
    for label in insertion_labels:
        match = INSERTION_RE.fullmatch(label)
        if not match:
            continue
        anchor, rank_text = match.groups()
        if anchor not in mapped or not anchor.isdigit():
            continue
        next_label = str(int(anchor) + 1)
        if next_label not in mapped:
            continue
        x1, y1 = mapped[anchor]
        x2, y2 = mapped[next_label]
        midpoint = ((x1 + x2) / 2, (y1 + y2) / 2)
        display_dx = (x2 - x1) * axes_box.width
        display_dy = (y2 - y1) * axes_box.height
        display_norm = (
            display_dx * display_dx + display_dy * display_dy
        ) ** 0.5 or 1.0
        normal = (display_dy / display_norm, -display_dx / display_norm)
        if (
            normal[0] * (midpoint[0] - loop_center[0]) * axes_box.width
            + normal[1] * (midpoint[1] - loop_center[1]) * axes_box.height
            < 0
        ):
            normal = (-normal[0], -normal[1])
        rank = int(rank_text)
        distance = axes_box.width * (0.080 + 0.065 * (rank - 1))
        mapped[label] = (
            midpoint[0] + normal[0] * distance / axes_box.width,
            midpoint[1] + normal[1] * distance / axes_box.height,
        )
    return mapped


def is_local_t_label(label: str) -> bool:
    insertion = INSERTION_RE.fullmatch(label)
    if insertion and insertion.group(1).isdigit():
        return 49 <= int(insertion.group(1)) <= 65
    return label.isdigit() and 49 <= int(label) <= 65


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
        str(label): normalize_base(base)
        for label, base in zip(case["labels"], case["target"])
    }
    actual_path = [
        str(row["label"])
        for row in residue_rows(case)
        if is_local_t_label(str(row["label"]))
    ]
    if before:
        insertions = [
            label for label in actual_path if INSERTION_RE.fullmatch(label)
        ]
    else:
        insertions = []
    points = local_t_positions(ax, positions, area, insertions)

    for left, right in zip(T_LABELS, T_LABELS[1:]):
        x1, y1 = points[left]
        x2, y2 = points[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            transform=ax.transAxes,
            color="#E3E8EF",
            linewidth=0.48,
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
            color="#CBD4DF",
            linewidth=0.72,
            zorder=1,
        )
    for left, right in T_PAIRS:
        x1, y1 = points[left]
        x2, y2 = points[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            transform=ax.transAxes,
            color=PAIR,
            linewidth=0.68,
            linestyle=(0, (2.0, 1.8)),
            zorder=1,
        )

    affected = (
        {*CORRECTED_T_LABELS, *insertions}
        if before
        else set(CORRECTED_T_LABELS)
    )
    draw_labels = list(T_LABELS) + insertions
    state_color = ERROR if before else PASS
    for label in draw_labels:
        x, y = points[label]
        base = bases.get(label, "-")
        face = original.BASE_COLORS.get(base, "#FBFCFD")
        edge = state_color if label in affected else "#667085"
        ax.scatter(
            [x],
            [y],
            s=118,
            transform=ax.transAxes,
            facecolor=face,
            edgecolor=edge,
            linewidth=0.95 if label in affected else 0.55,
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
            fontsize=6.0,
            color=INK if base != "-" else MUTED,
            zorder=5,
        )
        ax.text(
            x,
            y - 0.036,
            label,
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=4.1,
            color=state_color if label in affected else MUTED,
            weight="bold" if label in affected else "normal",
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.84,
                "pad": 0.08,
            },
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
        fontsize=6.0,
        color=INK,
    )


def draw_planned_local_correction(
    ax: plt.Axes,
    cm_case: dict,
    agent_case: dict,
    positions: dict[str, tuple[float, float]],
) -> None:
    ax.axis("off")
    panel_letter(ax, "b")
    draw_t_state(
        ax,
        case=cm_case,
        area=(0.12, 0.61, 0.64, 0.22),
        title="Initial projection",
        positions=positions,
        before=True,
    )
    ax.annotate(
        "",
        xy=(0.50, 0.415),
        xytext=(0.50, 0.535),
        xycoords=ax.transAxes,
        arrowprops={
            "arrowstyle": "-|>",
            "color": "#5F6F83",
            "lw": 0.8,
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )
    ax.text(
        0.525,
        0.485,
        "Agent plan",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.8,
        color="#344054",
        weight="bold",
    )
    ax.text(
        0.525,
        0.452,
        "selects reference tool",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.0,
        color=MUTED,
    )
    draw_t_state(
        ax,
        case=agent_case,
        area=(0.12, 0.11, 0.64, 0.22),
        title="Validated result",
        positions=positions,
        before=False,
    )


def extend_insertion_positions(
    canonical_positions: dict[str, tuple[float, float]],
    case: dict,
) -> dict[str, tuple[float, float]]:
    positions = dict(canonical_positions)
    next_map = {
        "V17": "V1",
        "45": "V11",
        "V5": "V27",
        "V21": "46",
        "20": "20a",
        "20a": "20b",
        "20b": "21",
        "-1": "1",
        "17": "17a",
        "17a": "18",
    }
    mirror = {"42", "43", "44", "45", "V11", "V12", "47", "48", "49", "50"}
    for label in case["labels"]:
        label = str(label)
        if label in positions:
            continue
        match = INSERTION_RE.fullmatch(label)
        if not match:
            continue
        anchor, rank_text = match.groups()
        anchor_point = positions.get(anchor)
        if anchor_point is None:
            continue
        if anchor in next_map:
            next_label = next_map[anchor]
        elif re.fullmatch(r"-?\d+", anchor):
            next_label = str(int(anchor) + 1)
        else:
            next_label = ""
        next_point = positions.get(next_label)
        if next_point is None:
            continue
        dx = next_point[0] - anchor_point[0]
        dy = next_point[1] - anchor_point[1]
        distance = (dx * dx + dy * dy) ** 0.5 or 1.0
        normal = (-dy / distance, dx / distance)
        midpoint = (
            (anchor_point[0] + next_point[0]) / 2,
            (anchor_point[1] + next_point[1]) / 2,
        )
        direction = -1 if anchor in mirror else 1
        offset = 20 * int(rank_text) * direction
        positions[label] = (
            midpoint[0] + normal[0] * offset,
            midpoint[1] + normal[1] * offset,
        )
    return positions


def draw_unoccupied_coordinate_scaffold(
    ax: plt.Axes,
    display_case: dict,
    canonical_positions: dict[str, tuple[float, float]],
) -> None:
    bases = {
        str(label): normalize_base(base)
        for label, base in zip(display_case["labels"], display_case["target"])
    }
    canonical_labels = list(canonical_positions)
    for left, right in zip(canonical_labels, canonical_labels[1:]):
        if bases.get(left, "-") != "-" and bases.get(right, "-") != "-":
            continue
        x1, y1 = canonical_positions[left]
        x2, y2 = canonical_positions[right]
        ax.plot(
            [x1, x2],
            [y1, y2],
            color="#DDE3EB",
            linewidth=0.46,
            linestyle=(0, (2.0, 2.0)),
            zorder=0,
        )
    for label, (x, y) in canonical_positions.items():
        if bases.get(label, "-") != "-":
            continue
        ax.add_patch(
            patches.Circle(
                (x, y),
                6.5,
                facecolor="#FBFCFD",
                edgecolor="#C8D1DD",
                linewidth=0.38,
                zorder=2,
            )
        )
        ax.text(
            x,
            y,
            "-",
            ha="center",
            va="center",
            fontsize=4.4,
            color="#98A2B3",
            zorder=3,
        )


def make_structure_case(agent_case: dict) -> dict:
    display_case = dict(agent_case)
    display_case["_full_structural_pairs"] = [
        ("1", "72"),
        ("2", "71"),
        ("3", "70"),
        ("4", "69"),
        ("5", "68"),
        ("6", "67"),
        ("7", "66"),
        ("11", "24"),
        ("12", "23"),
        ("13", "22"),
        ("27", "43"),
        ("28", "42"),
        ("29", "41"),
        ("30", "40"),
        ("31", "39"),
        *T_PAIRS,
    ]
    return display_case


def draw_final_structure(
    ax: plt.Axes,
    display_case: dict,
    canonical_positions: dict[str, tuple[float, float]],
    gold_rows: list[dict],
    cm_case: dict,
) -> None:
    positions = extend_insertion_positions(canonical_positions, display_case)
    draw_unoccupied_coordinate_scaffold(ax, display_case, canonical_positions)
    original.draw_structure_map(ax, display_case, positions, letter="c")

    agent_rows = residue_rows(display_case)
    for label in sorted(CORRECTED_T_LABELS):
        if label in positions:
            x, y = positions[label]
            ax.add_patch(
                patches.Circle(
                    (x, y),
                    12.4,
                    facecolor="none",
                    edgecolor=PASS,
                    linewidth=0.62,
                    zorder=5,
                )
            )
    agent_mismatches = set(mismatch_indices(agent_rows, gold_rows))
    for index in sorted(agent_mismatches):
        label = str(agent_rows[index - 1]["label"])
        if label != "9i1" or label not in positions:
            continue
        x, y = positions[label]
        ax.add_patch(
            patches.Circle(
                (x, y),
                12.4,
                facecolor="none",
                edgecolor=ERROR,
                linewidth=0.62,
                zorder=5,
            )
        )
    if "9i1" in positions:
        original.add_position_label(
            ax,
            positions,
            "9i1",
            -36,
            -2,
            color=ERROR,
        )
    agent_correct = len(gold_rows) - len(agent_mismatches)
    ax.text(
        0.98,
        0.985,
        f"{agent_correct}/{len(gold_rows)}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.1,
    color=PASS,
    weight="bold",
)
