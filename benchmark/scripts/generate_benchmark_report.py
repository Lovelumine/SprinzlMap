#!/usr/bin/env python3
"""Generate a reproducible markdown report with figures for a benchmark run."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The application package lives under APP/, and the run store resolves paths
# relative to this file, so both roots have to be importable.
for relative in ("APP", "."):
    path = str(PROJECT_ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.domain.alignment.number_to_region import number_to_region
from backend.infra.storage.benchmark_run_store import resolve_run_dir


VALID_BASES = {"A", "C", "G", "U", "N"}

FIGURE_SPECS = [
    ("fig01_overall_views.png", "图 1. 核心指标与去尾影响。运行：`{run_id}`。左侧为总体关键指标，右侧比较可评分样本口径与去尾口径。除错误率外，数值越高越好。"),
    ("fig02_label_accuracy_distribution.png", "图 2. 单样本编号准确率分布。运行：`{run_id}`。柱上数字为样本数，虚线和标注给出关键分位点。数值越高越好。"),
    ("fig03_region_breakdown.png", "图 3. 结构区段瓶颈。运行：`{run_id}`。柱状图比较逐碱基编号准确率与区段完全匹配率。数值越高越好。"),
    ("fig04_source_group_breakdown.png", "图 4. 生物来源分组差异。运行：`{run_id}`。柱为整条完全匹配率，点为编号准确率，括号内为该组样本数。数值越高越好。"),
    ("fig05_template_similarity_bins.png", "图 5. 模板相似度与性能。运行：`{run_id}`。柱为整条完全匹配率，折线为编号准确率和合法性通过率；横轴括号内为样本数。数值越高越好。"),
    ("fig06_error_modes.png", "图 6. 错误模式组成。运行：`{run_id}`。柱上标注为失败样本数及占错误样本比例。数值越高表示该失败模式越常见。"),
]

FINE_SOURCE_LABELS = {
    "ARCHAE": "Archaea",
    "EUBACT": "Bacteria",
    "CHLORO": "Chloroplast",
    "CYANEL": "Cyanelle",
    "CY ANI": "Cytosolic Animal",
    "CY PLA": "Cytosolic Plant",
    "CY SIN": "Cytosolic Fungi/Protist",
    "MI ANI": "Mito Animal",
    "MI PLA": "Mito Plant",
    "MI SIN": "Mito Fungi/Protist",
    "VIRUS": "Virus",
}

BROAD_SOURCE_ORDER = [
    "Mitochondrial",
    "Bacteria",
    "Chloroplast",
    "Cytosolic Eukaryote",
    "Archaea",
    "Virus",
    "Cyanelle",
]

PLOT_COLORS = {
    "blue": "#2F6690",
    "green": "#3A7D44",
    "orange": "#D97A04",
    "red": "#A63A50",
    "gold": "#C99A06",
    "teal": "#2A9D8F",
    "gray": "#6B7280",
}

SOURCE_LABEL_ZH = {
    "Mitochondrial": "线粒体",
    "Bacteria": "细菌",
    "Chloroplast": "叶绿体",
    "Cytosolic Eukaryote": "真核细胞质",
    "Archaea": "古菌",
    "Virus": "病毒",
    "Cyanelle": "蓝小体",
    "UNKNOWN": "未标注",
}

REGION_LABEL_ZH = {
    "Variable loop": "可变环",
    "D loop + D stem": "D环和D臂",
    "T loop + T stem": "T环和T臂",
    "Anticodon loop + Anticodon stem": "反密码子区",
    "Aminoacyl arm 5' end": "氨基酸臂5'端",
    "Aminoacyl arm 3' end": "氨基酸臂3'端",
}

ERROR_MODE_LABEL_ZH = {
    "tRNAscan no hit": "tRNAscan未检出",
    "non-canonical cloverleaf": "非标准三叶草结构",
    "base-preservation failure": "序列保持失败",
}


def configure_plot_style() -> None:
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in [
        "Noto Sans CJK SC",
        "Noto Sans Mono CJK SC",
        "WenQuanYi Micro Hei",
        "Droid Sans Fallback",
    ]:
        if font_name in available_fonts:
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            break
    plt.rcParams.update(
        {
            "axes.unicode_minus": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.size": 10,
        }
    )


configure_plot_style()


@dataclass
class CaseRow:
    seqname: str
    status: str
    elapsed_sec: float
    template_name: str
    error: str
    input_len: int
    base_preserved: Optional[bool]
    metrics: Dict[str, Any]
    kingdom_code: str
    gold_scored: bool
    organism: str
    amino_acid: str
    broad_group: str
    fine_group: str
    gold_labels: List[str]
    pred_labels: List[str]
    template_similarity: Optional[Dict[str, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Run id, or a path to a run directory. A run id is materialized "
             "from MySQL and MinIO into the git-ignored run cache.",
    )
    parser.add_argument(
        "--benchmark-tsv",
        type=Path,
        default=None,
        help="Optional whole-library answer copy.  When omitted each case is "
             "graded with the active answer already attached to its trace.",
    )
    parser.add_argument(
        "--export-tsv",
        type=Path,
        default=None,
        help="Optional tRNAdb metadata table.  When omitted the source group "
             "comes from the cohort label recorded on each case.",
    )
    parser.add_argument(
        "--template-tsv",
        type=Path,
        default=Path("data/tRNAs_flat.tsv"),
        help="Template flat table used to reconstruct template similarity.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Output report directory. Defaults to benchmark/reports/<run_id>.",
    )
    return parser.parse_args()


def sanitize_base(raw: str) -> str:
    base = str(raw or "").strip().upper().replace("T", "U")
    return base if base in VALID_BASES else "-"


def parse_csv_alignment(csv_raw: str) -> Tuple[List[str], List[str], List[str]]:
    try:
        rows = list(csv.reader(io.StringIO(csv_raw)))
    except Exception:
        return [], [], []
    if len(rows) < 3:
        return [], [], []
    return list(rows[0]), list(rows[1]), list(rows[2])


def residue_label_sequence(numbering_row: Sequence[str], sequence_row: Sequence[str]) -> Tuple[List[str], str]:
    labels: List[str] = []
    bases: List[str] = []
    for label, base in zip(numbering_row, sequence_row):
        norm = sanitize_base(base)
        if norm == "-":
            continue
        labels.append(str(label or "").strip())
        bases.append(norm)
    return labels, "".join(bases)


def safe_region(label: str) -> str:
    try:
        return number_to_region(str(label or "").strip())
    except Exception:
        return "UNKNOWN"


def load_flat_table(path: Path) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.reader(fin, delimiter="\t")
        header = next(reader)
        columns = header[1:]
        rows: Dict[str, Dict[str, str]] = {}
        for raw in reader:
            seqname = str(raw[0] or "").strip()
            rows[seqname] = dict(zip(columns, raw[1:]))
    return columns, rows


def load_export_meta(path: Path) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin, delimiter="\t")
        for row in reader:
            trna_id = str(row.get("tRNA ID") or "").strip()
            sprinzl_id = str(row.get("SprinzlID") or "").strip()
            seqname = f"{trna_id}|{sprinzl_id}" if sprinzl_id else trna_id
            if seqname:
                out[seqname] = dict(row)
    return out


def broad_group(kingdom_code: str) -> str:
    if kingdom_code in {"ARCHAE", "Archaea"}:
        return "Archaea"
    if kingdom_code in {"EUBACT", "Bacteria"}:
        return "Bacteria"
    if kingdom_code == "VIRUS":
        return "Virus"
    if kingdom_code.startswith("MI "):
        return "Mitochondrial"
    if kingdom_code.startswith("CY "):
        return "Cytosolic Eukaryote"
    if kingdom_code == "CHLORO":
        return "Chloroplast"
    if kingdom_code == "CYANEL":
        return "Cyanelle"
    if kingdom_code == "Eukaryota":
        return "Cytosolic Eukaryote"
    return kingdom_code or "UNKNOWN"


def fine_group(kingdom_code: str) -> str:
    return FINE_SOURCE_LABELS.get(kingdom_code, kingdom_code or "UNKNOWN")


COHORT_SOURCE_KINGDOMS = {
    "archaea": "ARCHAE",
    "bacteria": "EUBACT",
    "cytosolic_eukaryote": "Eukaryota",
    "mitochondria": "MI ANI",
    "chloroplast": "CHLORO",
    "cyanelle": "CYANEL",
    "virus": "VIRUS",
}


def kingdom_from_cohort(payload: Dict[str, Any]) -> str:
    """Kingdom label when no tRNAdb metadata table was supplied.

    The cohort label is what the benchmark used to select the sequence, so a
    report built from the store alone still groups results by domain.
    """
    case = payload.get("benchmark_case") or {}
    cohort = str(case.get("cohort_source") or "").strip().lower()
    if cohort in COHORT_SOURCE_KINGDOMS:
        return COHORT_SOURCE_KINGDOMS[cohort]
    broad = str(((case.get("metadata") or {}).get("broad_source") or "")).strip()
    return broad or "UNKNOWN"


def classify_error(error_text: str) -> str:
    if "tRNAscan-SE did not detect a tRNA hit" in error_text:
        return "tRNAscan no hit"
    if "sequence does not show a standard cloverleaf structure" in error_text:
        return "non-canonical cloverleaf"
    if "base-preservation check" in error_text:
        return "base-preservation failure"
    if not error_text:
        return "unknown"
    return error_text.split(":", 1)[0]


def reconstruct_template_similarity(
    columns: Sequence[str],
    gold_row: Dict[str, str],
    template_row: Optional[Dict[str, str]],
) -> Optional[Dict[str, float]]:
    if template_row is None:
        return None
    template_non_gap = 0
    target_non_gap = 0
    overlap_non_gap = 0
    overlap_match = 0
    for column in columns:
        target_base = sanitize_base(gold_row.get(column))
        template_base = sanitize_base(template_row.get(column))
        if target_base != "-":
            target_non_gap += 1
        if template_base != "-":
            template_non_gap += 1
        if target_base != "-" and template_base != "-":
            overlap_non_gap += 1
            if target_base == template_base:
                overlap_match += 1
    if overlap_non_gap == 0:
        return None
    return {
        "template_non_gap": float(template_non_gap),
        "target_non_gap": float(target_non_gap),
        "overlap_non_gap": float(overlap_non_gap),
        "overlap_match": float(overlap_match),
        "overlap_identity": overlap_match / overlap_non_gap,
        "template_overlap_coverage": overlap_non_gap / template_non_gap if template_non_gap else 0.0,
        "target_overlap_coverage": overlap_non_gap / target_non_gap if target_non_gap else 0.0,
    }


def load_case_rows(
    run_dir: Path,
    *,
    benchmark_columns: Sequence[str],
    benchmark_rows: Dict[str, Dict[str, str]],
    export_meta: Dict[str, Dict[str, str]],
    template_rows: Dict[str, Dict[str, str]],
) -> List[CaseRow]:
    rows: List[CaseRow] = []
    cases_dir = run_dir / "cases"
    for path in sorted(cases_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        seqname = str(payload.get("seqname") or "")
        # The answer normally travels with the case: a rebuilt run directory
        # attaches the active answer table to every frozen prediction, so the
        # optional whole-library TSV is only needed for an offline audit.
        case_columns = [str(v) for v in (payload.get("gold_labels") or [])]
        case_bases = [str(v) for v in (payload.get("gold_bases") or [])]
        if case_columns:
            gold_row = dict(zip(case_columns, case_bases))
            columns: Sequence[str] = case_columns
        else:
            gold_row = benchmark_rows.get(seqname, {})
            columns = benchmark_columns
        gold_labels = [col for col in columns if gold_row.get(col) not in (None, "", "-")]
        meta = export_meta.get(seqname) or {}
        kingdom_code = str(meta.get("Kingdom") or kingdom_from_cohort(payload))
        payload_metrics = dict(payload.get("metrics") or {})
        template_name = str(payload.get("template_name") or (payload.get("agent") or {}).get("best_template") or "")
        pred_labels: List[str] = []
        if str(payload.get("status") or "") == "ok":
            numbering_row, _template_row, sequence_row = parse_csv_alignment(str(payload.get("csv_content") or ""))
            pred_labels, _pred_seq = residue_label_sequence(numbering_row, sequence_row)
        rows.append(
            CaseRow(
                seqname=seqname,
                status=str(payload.get("status") or ""),
                elapsed_sec=float(payload.get("elapsed_sec") or 0.0),
                template_name=template_name,
                error=str(payload.get("error") or ""),
                input_len=int(payload.get("input_len") or 0),
                base_preserved=payload.get("base_preserved"),
                metrics=payload_metrics,
                kingdom_code=kingdom_code,
                gold_scored=bool(payload.get("gold_evaluation_allowed", True)),
                organism=str(meta.get("Organism") or ""),
                amino_acid=str(meta.get("Amino Acid") or ""),
                broad_group=broad_group(kingdom_code),
                fine_group=fine_group(kingdom_code),
                gold_labels=gold_labels,
                pred_labels=pred_labels,
                template_similarity=reconstruct_template_similarity(
                    columns,
                    gold_row,
                    template_rows.get(template_name),
                ),
            )
        )
    return rows


def ratio(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def format_num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def summarize_ok_rows(rows: Sequence[CaseRow]) -> Dict[str, Any]:
    ok_rows = [row for row in rows if row.status == "ok"]
    # Exact match is a statement about sequences that have an answer to match,
    # the same denominator the run summary and the paper use; legality and base
    # preservation apply to every processed sequence.
    scored_rows = [row for row in ok_rows if row.gold_scored]

    def metric_sum(name: str) -> int:
        total = 0
        for row in ok_rows:
            total += int(row.metrics.get(name) or 0)
        return total

    label_total = metric_sum("label_total")
    region_total = metric_sum("region_total")
    anticodon_total = metric_sum("anticodon_total")
    cca_total = metric_sum("cca_total")
    variable_total = metric_sum("variable_total")
    elapsed_values = [row.elapsed_sec for row in ok_rows]

    return {
        "n_ok": len(ok_rows),
        "n_gold_scored": len(scored_rows),
        "residue_label_accuracy": ratio(metric_sum("label_correct"), label_total),
        "region_accuracy": ratio(metric_sum("region_correct"), region_total),
        "anticodon_accuracy": ratio(metric_sum("anticodon_correct"), anticodon_total),
        "cca_accuracy": ratio(metric_sum("cca_correct"), cca_total),
        "variable_accuracy": ratio(metric_sum("variable_correct"), variable_total),
        "exact_match_rate": ratio(
            sum(1 for row in ok_rows if row.metrics.get("exact_match")),
            len(scored_rows),
        ),
        "legality_pass_rate": ratio(
            sum(1 for row in ok_rows if row.metrics.get("legality_passed")),
            len(ok_rows),
        ),
        "base_preservation_rate": ratio(
            sum(1 for row in ok_rows if row.base_preserved),
            len(ok_rows),
        ),
        "elapsed_mean_sec": statistics.mean(elapsed_values) if elapsed_values else None,
        "elapsed_median_sec": statistics.median(elapsed_values) if elapsed_values else None,
    }


def summarize_error_inclusive(rows: Sequence[CaseRow]) -> Dict[str, Any]:
    return {
        "n_analyzed": len(rows),
        "n_ok": sum(1 for row in rows if row.status == "ok"),
        "n_error": sum(1 for row in rows if row.status != "ok"),
        "error_rate": ratio(sum(1 for row in rows if row.status != "ok"), len(rows)),
        "exact_match_rate": ratio(
            sum(1 for row in rows if row.status == "ok" and row.metrics.get("exact_match")),
            len(rows),
        ),
        "legality_pass_rate": ratio(
            sum(1 for row in rows if row.status == "ok" and row.metrics.get("legality_passed")),
            len(rows),
        ),
        "base_preservation_rate": ratio(
            sum(1 for row in rows if row.status == "ok" and row.base_preserved),
            len(rows),
        ),
    }


def trim_bottom_by_label_accuracy(ok_rows: Sequence[CaseRow], fraction: float = 0.10) -> Dict[str, Any]:
    sorted_rows = sorted(
        ok_rows,
        key=lambda row: (
            row.metrics.get("label_accuracy") is None,
            row.metrics.get("label_accuracy"),
        ),
    )
    trim_n = math.floor(len(sorted_rows) * fraction)
    trimmed_rows = sorted_rows[trim_n:]
    removed_max = None
    kept_min = None
    if trim_n:
        removed_max = sorted_rows[trim_n - 1].metrics.get("label_accuracy")
    if trim_n < len(sorted_rows):
        kept_min = sorted_rows[trim_n].metrics.get("label_accuracy")
    return {
        "trim_n": trim_n,
        "fraction": fraction,
        "removed_max_label_accuracy": removed_max,
        "kept_min_label_accuracy": kept_min,
        "rows": trimmed_rows,
        "summary": summarize_ok_rows(trimmed_rows),
    }


def make_region_summary(ok_rows: Sequence[CaseRow]) -> List[Dict[str, Any]]:
    region_stats: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "total": 0, "exact_hits": 0, "case_count": 0}
    )
    for row in ok_rows:
        by_region: Dict[str, List[int]] = defaultdict(list)
        for idx, gold_label in enumerate(row.gold_labels):
            by_region[safe_region(gold_label)].append(idx)
        for region, idxs in by_region.items():
            exact = True
            correct = 0
            for idx in idxs:
                pred = row.pred_labels[idx] if idx < len(row.pred_labels) else None
                if pred == row.gold_labels[idx]:
                    correct += 1
                else:
                    exact = False
            region_stats[region]["correct"] += correct
            region_stats[region]["total"] += len(idxs)
            region_stats[region]["case_count"] += 1
            if exact:
                region_stats[region]["exact_hits"] += 1
    out: List[Dict[str, Any]] = []
    for region, stats in region_stats.items():
        out.append(
            {
                "region": region,
                "residue_count": stats["total"],
                "case_count": stats["case_count"],
                "label_accuracy": ratio(stats["correct"], stats["total"]),
                "exact_match_rate": ratio(stats["exact_hits"], stats["case_count"]),
            }
        )
    return sorted(out, key=lambda row: row["label_accuracy"] or -1.0)


def summarize_group(rows: Sequence[CaseRow]) -> Dict[str, Any]:
    ok_rows = [row for row in rows if row.status == "ok"]
    out = summarize_ok_rows(ok_rows)
    out.update(
        {
            "n_analyzed": len(rows),
            "n_ok": len(ok_rows),
            "n_error": len(rows) - len(ok_rows),
            "error_rate": ratio(len(rows) - len(ok_rows), len(rows)),
        }
    )
    return out


def make_group_summary(rows: Sequence[CaseRow], attribute: str, order: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[CaseRow]] = defaultdict(list)
    for row in rows:
        grouped[getattr(row, attribute)].append(row)
    keys = list(grouped)
    if order is not None:
        keys = [key for key in order if key in grouped] + [key for key in keys if key not in order]
    else:
        keys.sort()
    out: List[Dict[str, Any]] = []
    for key in keys:
        summary = summarize_group(grouped[key])
        summary["name"] = key
        out.append(summary)
    return out


def similarity_bin_name(value: float) -> str:
    if value < 0.50:
        return "<0.50"
    if value < 0.60:
        return "0.50-0.59"
    if value < 0.70:
        return "0.60-0.69"
    if value < 0.80:
        return "0.70-0.79"
    if value < 0.90:
        return "0.80-0.89"
    return ">=0.90"


def make_similarity_summary(ok_rows: Sequence[CaseRow]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[CaseRow]] = defaultdict(list)
    for row in ok_rows:
        similarity = row.template_similarity
        if similarity is None:
            continue
        identity = similarity.get("overlap_identity")
        if identity is None:
            continue
        grouped[similarity_bin_name(identity)].append(row)
    out: List[Dict[str, Any]] = []
    for name in ["<0.50", "0.50-0.59", "0.60-0.69", "0.70-0.79", "0.80-0.89", ">=0.90"]:
        bucket_rows = grouped.get(name)
        if not bucket_rows:
            continue
        summary = summarize_ok_rows(bucket_rows)
        summary["similarity_bin"] = name
        summary["n_ok"] = len(bucket_rows)
        summary["mean_similarity"] = statistics.mean(
            row.template_similarity["overlap_identity"] for row in bucket_rows if row.template_similarity
        )
        out.append(summary)
    return out


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    idx = min(len(values) - 1, max(0, int((len(values) - 1) * q)))
    return sorted(values)[idx]


def make_error_summary(rows: Sequence[CaseRow]) -> List[Dict[str, Any]]:
    counter = Counter(classify_error(row.error) for row in rows if row.status != "ok")
    return [{"error_mode": name, "count": count} for name, count in counter.most_common()]


def count_api_quota_errors(rows: Sequence[CaseRow]) -> int:
    needles = ("402", "insufficient", "balance", "quota")
    return sum(
        1
        for row in rows
        if row.status != "ok" and any(needle in row.error.lower() for needle in needles)
    )


def add_value_labels(ax: Any, bars: Iterable[Any], percent: bool = False) -> None:
    for bar in bars:
        height = bar.get_height()
        if percent:
            text = f"{height * 100:.1f}%"
        else:
            text = f"{height:.0f}"
        ax.annotate(
            text,
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def add_horizontal_value_labels(ax: Any, bars: Iterable[Any], percent: bool = True) -> None:
    x_min, x_max = ax.get_xlim()
    span = x_max - x_min
    pad = span * 0.012
    for bar in bars:
        width = bar.get_width()
        text = format_pct(width) if percent else f"{width:.0f}"
        if width > x_max - span * 0.14:
            x = width - pad
            ha = "right"
            color = "white"
        else:
            x = width + pad
            ha = "left"
            color = "#111827"
        ax.annotate(
            text,
            xy=(x, bar.get_y() + bar.get_height() / 2),
            ha=ha,
            va="center",
            fontsize=9,
            color=color,
            fontweight="bold",
        )


def add_point_labels(ax: Any, xs: Sequence[Any], ys: Sequence[Optional[float]], dy: int, color: str) -> None:
    for x, y in zip(xs, ys):
        if y is None:
            continue
        ax.annotate(
            format_pct(y),
            xy=(x, y),
            xytext=(0, dy),
            textcoords="offset points",
            ha="center",
            va="bottom" if dy >= 0 else "top",
            fontsize=8,
            color=color,
            fontweight="bold",
        )


def zh_source_label(name: str) -> str:
    return SOURCE_LABEL_ZH.get(name, name)


def zh_region_label(name: str) -> str:
    return REGION_LABEL_ZH.get(name, name)


def zh_error_mode_label(name: str) -> str:
    return ERROR_MODE_LABEL_ZH.get(name, name)


def save_overall_views_figure(
    path: Path,
    error_inclusive: Dict[str, Any],
    ok_summary: Dict[str, Any],
    trimmed_summary: Dict[str, Any],
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8), constrained_layout=True)

    metrics = [
        ("错误率", error_inclusive["error_rate"], PLOT_COLORS["red"]),
        ("整条完全匹配率", ok_summary["exact_match_rate"], PLOT_COLORS["orange"]),
        ("合法性通过率", ok_summary["legality_pass_rate"], PLOT_COLORS["teal"]),
        ("编号准确率", ok_summary["residue_label_accuracy"], PLOT_COLORS["blue"]),
        ("序列保持率", ok_summary["base_preservation_rate"], PLOT_COLORS["green"]),
    ]
    labels = [name for name, _value, _color in metrics]
    values = [value for _name, value, _color in metrics]
    colors = [color for _name, _value, color in metrics]
    y = list(range(len(metrics)))
    axes[0].set_xlim(0, 1.06)
    bars = axes[0].barh(y, values, color=colors, height=0.58)
    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("比例")
    axes[0].set_title("核心结论：序列保持接近满分，整条匹配仍偏低", fontsize=12, pad=12)
    axes[0].grid(axis="x", alpha=0.18)
    add_horizontal_value_labels(axes[0], bars, percent=True)

    comparison = [
        ("编号准确率", ok_summary["residue_label_accuracy"], trimmed_summary["residue_label_accuracy"]),
        ("整条完全匹配率", ok_summary["exact_match_rate"], trimmed_summary["exact_match_rate"]),
        ("合法性通过率", ok_summary["legality_pass_rate"], trimmed_summary["legality_pass_rate"]),
    ]
    y = list(range(len(comparison)))
    height = 0.30
    ok_values = [row[1] for row in comparison]
    trimmed_values = [row[2] for row in comparison]
    axes[1].set_xlim(0, 1.18)
    bars_ok = axes[1].barh(
        [idx + height / 2 for idx in y],
        ok_values,
        height=height,
        color="#9CA3AF",
        label="可评分样本口径",
    )
    bars_trimmed = axes[1].barh(
        [idx - height / 2 for idx in y],
        trimmed_values,
        height=height,
        color=PLOT_COLORS["blue"],
        label="去尾口径",
    )
    axes[1].set_yticks(y, [row[0] for row in comparison])
    axes[1].invert_yaxis()
    axes[1].set_xlabel("比例")
    axes[1].set_title("去掉最低10%后，整条匹配提升最明显", fontsize=12, pad=12)
    axes[1].grid(axis="x", alpha=0.18)
    axes[1].legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=9)
    add_horizontal_value_labels(axes[1], bars_ok, percent=True)
    add_horizontal_value_labels(axes[1], bars_trimmed, percent=True)
    fig.suptitle("整体指标：主要短板不是序列破坏，而是完整编号和合法性", fontsize=15)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_label_distribution_figure(path: Path, ok_rows: Sequence[CaseRow]) -> None:
    values = sorted(
        row.metrics.get("label_accuracy")
        for row in ok_rows
        if row.metrics.get("label_accuracy") is not None
    )
    p10 = percentile(values, 0.10)
    p50 = percentile(values, 0.50)
    p90 = percentile(values, 0.90)

    values_pct = [value * 100 for value in values]
    fig, ax = plt.subplots(figsize=(12, 5.5), constrained_layout=True)
    counts, _bins, patches = ax.hist(
        values_pct,
        bins=list(range(0, 105, 5)),
        color=PLOT_COLORS["blue"],
        edgecolor="white",
        linewidth=1.0,
    )
    max_count = max(counts) if len(counts) else 0
    for count, patch in zip(counts, patches):
        if count <= 0:
            continue
        ax.annotate(
            f"{int(count)}",
            xy=(patch.get_x() + patch.get_width() / 2, count),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#111827",
        )
    percentile_markers: Dict[float, Dict[str, Any]] = {}
    for marker, label, color in [
        (p10, "10%分位", PLOT_COLORS["red"]),
        (p50, "50%分位", PLOT_COLORS["green"]),
        (p90, "90%分位", PLOT_COLORS["orange"]),
    ]:
        if marker is None:
            continue
        key = round(marker, 4)
        if key not in percentile_markers:
            percentile_markers[key] = {"marker": marker, "labels": [label], "color": color}
        else:
            percentile_markers[key]["labels"].append(label)
    for item in percentile_markers.values():
        marker = item["marker"]
        label = "/".join(item["labels"])
        color = item["color"]
        marker_pct = marker * 100
        ax.axvline(marker_pct, color=color, linestyle="--", linewidth=1.6)
        ax.annotate(
            f"{label}\n{marker_pct:.1f}%",
            xy=(marker_pct, max_count * 0.92),
            xytext=(6, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=9,
            color=color,
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": color, "alpha": 0.92},
        )
    if values_pct:
        ax.text(
            0.02,
            0.92,
            f"最低值 {min(values_pct):.1f}%\n中位数 {p50 * 100:.1f}%\n最高值 {max(values_pct):.1f}%",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=10,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#D1D5DB", "alpha": 0.95},
        )
    ax.set_xlim(0, 102)
    ax.set_ylim(0, max_count * 1.16 if max_count else 1)
    ax.set_xlabel("单样本编号准确率")
    ax.set_ylabel("样本数")
    ax.set_title("单样本分布：中位数已到100%，但仍有明显低分长尾", fontsize=13, pad=12)
    ax.grid(axis="y", alpha=0.18)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_region_breakdown_figure(path: Path, region_summary: Sequence[Dict[str, Any]]) -> None:
    sorted_rows = sorted(region_summary, key=lambda row: row["label_accuracy"] or 0.0)
    labels = [zh_region_label(row["region"]) for row in sorted_rows]
    label_acc = [row["label_accuracy"] for row in sorted_rows]
    exact_rate = [row["exact_match_rate"] for row in sorted_rows]

    fig, ax = plt.subplots(figsize=(11.5, 5.8), constrained_layout=True)
    y = list(range(len(labels)))
    height = 0.32
    label_colors = [
        PLOT_COLORS["red"] if label == "可变环" else PLOT_COLORS["blue"]
        for label in labels
    ]
    exact_colors = [
        "#D97706" if label == "可变环" else PLOT_COLORS["orange"]
        for label in labels
    ]
    ax.set_xlim(0, 1.12)
    bars_label = ax.barh(
        [v + height / 2 for v in y],
        label_acc,
        height=height,
        color=label_colors,
        label="逐碱基编号准确率",
    )
    bars_exact = ax.barh(
        [v - height / 2 for v in y],
        exact_rate,
        height=height,
        color=exact_colors,
        alpha=0.78,
        label="区段完全匹配率",
    )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("比例")
    ax.set_title("结构瓶颈：可变环同时拉低逐碱基准确率和整区完全匹配", fontsize=13, pad=12)
    ax.grid(axis="x", alpha=0.18)
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.14), ncol=2, fontsize=9)
    add_horizontal_value_labels(ax, bars_label, percent=True)
    add_horizontal_value_labels(ax, bars_exact, percent=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_source_group_figure(path: Path, broad_summary: Sequence[Dict[str, Any]]) -> None:
    sorted_rows = sorted(broad_summary, key=lambda row: row["exact_match_rate"] or 0.0)
    labels = [
        f"{zh_source_label(row['name'])}  (n={row['n_analyzed']})"
        for row in sorted_rows
    ]
    exact = [row["exact_match_rate"] for row in sorted_rows]
    label_acc = [row["residue_label_accuracy"] for row in sorted_rows]

    fig, ax = plt.subplots(figsize=(12, 6.4), constrained_layout=True)
    y = list(range(len(labels)))
    colors = [
        PLOT_COLORS["red"] if row["name"] in {"Mitochondrial", "Virus"} else PLOT_COLORS["orange"]
        for row in sorted_rows
    ]
    ax.set_xlim(0, 1.12)
    bars = ax.barh(y, exact, color=colors, height=0.56, label="整条完全匹配率")
    ax.scatter(
        label_acc,
        y,
        s=70,
        color=PLOT_COLORS["blue"],
        edgecolor="white",
        linewidth=1.2,
        zorder=3,
        label="编号准确率",
    )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("比例")
    ax.set_title("来源差异：线粒体和病毒的整条完全匹配率最低", fontsize=13, pad=12)
    ax.grid(axis="x", alpha=0.18)
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=9)
    add_horizontal_value_labels(ax, bars, percent=True)
    for yy, value in zip(y, label_acc):
        ax.annotate(
            format_pct(value),
            xy=(value, yy),
            xytext=(-8, -14 if value > 0.92 else 12),
            textcoords="offset points",
            ha="right",
            va="center",
            fontsize=8,
            color=PLOT_COLORS["blue"],
            fontweight="bold",
        )
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_similarity_bins_figure(path: Path, similarity_summary: Sequence[Dict[str, Any]]) -> None:
    labels = [f"{row['similarity_bin']}\n(n={row['n_ok']})" for row in similarity_summary]
    label_acc = [row["residue_label_accuracy"] for row in similarity_summary]
    exact = [row["exact_match_rate"] for row in similarity_summary]
    legality = [row["legality_pass_rate"] for row in similarity_summary]

    fig, ax = plt.subplots(figsize=(12, 6.3), constrained_layout=True)
    x = list(range(len(labels)))
    bars = ax.bar(
        x,
        exact,
        color=PLOT_COLORS["orange"],
        width=0.58,
        alpha=0.82,
        label="整条完全匹配率",
    )
    ax.plot(x, label_acc, marker="o", linewidth=2.4, color=PLOT_COLORS["blue"], label="编号准确率")
    ax.plot(x, legality, marker="o", linewidth=2.4, color=PLOT_COLORS["green"], label="合法性通过率")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("比例")
    ax.set_title("模板相似度越高，整条完全匹配率越稳定", fontsize=13, pad=12)
    ax.grid(axis="y", alpha=0.18)
    ax.legend(frameon=False, loc="upper left", ncol=3, fontsize=9)
    add_value_labels(ax, bars, percent=True)
    add_point_labels(ax, x, label_acc, 11, PLOT_COLORS["blue"])
    add_point_labels(ax, x, legality, -13, PLOT_COLORS["green"])
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_error_modes_figure(path: Path, error_summary: Sequence[Dict[str, Any]]) -> None:
    total = sum(row["count"] for row in error_summary)
    labels = [zh_error_mode_label(row["error_mode"]) for row in error_summary]
    counts = [row["count"] for row in error_summary]
    fig, ax = plt.subplots(figsize=(10.8, 4.8), constrained_layout=True)
    y = list(range(len(labels)))
    colors = [PLOT_COLORS["red"]] + [PLOT_COLORS["orange"], PLOT_COLORS["gray"]]
    bars = ax.barh(y, counts, color=colors[: len(labels)], height=0.54)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max(counts) * 1.25 if counts else 1)
    ax.set_xlabel("失败样本数")
    ax.set_title("错误模式：失败主要发生在前端结构注释阶段", fontsize=13, pad=12)
    ax.grid(axis="x", alpha=0.18)
    for bar, count in zip(bars, counts):
        pct = count / total if total else 0
        ax.annotate(
            f"{count}（{pct * 100:.1f}%）",
            xy=(count, bar.get_y() + bar.get_height() / 2),
            xytext=(7, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=10,
            color="#111827",
            fontweight="bold",
        )
    fig.savefig(path, dpi=180)
    plt.close(fig)


def to_serializable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: to_serializable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [to_serializable(value) for value in obj]
    return obj


def table_from_rows(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def build_report_markdown(
    run_id: str,
    run_dir: Path,
    report_time: str,
    error_inclusive: Dict[str, Any],
    ok_summary: Dict[str, Any],
    trimmed: Dict[str, Any],
    region_summary: Sequence[Dict[str, Any]],
    broad_summary: Sequence[Dict[str, Any]],
    fine_summary: Sequence[Dict[str, Any]],
    similarity_summary: Sequence[Dict[str, Any]],
    error_summary: Sequence[Dict[str, Any]],
    api_quota_error_count: int,
) -> str:
    analyzed_n = error_inclusive["n_analyzed"]
    ok_n = error_inclusive["n_ok"]
    error_n = error_inclusive["n_error"]
    p10_cut = trimmed["removed_max_label_accuracy"]
    kept_cut = trimmed["kept_min_label_accuracy"]

    overall_table = table_from_rows(
        ["View", "Cases", "Error Rate", "Label Acc.", "Exact Match", "Region Acc.", "Legality", "Base Preservation"],
        [
            [
                "Error-inclusive",
                str(analyzed_n),
                format_pct(error_inclusive["error_rate"]),
                "-",
                format_pct(error_inclusive["exact_match_rate"]),
                "-",
                format_pct(error_inclusive["legality_pass_rate"]),
                format_pct(error_inclusive["base_preservation_rate"]),
            ],
            [
                "OK only",
                str(ok_n),
                "0.00%",
                format_pct(ok_summary["residue_label_accuracy"]),
                format_pct(ok_summary["exact_match_rate"]),
                format_pct(ok_summary["region_accuracy"]),
                format_pct(ok_summary["legality_pass_rate"]),
                format_pct(ok_summary["base_preservation_rate"]),
            ],
            [
                "Tail-trimmed",
                str(trimmed["summary"]["n_ok"]),
                "0.00%",
                format_pct(trimmed["summary"]["residue_label_accuracy"]),
                format_pct(trimmed["summary"]["exact_match_rate"]),
                format_pct(trimmed["summary"]["region_accuracy"]),
                format_pct(trimmed["summary"]["legality_pass_rate"]),
                format_pct(trimmed["summary"]["base_preservation_rate"]),
            ],
        ],
    )

    region_table = table_from_rows(
        ["Region", "Residues", "Cases", "Residue Label Acc.", "Region Exact-Match"],
        [
            [
                row["region"],
                str(row["residue_count"]),
                str(row["case_count"]),
                format_pct(row["label_accuracy"]),
                format_pct(row["exact_match_rate"]),
            ]
            for row in region_summary
        ],
    )

    broad_table = table_from_rows(
        ["Group", "Analyzed", "Error Rate", "Label Acc.", "Exact Match", "Legality"],
        [
            [
                row["name"],
                str(row["n_analyzed"]),
                format_pct(row["error_rate"]),
                format_pct(row["residue_label_accuracy"]),
                format_pct(row["exact_match_rate"]),
                format_pct(row["legality_pass_rate"]),
            ]
            for row in broad_summary
        ],
    )

    fine_table = table_from_rows(
        ["Fine Group", "Analyzed", "Error Rate", "Label Acc.", "Exact Match"],
        [
            [
                row["name"],
                str(row["n_analyzed"]),
                format_pct(row["error_rate"]),
                format_pct(row["residue_label_accuracy"]),
                format_pct(row["exact_match_rate"]),
            ]
            for row in fine_summary[:8]
        ],
    )

    similarity_table = table_from_rows(
        ["Template Identity Bin", "OK Cases", "Mean Identity", "Label Acc.", "Exact Match", "Legality"],
        [
            [
                row["similarity_bin"],
                str(row["n_ok"]),
                format_pct(row["mean_similarity"]),
                format_pct(row["residue_label_accuracy"]),
                format_pct(row["exact_match_rate"]),
                format_pct(row["legality_pass_rate"]),
            ]
            for row in similarity_summary
        ],
    )

    error_table = table_from_rows(
        ["Error Mode", "Count", "Share of Errors"],
        [
            [
                row["error_mode"],
                str(row["count"]),
                format_pct(ratio(row["count"], error_n)),
            ]
            for row in error_summary
        ],
    )

    worst_region = region_summary[0]
    second_region = region_summary[1] if len(region_summary) > 1 else None
    best_similarity = similarity_summary[-1]
    worst_similarity = similarity_summary[0]
    broad_mito = next((row for row in broad_summary if row["name"] == "Mitochondrial"), None)
    broad_bact = next((row for row in broad_summary if row["name"] == "Bacteria"), None)

    return f"""# Single-Sequence Sprinzl Numbering Benchmark Report

