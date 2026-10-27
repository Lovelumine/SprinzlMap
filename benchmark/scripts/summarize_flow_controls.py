#!/usr/bin/env python3
"""Turn two stored runs into the paired tables behind the ablation figure.

The main agent run and one deterministic control are compared case by case.
Both runs must cover the same cohort, which the script verifies before it writes
anything, so a reported delta can never come from two different question sets.
The answers themselves come from the current answer table: each rebuilt trace
carries the ``gold_labels`` it was graded against, so re-curating a sequence
moves both sides of the comparison together.

Everything is read from MySQL (index) and MinIO (traces); the repository keeps
only the three generated tables.

    python benchmark/scripts/summarize_flow_controls.py \
        --main-run 20261020-164533-deepseekv4-flash-guangdong \
        --baseline-run 20261023-041911-control-harness-cm-root \
        --baseline-label "检索+DP"
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    BenchmarkRunStore,
    load_case_jsons,
)
from backend.domain.benchmarking.case_index import pred_residue_map  # noqa: E402

OUT_DIR = PROJECT_ROOT / "research/paper/results"


def region_for(label: str) -> str:
    """Six-region split used by the paper's Methods and by Figure 2."""
    if label.startswith("V"):
        return "Variable"
    if label.startswith("-1"):
        return "Acceptor 5'"
    number = ""
    for char in label:
        if char.isdigit() or char == "-":
            number += char
        else:
            break
    try:
        value = int(number)
    except ValueError:
        return "Other"
    if value <= 9:
        return "Acceptor 5'"
    if 10 <= value <= 26 or label in ("17a", "20a", "20b"):
        return "D"
    if 27 <= value <= 43:
        return "Anticodon"
    if 44 <= value <= 48:
        return "Variable"
    if 49 <= value <= 65:
        return "T"
    if 66 <= value <= 76:
        return "Acceptor 3'"
    return "Other"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-run", required=True, help="Run with the language-model planner")
    parser.add_argument("--baseline-run", required=True, help="Control run compared against it")
    parser.add_argument("--baseline-label", default="检索+DP")
    parser.add_argument("--main-label", default="完整流程")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--stem", default="agent_ablation_2530")
    return parser.parse_args()


def assert_same_cohort(runs: List[str]) -> Dict[str, str]:
    """Prove both runs answer the same questions, then report the roster hashes.

    A control is a rerun of the main cohort, so its selected-sequence hash and
    target count must match.  The answer key is not part of this check: both
    sides are graded against whichever answers are active now.
    """
    seen: Dict[str, str] = {}
    for run_id in runs:
        row = BenchmarkRunStore.readonly().run_row(run_id)
        if row is None:
            raise BenchmarkStoreError(f"{run_id}: no such run in MySQL")
        seen[run_id] = str(row.get("selected_seqnames_sha256") or "")
        if int(row.get("n_targets") or 0) != int(row.get("n_completed") or 0):
            raise ValueError(f"{run_id}: the run is incomplete")
    if len(set(seen.values())) != 1 or not all(seen.values()):
        raise ValueError(
            "runs address different cohorts: "
            + ", ".join(f"{run_id}={sha[:16]}…" for run_id, sha in seen.items())
        )
    return seen


def load_run(run_id: str) -> Dict[str, Dict[str, Any]]:
    store = BenchmarkRunStore(run_id)
    rows = {str(row["seqname"]): row for row in store.list_rows()}
    cases = load_case_jsons(run_id, rows, workers=16)
    missing = sorted(set(rows) - set(cases))
    if missing:
        raise BenchmarkStoreError(f"{run_id}: {len(missing)} traces missing from MinIO, e.g. {missing[:5]}")
    for seqname, row in rows.items():
        cases[seqname]["_index_row"] = row
    return cases


