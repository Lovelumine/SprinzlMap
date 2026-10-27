#!/usr/bin/env python3
"""Build the three-panel main benchmark figure for the 2,530-case cohort.

Panel A compares the complete workflow with two deterministic CM controls using
whole-sequence exact match as the primary visual measure and pooled residue
accuracy as a compact companion value. Panel B stratifies the complete workflow
by source domain. Panel C separates process disposition from correctness within
each disposition group.

The main run is materialized from MySQL and MinIO by ``resolve_run_dir``. The
control values are read from the sealed, same-cohort ablation table and checked
against its provenance record. No model is called by this script.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "APP"))
from backend.infra.storage.benchmark_run_store import resolve_run_dir  # noqa: E402

PAPER = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "20261020-164533-deepseekv4-flash-guangdong"
ABLATION_TABLE = PAPER / "results/ablation_ladder_2530.tsv"
ABLATION_PROVENANCE = PAPER / "results/ablation_ladder_2530_provenance.json"

RUN_ID = DEFAULT_RUN_ID
RUN_DIR = Path()
OUT = PAPER / "results/figure2"
FIGURES = PAPER / "figures"
INPUT_MEMBERSHIP = Path()

DOMAINS = ("archaea", "bacteria", "cytosolic_eukaryote")
DOMAIN_LABELS = {
    "archaea": "Archaea",
    "bacteria": "Bacteria",
    "cytosolic_eukaryote": "Cytosolic eukaryotes",
}
STATUS_LABELS = {"accepted": "Accepted", "investigate": "Review"}

# A restrained, colour-blind-friendly palette. The full workflow is the only
# saturated series; controls recede to neutral blue-grey.
NAVY = "#1F5A73"
MID_BLUE = "#7393A3"
LIGHT_BLUE = "#B8C5CC"
REVIEW = "#C8924B"
REVIEW_LIGHT = "#E8D7BD"
INK = "#26333B"
MUTED = "#66737C"
GRID = "#E6EBEE"

CONTROL_RUNS = {
    "cm_root": "20261023-043053-control-harness-cm-root",
    "cm_direct": "20261023-041911-control-cm-direct",
}
METHOD_LABELS = {
    DEFAULT_RUN_ID: "Complete workflow",
    CONTROL_RUNS["cm_root"]: "CM + rules",
    CONTROL_RUNS["cm_direct"]: "CM only",
}
METHOD_COLORS = {
    DEFAULT_RUN_ID: NAVY,
    CONTROL_RUNS["cm_root"]: MID_BLUE,
    CONTROL_RUNS["cm_direct"]: LIGHT_BLUE,
}

# Direct-generation control: the same model receives only the sequence and must
# emit one Sprinzl label per residue. Numbers come from the sealed
# 20261026-2257-control-raw-llm-deepseek run; answers that cannot be read as one
# label per residue are scored zero rather than dropped.
RAW_LLM_ARM = {
    "run_id": "20261026-2257-control-raw-llm-deepseek",
    "method": "Direct LLM",
    "n": 2530,
    "residue_accuracy": 0.66,
    "exact_match": 0.04,
    "exact_n": 1,
    "usable": 24,
    "color": "#C9C3BD",
}


def configure(run_id: str = DEFAULT_RUN_ID, out_dir: Path | None = None) -> None:
    global RUN_ID, RUN_DIR, OUT, FIGURES, INPUT_MEMBERSHIP
    RUN_ID = str(run_id)
    RUN_DIR = resolve_run_dir(RUN_ID)
    INPUT_MEMBERSHIP = RUN_DIR / "inputs/cohort_membership.tsv"
    if out_dir is None:
        OUT = PAPER / "results/figure2"
        FIGURES = PAPER / "figures"
    else:
        OUT = Path(out_dir)
        FIGURES = OUT


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(name: str, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {name}")
    with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_cohort() -> list[dict]:
    membership = read_tsv(INPUT_MEMBERSHIP)
    names = [row["seqname"] for row in membership]
    if len(names) != 2530 or len(set(names)) != 2530:
        raise ValueError("the selected run does not contain the 2,530-case cohort")
    expected = {"archaea": 408, "bacteria": 1200, "cytosolic_eukaryote": 922}
    if Counter(row["source"] for row in membership) != expected:
        raise ValueError("the selected run has a different domain roster")

    cases: dict[str, dict] = {}
    for path in (RUN_DIR / "cases").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row["seqname"] in cases:
            raise ValueError(f"duplicate case: {row['seqname']}")
        cases[row["seqname"]] = row
    if set(names) != set(cases):
        raise ValueError("the stored run is not exactly the frozen cohort")

    output: list[dict] = []
    for roster in membership:
        result = cases[roster["seqname"]]
        if result.get("status") != "ok":
            raise ValueError(f"case did not complete: {roster['seqname']}")
        metrics = result.get("metrics") or {}
        scored = bool(result.get("gold_evaluation_allowed", True))
        total = int(metrics.get("label_total") or 0) if scored else 0
        correct = int(metrics.get("label_correct") or 0) if scored else 0
        exact = int(bool(metrics.get("exact_match"))) if scored else 0
        if scored and (total <= 0 or not 0 <= correct <= total):
            raise ValueError(f"invalid score for {roster['seqname']}")
        if scored and bool(metrics.get("exact_match")) != (correct == total):
            raise ValueError(f"exact-match flag disagrees with residue score for {roster['seqname']}")
        disposition = str((result.get("agent") or {}).get("disposition") or "")
        if disposition not in STATUS_LABELS:
            raise ValueError(f"unknown disposition for {roster['seqname']}: {disposition}")
        output.append({
            "seqname": roster["seqname"],
            "source": roster["source"],
            "disposition": disposition,
            "label_correct": correct,
            "label_total": total,
            "incorrect": total - correct,
            "exact_match": exact,
            "scored": int(scored),
        })
    return sorted(output, key=lambda row: row["seqname"])


def summarize(rows: list[dict], group: str) -> dict:
    scored = [row for row in rows if row["scored"]]
    correct = sum(row["label_correct"] for row in scored)
    total = sum(row["label_total"] for row in scored)
    exact = sum(row["exact_match"] for row in scored)
    accepted = sum(row["disposition"] == "accepted" for row in rows)
    return {
        "group": group,
        "n": len(rows),
        "n_scored": len(scored),
        "label_correct": correct,
        "label_total": total,
        "exact_n": exact,
        "residue_accuracy": correct / total if total else float("nan"),
        "exact_match_rate": exact / len(scored) if scored else float("nan"),
        "automatic_n": accepted,
        "review_n": len(rows) - accepted,
        "automatic_coverage": accepted / len(rows) if rows else float("nan"),
    }


def load_model_metadata(rows: list[dict], overall: dict) -> dict:
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((RUN_DIR / "summary.json").read_text(encoding="utf-8"))
    lineage_path = RUN_DIR / "retry_lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8")) if lineage_path.exists() else {}
    pointer = json.loads((RUN_DIR / "RUN.json").read_text(encoding="utf-8"))
    if manifest.get("run_id") != RUN_ID or manifest.get("status") != "completed":
        raise ValueError("the selected run is not a completed sealed run")
    if manifest.get("target_case_count") != len(rows):
        raise ValueError("run manifest and materialized cohort disagree")

    full = read_tsv(RUN_DIR / "case_results.tsv")
    scored_rows = [row for row in full if str(row.get("gold_evaluation_allowed", "True")).lower() != "false"]
    full_correct = sum(int(row["label_correct"]) for row in scored_rows)
    full_total = sum(int(row["label_total"]) for row in scored_rows)
    full_exact = sum(1 for row in scored_rows if str(row["exact_match"]).lower() in ("1", "true"))
    full_accepted = sum(row["disposition"] == "accepted" for row in full)
    checks = {
        "residue_label_accuracy": full_correct / full_total,
        "exact_match_rate": full_exact / len(scored_rows),
        "legality_pass_rate": full_accepted / len(full),
    }
    for key, value in checks.items():
        if abs(float(summary["metrics"][key]) - value) > 1e-9:
            raise ValueError(f"run summary mismatch for {key}")
    return {
        "manifest": {key: manifest.get(key) for key in (
            "run_id", "status", "provider", "model", "git_commit",
            "git_worktree_dirty", "started_at", "finished_at", "target_case_count",
            "attempt", "method_backend", "agent_planner_policy", "max_workers", "notes")},
        "gold_snapshot_sha256": manifest.get("input_files", {}).get("gold", {}).get("sha256"),
        "run_contract_sha256": manifest.get("run_contract_sha256"),
        "answer_source": summary.get("score_basis"),
        "run_storage": pointer.get("storage"),
        "retry_lineage": {"source_run": lineage.get("source_run"),
                           "reason": lineage.get("reason"),
                           "failure_policy": lineage.get("failure_policy"),
                           "recorded_attempts": pointer.get("attempts")},
        "evaluation_cohort_size": overall["n"],
        "scored_case_count": overall["n_scored"],
        "unscored_cases": [row["seqname"] for row in full if str(row.get("gold_evaluation_allowed", "True")).lower() == "false"],
    }


def load_baseline_metrics(overall: dict) -> list[dict]:
    if not ABLATION_TABLE.exists() or not ABLATION_PROVENANCE.exists():
        raise FileNotFoundError("the sealed ablation ladder is required for panel A")
    rows = read_tsv(ABLATION_TABLE)
    provenance = json.loads(ABLATION_PROVENANCE.read_text(encoding="utf-8"))
    listed_runs = set(provenance.get("runs", []))
    expected_runs = {RUN_ID, *CONTROL_RUNS.values()}
    if not expected_runs.issubset(listed_runs):
        raise ValueError("ablation provenance does not contain all three panel-A runs")
    by_run = {row["run_id"]: row for row in rows}
    output: list[dict] = []
    for run_id in (RUN_ID, CONTROL_RUNS["cm_root"], CONTROL_RUNS["cm_direct"]):
        if run_id == RUN_ID:
            residue = 100 * overall["residue_accuracy"]
            exact = 100 * overall["exact_match_rate"]
            exact_n = overall["exact_n"]
            n = overall["n_scored"]
        else:
            row = by_run.get(run_id)
            if row is None:
                raise ValueError(f"missing baseline row: {run_id}")
            n = int(row["scored_cases"])
            residue = 100 * float(row["residue_accuracy"])
            exact = 100 * float(row["exact_sequence_accuracy"])
            exact_n = round(n * float(row["exact_sequence_accuracy"]))
        if n != 2530:
            raise ValueError(f"panel-A arm {run_id} does not cover 2,530 scored cases")
        output.append({
            "run_id": run_id,
            "method": METHOD_LABELS[run_id],
            "n": n,
            "residue_accuracy": residue,
            "exact_match": exact,
            "exact_n": exact_n,
            "color": METHOD_COLORS[run_id],
        })
    output.append(dict(RAW_LLM_ARM))
    return output


def style() -> None:
    family = "DejaVu Sans"
    candidates = [
        Path.home() / ".local/share/fonts/aylm_fonts/Arial.TTF",
        Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
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
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.linewidth": 0.55,
        "axes.edgecolor": INK,
        "text.color": INK,
        "axes.labelcolor": INK,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })


def clean_axis(ax, grid_axis: str | None = "x") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(INK)
    ax.spines["bottom"].set_color(INK)
    ax.tick_params(length=2.5, width=0.55, colors=INK, pad=2.5)
    ax.set_axisbelow(True)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.45)


def panel_label(fig, x: float, y: float, label: str) -> None:
    fig.text(x, y, label, fontsize=10, fontweight="bold", color=INK,
             va="top", ha="left")


def format_n(value: int) -> str:
    return f"{value:,}"


def plot_method_comparison(ax, methods: list[dict]) -> None:
    # The primary visual is exact sequence match. Residue accuracy is shown as
    # a compact aligned value at the right, preventing a second truncated axis.
    y = np.arange(len(methods))[::-1]
    for yi, row in zip(y, methods):
        ax.hlines(yi, 0, row["exact_match"], color=row["color"], linewidth=3.2,
                  alpha=0.28, zorder=2)
        ax.scatter(row["exact_match"], yi, s=42, color=row["color"],
                   edgecolor="white", linewidth=0.8, zorder=4)
        # Keep a near-zero mark readable: a value at 0.1% would otherwise print
        # on top of the axis labels.
        label_x = max(row["exact_match"] + 1.5, 5.5)
        exact_text = (f"{row['exact_match']:.2f}%" if row["exact_match"] < 1
                      else f"{row['exact_match']:.1f}%")
        ax.text(label_x, yi, exact_text,
                ha="left", va="center", fontsize=8, fontweight="bold", color=INK)
        residue_text = (f"{row['residue_accuracy']:.2f}"
                        if row["residue_accuracy"] < 1 else f"{row['residue_accuracy']:.1f}")
        ax.text(102.0, yi, residue_text,
                ha="left", va="center", fontsize=7.2, color=MUTED)
    ax.set_xlim(0, 116)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Exact sequence match (%)", labelpad=5)
    ax.set_ylim(-0.65, len(methods) - 0.35)
    ax.set_yticks(y)
    ax.set_yticklabels([row["method"] for row in methods])
    ax.tick_params(axis="y", length=0, pad=5)
    clean_axis(ax)
    ax.spines["left"].set_visible(False)
    ax.axvline(methods[-1]["exact_match"], color=GRID, linewidth=0.6, zorder=1)
    ax.text(102.0, len(methods) - 0.42, "Residue accuracy (%)",
            ha="left", va="bottom", fontsize=6.5, color=MUTED)


def plot_domain_stratification(ax_exact, ax_residue, domain_rows: list[dict]) -> None:
    y = np.arange(len(domain_rows))[::-1]
    # Shared row positions make the two metrics easy to read without a large legend.
    for yi, row in zip(y, domain_rows):
        exact = 100 * row["exact_match_rate"]
        residue = 100 * row["residue_accuracy"]
        ax_exact.scatter(exact, yi, s=38, color=NAVY, edgecolor="white", linewidth=0.8, zorder=3)
        ax_exact.text(exact + 5.0, yi, f"{exact:.1f}%", ha="left", va="center",
                      fontsize=7.5, fontweight="bold")
        ax_residue.scatter(residue, yi, s=38, color=MID_BLUE, edgecolor="white", linewidth=0.8, zorder=3)
        ax_residue.text(residue + 1.25, yi, f"{residue:.1f}%", ha="left", va="center",
                        fontsize=7.5, fontweight="bold")
    for ax, limits, ticks, xlabel in (
        (ax_exact, (0, 100), [0, 50, 100], "Exact match (%)"),
        (ax_residue, (95, 100), [95, 97.5, 100], "Residue accuracy (%)"),
    ):
        ax.set_xlim(*limits)
        ax.set_xticks(ticks)
        ax.set_xlabel(xlabel, labelpad=5)
        ax.set_ylim(-0.5, len(domain_rows) - 0.5)
        ax.set_yticks(y)
        ax.tick_params(axis="y", length=0)
        clean_axis(ax)
        ax.spines["left"].set_visible(False)
        for yi in y:
            ax.axhline(yi, color=GRID, linewidth=0.45, zorder=0)
    domain_labels = {
        "archaea": "Archaea",
        "bacteria": "Bacteria",
        "cytosolic_eukaryote": "Cytosolic\neukaryotes",
    }
    ax_exact.set_yticklabels([domain_labels[row["group"]] for row in domain_rows])
    ax_residue.tick_params(axis="y", labelleft=False)


def plot_routing(ax_disposition, ax_accuracy, overall: dict, statuses: list[dict]) -> None:
    accepted = next(row for row in statuses if row["group"] == "accepted")
    review = next(row for row in statuses if row["group"] == "investigate")

    # A single, clean composition bar describes how all 2,530 cases were routed.
    ax_disposition.barh(0, overall["automatic_coverage"] * 100, color=NAVY, height=0.42)
    ax_disposition.barh(0, 100 - overall["automatic_coverage"] * 100,
                        left=overall["automatic_coverage"] * 100, color=REVIEW_LIGHT, height=0.42)
    ax_disposition.text(overall["automatic_coverage"] * 50, 0,
                        f"{100*overall['automatic_coverage']:.1f}%",
                        ha="center", va="center", color="white", fontsize=7.2, fontweight="bold")
    ax_disposition.text(overall["automatic_coverage"] * 100 + (100-overall["automatic_coverage"]*100)/2,
                        0, f"{100*(1-overall['automatic_coverage']):.1f}%",
                        ha="center", va="center", color=INK, fontsize=7.0)
    ax_disposition.set_xlim(0, 116)
    ax_disposition.set_xticks([])
    ax_disposition.set_xlabel("")
    ax_disposition.set_yticks([])
    ax_disposition.set_ylim(-0.55, 0.55)
    clean_axis(ax_disposition, grid_axis=None)
    ax_disposition.spines["left"].set_visible(False)
    ax_disposition.spines["bottom"].set_visible(False)
    ax_disposition.tick_params(axis="x", length=0, pad=3)
    ax_disposition.legend(
        handles=[Patch(facecolor=NAVY, label="Accepted"),
                 Patch(facecolor=REVIEW_LIGHT, label="Review")],
        loc="lower left", bbox_to_anchor=(0.0, 1.03), ncol=2,
        frameon=False, fontsize=6.5, handlelength=0.9,
        handletextpad=0.35, columnspacing=0.9, borderaxespad=0,
    )

    values = [100 * accepted["exact_match_rate"], 100 * review["exact_match_rate"]]
    counts = [accepted["exact_n"], review["exact_n"]]
    labels = ["Accepted", "Review"]
    colors = [NAVY, REVIEW]
    y = np.arange(2)[::-1]
    for yi, value, count, label, color in zip(y, values, counts, labels, colors):
        ax_accuracy.hlines(yi, 0, value, color=color, linewidth=3.2, alpha=0.28, zorder=2)
        ax_accuracy.scatter(value, yi, s=40, color=color, edgecolor="white", linewidth=0.8, zorder=4)
        ax_accuracy.text(value + 4.5, yi, f"{value:.1f}%", ha="left", va="center",
                         fontsize=7.5, fontweight="bold")
    ax_accuracy.set_xlim(0, 116)
    ax_accuracy.set_xticks([0, 50, 100])
    ax_accuracy.set_xlabel("Exact match (%)", labelpad=5)
    ax_accuracy.set_ylim(-0.55, 1.55)
    ax_accuracy.set_yticks(y)
    ax_accuracy.set_yticklabels(labels)
    ax_accuracy.tick_params(axis="y", length=0, pad=5)
    clean_axis(ax_accuracy)
    ax_accuracy.spines["left"].set_visible(False)
    for yi in y:
        ax_accuracy.axhline(yi, color=GRID, linewidth=0.45, zorder=0)


def write_provenance(rows: list[dict], overall: dict, metadata: dict, methods: list[dict]) -> None:
    tracked_inputs = {
        "run:manifest.json": RUN_DIR / "manifest.json",
        "run:summary.json": RUN_DIR / "summary.json",
        "run:case_results.tsv": RUN_DIR / "case_results.tsv",
        "run:inputs/cohort_membership.tsv": INPUT_MEMBERSHIP,
        "research/paper/results/ablation_ladder_2530.tsv": ABLATION_TABLE,
        "research/paper/results/ablation_ladder_2530_provenance.json": ABLATION_PROVENANCE,
    }
    input_hashes = {label: hashlib.sha256(path.read_bytes()).hexdigest()
                    for label, path in tracked_inputs.items()}
    provenance = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "cohort_size": overall["n"],
        "scored_cases": overall["n_scored"],
        "unscored_cases": metadata["unscored_cases"],
        "evaluation_scope": "fixed 2,530-case three-domain cohort; current answer table",
        "operation": "reaggregate stored per-case results and sealed same-cohort controls; no model call",
        "run_id": RUN_ID,
        "comparison_runs": [row["run_id"] for row in methods],
        "input_sha256": input_hashes,
        "llm_provider": metadata["manifest"].get("provider"),
        "llm_model": metadata["manifest"].get("model"),
        "run_storage": metadata["run_storage"],
        "residue_denominator": "pooled reference residues in each named group",
        "exact_denominator": "all scored sequences in each named group",
        "routing_denominator": "all 2,530 processed sequences",
        "panel_a_direct_llm": {
            "run_id": RAW_LLM_ARM["run_id"],
            "model": "deepseekv4-flash-guangdong",
            "reporting_basis": "unusable answers scored zero, not dropped",
            "usable_answers": RAW_LLM_ARM["usable"],
            "source_status": "sealed run stored in MySQL and MinIO; every case completed",
            "failure_counts": {"length_mismatch": 2506},
        },
        "artwork": {
            "width_mm": 180,
            "height_mm": 108,
            "font_pt": "7–10",
            "format": "editable vector PDF and SVG; 300 dpi PNG preview",
            "panels": "A overall method comparison; B domain stratification; C disposition and within-group exact match",
        },
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    configure(args.run_id, args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    rows = load_cohort()
    overall = summarize(rows, "all")
    domains = [summarize([row for row in rows if row["source"] == domain], domain)
               for domain in DOMAINS]
    statuses = [summarize([row for row in rows if row["disposition"] == status], status)
                for status in ("accepted", "investigate")]
    metadata = load_model_metadata(rows, overall)
    methods = load_baseline_metrics(overall)

    write_tsv("cohort_evaluation.tsv", rows)
    write_tsv("panel_a_method_comparison.tsv", [
        {key: value for key, value in method.items()
         if key not in ("color", "usable")} for method in methods])
    write_tsv("panel_b_routing_metrics.tsv", [overall, *statuses])
    write_tsv("panel_c_domain_metrics.tsv", domains)
    (OUT / "model_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    write_provenance(rows, overall, metadata, methods)

    # Remove obsolete source tables produced by the former four-panel figure.
    for name in (
        "panel_a_accuracy_metrics.tsv", "panel_b_coverage_metrics.tsv",
        "panel_c_position_accuracy.tsv", "panel_c_region_errors.tsv",
        "panel_d_error_distribution.tsv", "routing_accuracy_metrics.tsv",
        "error_count_bins.tsv",
        # Retired when panels b and c swapped places.
        "panel_b_domain_metrics.tsv", "panel_c_routing_metrics.tsv",
    ):
        (OUT / name).unlink(missing_ok=True)

    style()
    fig = plt.figure(figsize=(180 / 25.4, 108 / 25.4))
    # Panel A is the hero panel. The lower blocks follow the order the text
    # cites them in: routing first (b), then the domain stratification (c).
    ax_a = fig.add_axes([0.185, 0.605, 0.735, 0.275])
    routing_disp = fig.add_axes([0.185, 0.305, 0.290, 0.085])
    routing_acc = fig.add_axes([0.185, 0.145, 0.290, 0.125])
    domain_exact = fig.add_axes([0.575, 0.145, 0.160, 0.245])
    domain_residue = fig.add_axes([0.785, 0.145, 0.135, 0.245], sharey=domain_exact)

    plot_method_comparison(ax_a, methods)
    plot_routing(routing_disp, routing_acc, overall, statuses)
    plot_domain_stratification(domain_exact, domain_residue, domains)

    panel_label(fig, 0.075, 0.925, "a")
    panel_label(fig, 0.075, 0.435, "b")
    panel_label(fig, 0.487, 0.435, "c")

    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / "fig02_domain_benchmark.png", dpi=300, facecolor="white")
    fig.savefig(FIGURES / "fig02_domain_benchmark.pdf", facecolor="white")
    svg_path = FIGURES / "fig02_domain_benchmark.svg"
    fig.savefig(svg_path, facecolor="white")
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n")
    plt.close(fig)

    print(json.dumps({"overall": overall, "domains": domains, "statuses": statuses,
                      "methods": methods}, ensure_ascii=False, indent=2, default=float))
    print(f"Figure: {FIGURES / 'fig02_domain_benchmark.pdf'}")
    print(f"Source data: {OUT}")


if __name__ == "__main__":
    main()