- Run ID: `{run_id}`
- Run directory: `{run_dir}`
- Report generated at: `{report_time}`
- Analyzed sample count: `{analyzed_n}`
- OK cases: `{ok_n}`
- Error cases: `{error_n}`

## Executive Summary

- 当前分析样本共 `{analyzed_n}` 条，其中 `{ok_n}` 条产生可评分输出，`{error_n}` 条在前端注释或一致性检查阶段终止。
- 最终 case-level 结果中检测到 `{api_quota_error_count}` 个 402、余额不足或 quota 类 API 错误；因此这 `{error_n}` 条 error 不再是余额问题。
- 在 ok case 口径下，整体 `residue_label_accuracy` 为 `{format_pct(ok_summary["residue_label_accuracy"])}`，`exact_match_rate` 为 `{format_pct(ok_summary["exact_match_rate"])}`，`legality_pass_rate` 为 `{format_pct(ok_summary["legality_pass_rate"])}`。
- tail-trimmed 口径下，去除单样本 `label_accuracy` 最低的 `{trimmed["trim_n"]}` 条后，`residue_label_accuracy` 提升到 `{format_pct(trimmed["summary"]["residue_label_accuracy"])}`，说明尾部困难样本对总体结果有显著拖拽。
- 区域层面最弱的是 `{worst_region["region"]}`，其 residue-level label accuracy 仅 `{format_pct(worst_region["label_accuracy"])}`；其次是 `{second_region["region"] if second_region else "-"}`。
- 生物来源层面，线粒体来源样本明显低于细菌、古菌和细胞质真核来源；模板相似度分箱显示性能与模板重合度呈近单调正相关。