def position_counts(cases: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    counts: Dict[str, Dict[str, int]] = {}
    for seqname, case in cases.items():
        gold_labels = case.get("gold_labels") or []
        if not case.get("gold_evaluation_allowed", True):
            continue
        pred_labels, _pred_bases = pred_residue_map(case.get("csv_content"))
        for index, gold_label in enumerate(gold_labels):
            entry = counts.setdefault(gold_label, {"total": 0, "correct": 0})
            entry["total"] += 1
            if index < len(pred_labels) and str(pred_labels[index]).strip() == gold_label:
                entry["correct"] += 1
    return counts


def pooled(cases: List[Dict[str, Any]], correct_key: str, total_key: str) -> Optional[float]:
    correct = 0
    total = 0
    for case in cases:
        metrics = case.get("metrics") or {}
        correct += int(metrics.get(correct_key) or 0)
        total += int(metrics.get(total_key) or 0)
    return (correct / total) if total else None


def summary_row(label: str, cases: List[Dict[str, Any]], positions: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    scored = [case for case in cases if case.get("gold_evaluation_allowed", True)]
    def mean(flag: str) -> float:
        values = [1 if bool((case.get("metrics") or {}).get(flag)) else 0 for case in scored]
        return sum(values) / len(values) if values else 0.0

    def region_accuracy(region: str) -> Optional[float]:
        correct = sum(entry["correct"] for name, entry in positions.items() if region_for(name) == region)
        total = sum(entry["total"] for name, entry in positions.items() if region_for(name) == region)
        return (correct / total) if total else None

    def residue_accuracy() -> float:
        correct = sum(int((case.get("metrics") or {}).get("label_correct") or 0) for case in scored)
        total = sum(int((case.get("metrics") or {}).get("label_total") or 0) for case in scored)
        return correct / total if total else 0.0

    return {
        "method": label,
        "n_total": len(cases),
        "n_ok": sum(1 for case in cases if str(case.get("status") or "") == "ok"),
        "n_error": sum(1 for case in cases if str(case.get("status") or "") != "ok"),
        "paired_ok": len(cases),
        "residue_label_accuracy": residue_accuracy(),
        "exact_match_rate": mean("exact_match"),
        "d_accuracy": region_accuracy("D"),
        "variable_accuracy": region_accuracy("Variable"),
        "anticodon_accuracy": pooled(scored, "anticodon_correct", "anticodon_total"),
        "n73_accuracy": pooled(scored, "discriminator_correct", "discriminator_total"),
        "cca_tail_accuracy": pooled(scored, "terminal_coordinate_correct", "terminal_coordinate_total"),
        "legality_pass_rate": mean("legality_passed"),
        "base_preservation_rate": (
            sum(1 for case in cases if case.get("base_preserved")) / len(cases) if cases else 0.0
        ),
    }


def write_table(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (f"{value:.10f}" if isinstance(value, float) else value)
                for key, value in row.items()
            })


def main() -> int:
    args = parse_args()
    try:
        main_cases = load_run(args.main_run)
        base_cases = load_run(args.baseline_run)
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        return 2

    if set(main_cases) != set(base_cases):
        only_main = sorted(set(main_cases) - set(base_cases))[:5]
        only_base = sorted(set(base_cases) - set(main_cases))[:5]
        raise ValueError(
            f"cohorts differ ({len(main_cases)} vs {len(base_cases)}); "
            f"main-only {only_main} baseline-only {only_base}"
        )

    cohort_hashes = assert_same_cohort([args.main_run, args.baseline_run])
    ungraded = sorted(
        name for name, case in main_cases.items()
        if case.get("gold_labels") is None and case.get("gold_evaluation_allowed", True)
    )[:5]
    if ungraded:
        raise ValueError(f"cases carry no active answer, e.g. {ungraded}")

    main_positions = position_counts(main_cases)
    base_positions = position_counts(base_cases)

    summary_rows = [
        summary_row(args.baseline_label, list(base_cases.values()), base_positions),
        summary_row(args.main_label, list(main_cases.values()), main_positions),
    ]

    position_rows = []
    for position in sorted(
        set(main_positions) | set(base_positions),
        key=lambda label: (0, int(label)) if label.lstrip("-").isdigit() else (1, label),
    ):
        full = main_positions.get(position, {"total": 0, "correct": 0})
        base = base_positions.get(position, {"total": 0, "correct": 0})
        total = max(full["total"], base["total"])
        if not total:
            continue
        full_accuracy = full["correct"] / total
        base_accuracy = base["correct"] / total
        position_rows.append({
            "position": position,
            "region": region_for(position),
            "total": total,
            "full_correct": full["correct"],
            "no_agent_correct": base["correct"],
            "full_accuracy": full_accuracy,
            "no_agent_accuracy": base_accuracy,
            "delta_accuracy": full_accuracy - base_accuracy,
        })

    case_rows = []
    for seqname in sorted(main_cases):
        full_case = main_cases[seqname]
        base_case = base_cases[seqname]
        if not full_case.get("gold_evaluation_allowed", True):
            continue
        total = int((full_case.get("metrics") or {}).get("label_total") or 0)
        if not total:
            continue
        full_accuracy = int((full_case.get("metrics") or {}).get("label_correct") or 0) / total
        base_accuracy = int((base_case.get("metrics") or {}).get("label_correct") or 0) / total
        delta = full_accuracy - base_accuracy
        case_rows.append({
            "seqname": seqname,
            "full_status": full_case.get("status"),
            "no_agent_status": base_case.get("status"),
            "label_total": total,
            "full_label_accuracy": full_accuracy,
            "no_agent_label_accuracy": base_accuracy,
            "delta_label_accuracy": delta,
            "full_exact": 1 if (full_case.get("metrics") or {}).get("exact_match") else 0,
            "no_agent_exact": 1 if (base_case.get("metrics") or {}).get("exact_match") else 0,
            "change": "improved" if delta > 1e-9 else ("worsened" if delta < -1e-9 else "unchanged"),
        })

    out = args.out_dir
    write_table(out / f"{args.stem}_summary.tsv", summary_rows)
    write_table(out / f"{args.stem}_position_delta.tsv", position_rows)
    write_table(out / f"{args.stem}_case_changes.tsv", case_rows)

    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_run": args.main_run,
        "baseline_run": args.baseline_run,
        "baseline_label": args.baseline_label,
        "cohort_sha256": cohort_hashes,
        "answer_source": "current trna_gold answer table, attached to each stored trace",
        "cases": len(main_cases),
        "scored_cases": len(case_rows),
        "positions": len(position_rows),
        "summary": summary_rows,
        "change_counts": {
            state: sum(1 for row in case_rows if row["change"] == state)
            for state in ("improved", "unchanged", "worsened")
        },
        "note": (
            "region accuracies use the six-region boundaries from Methods and the "
            "same index-matched scoring as Figure 2"
        ),
    }
    (out / f"{args.stem}_provenance.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