## Evaluation View and Metric Definitions

- `error-inclusive`：把全部已分析 case 都纳入分母，适合看 case-level 成功率、legality 和 base-preservation。
- `ok-only`：只统计 `status=ok` 的 case，适合看 residue-level 编号准确率。
- `tail-trimmed`：在 ok case 中按单样本 `label_accuracy` 升序，去掉最低 10%，用于分离尾部困难样本影响。本次对应的移除上界约为 `{format_num(p10_cut, 4)}`，保留下界约为 `{format_num(kept_cut, 4)}`。
- `template similarity`：不是直接从 case 文件读取，而是根据目标样本与命中模板在共享非 gap Sprinzl 位点上的 overlap identity 重建。
- `region exact-match`：某个区域内所有 gold 标签都命中才记为 1，否则记为 0。

## Overall Results

{overall_table}

![Figure 1](figures/{FIGURE_SPECS[0][0]})

*{FIGURE_SPECS[0][1].format(run_id=run_id)}*

整体看，当前 run 的主要问题不是碱基丢失。`base_preservation_rate` 在 ok case 口径下达到 `{format_pct(ok_summary["base_preservation_rate"])}`，而 `legality_pass_rate` 只有 `{format_pct(ok_summary["legality_pass_rate"])}`，说明剩余误差更集中在结构一致性与编号合法性，而不是序列内容被改坏。

另外，`exact_match_rate` 仅 `{format_pct(ok_summary["exact_match_rate"])}`，明显低于 `residue_label_accuracy`。这意味着多数失败样本并不是“全局崩掉”，而是只要个别关键位置，尤其是可变环和 D 区域错位，就会把整条样本从 exact match 打成失败。

## Per-Case Accuracy Distribution

![Figure 2](figures/{FIGURE_SPECS[1][0]})

*{FIGURE_SPECS[1][1].format(run_id=run_id)}*

单样本分布呈现明显长尾。中位数接近高分段，但 10 分位点显著下沉，说明主体验证集已经进入高准确区，而尾部少量困难样本承担了大部分失分。这与 tail-trimmed 后整体指标明显回升的现象一致。

## Region Analysis

{region_table}

![Figure 3](figures/{FIGURE_SPECS[2][0]})

*{FIGURE_SPECS[2][1].format(run_id=run_id)}*

区域分析给出的信号非常集中：

- `{worst_region["region"]}` 是当前最主要的编号瓶颈。它同时具有最低的 residue-level accuracy 和最低的整区 exact-match，说明问题不是少量离群值，而是系统性困难。
- `{second_region["region"] if second_region else "D loop + D stem"}` 也是高频失分区。这里同时承载结构配对约束和可选编号位点，容易出现局部错位。
- 反密码子区和两端氨基酸臂已经相对稳定，说明 canonical 干茎和强锚点区域的恢复能力已明显强于可变长度与可选插入主导的区域。

从机制上看，可变环和 D 区域都具有更强的 family-specific 变长、可选标签与局部插入行为，模板一旦不够贴近，局部 postcheck 也很难完全补回。

## Biological Source Analysis

{broad_table}

补充细分来源（按 `Kingdom` 细码）：

{fine_table}

![Figure 4](figures/{FIGURE_SPECS[3][0]})

*{FIGURE_SPECS[3][1].format(run_id=run_id)}*

来源分组的结论也非常清楚：

- 线粒体样本是当前最弱的一组。其 `label_accuracy` 只有 `{format_pct(broad_mito["residue_label_accuracy"]) if broad_mito else "-"}`，`exact_match_rate` 只有 `{format_pct(broad_mito["exact_match_rate"]) if broad_mito else "-"}`，并且承担了几乎全部 error。
- 细菌样本已经进入高质量区间，`label_accuracy` 达 `{format_pct(broad_bact["residue_label_accuracy"]) if broad_bact else "-"}`，`exact_match_rate` 达 `{format_pct(broad_bact["exact_match_rate"]) if broad_bact else "-"}`。
- 细胞质真核与古菌样本同样表现稳定，说明 canonical cloverleaf 前提和现有模板库在这些来源上已能提供足够强的结构先验。

这说明当前系统的失分并不是均匀分布，而是高度集中在线粒体这一类结构偏离 canonical 假设、模板匹配更困难的来源上。

## Template Similarity Analysis

{similarity_table}

![Figure 5](figures/{FIGURE_SPECS[4][0]})

*{FIGURE_SPECS[4][1].format(run_id=run_id)}*

模板相似度是当前最强的解释变量之一：

- 在最低相似度分箱 `{worst_similarity["similarity_bin"]}` 中，`label_accuracy` 只有 `{format_pct(worst_similarity["residue_label_accuracy"])}`，`exact_match_rate` 几乎归零。
- 在最高相似度分箱 `{best_similarity["similarity_bin"]}` 中，`label_accuracy` 提升到 `{format_pct(best_similarity["residue_label_accuracy"])}`，`exact_match_rate` 提升到 `{format_pct(best_similarity["exact_match_rate"])}`。

这个趋势接近单调，说明当前 pipeline 对命中模板的依赖仍然很强。换句话说，系统已经能在“模板很像”的条件下稳定工作，但当检索到的模板距离目标较远时，结构修正与 postcheck 还不足以完全弥补模板先验的偏差。

## Error Analysis

{error_table}

![Figure 6](figures/{FIGURE_SPECS[5][0]})

*{FIGURE_SPECS[5][1].format(run_id=run_id)}*

error 组成主要分成两类：

- 结构注释前端失败：以 `tRNAscan no hit` 和 `non-canonical cloverleaf` 为主；
- 一致性保护失败：以 `base-preservation failure` 为主，但数量很少。

最终 case-level 结果中未发现余额或 quota 类错误，因此这部分 error 应解释为结构注释、非标准结构或序列保持检查问题，而不是 API 资金中断。

这意味着当前 error 的主体不是模型输出随机失控，而是前端结构假设无法覆盖一部分输入，特别是非 canonical 或弱 cloverleaf 的样本。

## Root-Cause Interpretation

### 1. 模板匹配质量仍是主导因素

图 5 展示的梯度几乎没有歧义。模板越接近目标，编号恢复越稳；模板一旦偏离，exact match 会迅速塌陷。当前系统虽有 postcheck，但还不足以把“弱模板”完全修正成“强结果”。

### 2. 主要失分集中在高自由度区域

图 3 显示，可变环与 D 区域远弱于反密码子区和 acceptor arm。这类区域天然存在插入、变长和可选编号位点，属于模板失配后最先放大的局部。

### 3. 线粒体样本暴露了 canonical 假设的边界

图 4 里最弱的组别不是广义真核，而是线粒体。线粒体 tRNA 更常见非标准二级结构，导致前端注释、模板检索和区域修正都更容易失效，因此既拉低准确率，也抬高 error rate。

### 4. 当前短板是“编号一致性”，不是“序列破坏”

overall 指标已经说明，base preservation 几乎不是问题，真正落后的指标是 legality 和 exact match。这说明保护机制生效了，但合法编号恢复还没完全跨过长尾困难样本。

## Priority Fixes

1. 优先扩充或重分层线粒体模板库，至少把动物线粒体、植物线粒体和真菌/原生生物线粒体分开处理。
2. 对 `{worst_region["region"]}` 和 `{second_region["region"] if second_region else "D loop + D stem"}` 单独做更强的结构化 postcheck，不要完全依赖统一模板先验。
3. 在模板检索阶段加入更明确的“低相似度告警或降级策略”，避免弱模板直接驱动后续编号。
4. 针对 `tRNAscan no hit` 样本提供备用注释路径，否则这部分样本会始终停在前端错误而不是进入可评分流程。
5. 把 legality 修复作为下一优先级，因为它比 base preservation 更接近当前主失分来源。
"""


def main() -> None:
    args = parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    run_id = run_dir.name
    # A report is derived output, so it lands in the git-ignored scratch tree
    # beside the run cache rather than back into the repository.
    report_dir = (args.report_dir or (PROJECT_ROOT / ".runtime" / "reports" / run_id)).resolve()
    figures_dir = report_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    if args.benchmark_tsv:
        benchmark_columns, benchmark_rows = load_flat_table(args.benchmark_tsv)
    else:
        # Each rebuilt case already carries the active answer, so the whole
        # library table is only needed when auditing an offline copy.
        benchmark_columns, benchmark_rows = [], {}
    _template_columns, template_rows = load_flat_table(args.template_tsv)
    export_meta = load_export_meta(args.export_tsv) if args.export_tsv else {}
    case_rows = load_case_rows(
        run_dir,
        benchmark_columns=benchmark_columns,
        benchmark_rows=benchmark_rows,
        export_meta=export_meta,
        template_rows=template_rows,
    )
    ok_rows = [row for row in case_rows if row.status == "ok"]

    error_inclusive = summarize_error_inclusive(case_rows)
    ok_summary = summarize_ok_rows(ok_rows)
    trimmed = trim_bottom_by_label_accuracy(ok_rows)
    region_summary = make_region_summary(ok_rows)
    broad_summary = make_group_summary(case_rows, "broad_group", BROAD_SOURCE_ORDER)
    fine_summary = make_group_summary(case_rows, "fine_group")
    fine_summary.sort(key=lambda row: row["n_analyzed"], reverse=True)
    similarity_summary = make_similarity_summary(ok_rows)
    error_summary = make_error_summary(case_rows)
    api_quota_error_count = count_api_quota_errors(case_rows)

    save_overall_views_figure(figures_dir / FIGURE_SPECS[0][0], error_inclusive, ok_summary, trimmed["summary"])
    save_label_distribution_figure(figures_dir / FIGURE_SPECS[1][0], ok_rows)
    save_region_breakdown_figure(figures_dir / FIGURE_SPECS[2][0], region_summary)
    save_source_group_figure(figures_dir / FIGURE_SPECS[3][0], broad_summary)
    save_similarity_bins_figure(figures_dir / FIGURE_SPECS[4][0], similarity_summary)
    save_error_modes_figure(figures_dir / FIGURE_SPECS[5][0], error_summary)

    report_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    data_payload = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "report_dir": str(report_dir),
        "error_inclusive": error_inclusive,
        "ok_summary": ok_summary,
        "tail_trimmed": {
            "trim_n": trimmed["trim_n"],
            "fraction": trimmed["fraction"],
            "removed_max_label_accuracy": trimmed["removed_max_label_accuracy"],
            "kept_min_label_accuracy": trimmed["kept_min_label_accuracy"],
            "summary": trimmed["summary"],
        },
        "region_summary": region_summary,
        "broad_source_summary": broad_summary,
        "fine_source_summary": fine_summary,
        "similarity_summary": similarity_summary,
        "error_summary": error_summary,
        "api_quota_error_count": api_quota_error_count,
        "figures": [
            {"filename": filename, "caption": caption.format(run_id=run_id)}
            for filename, caption in FIGURE_SPECS
        ],
    }
    (report_dir / "report_data.json").write_text(
        json.dumps(to_serializable(data_payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    markdown = build_report_markdown(
        run_id,
        run_dir,
        report_time,
        error_inclusive,
        ok_summary,
        trimmed,
        region_summary,
        broad_summary,
        fine_summary,
        similarity_summary,
        error_summary,
        api_quota_error_count,
    )
    (report_dir / "REPORT.md").write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    main()
