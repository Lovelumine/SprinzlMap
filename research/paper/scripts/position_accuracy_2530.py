#!/usr/bin/env python3
"""Position-wise Sprinzl accuracy for the frozen 2,530-case cohort.

The answers come from the current answer table: a rebuilt run directory attaches
the active ``trna_gold`` labels to each frozen prediction, so re-curating an
answer moves this figure without replaying the model.  Two details matter and
both caused wrong totals before:

1. A gold cell can hold ``-``, an empty string, or a base. Only real bases count.
   Treating ``''`` as a residue inflated the 2,530-case total from 193,238 to
   353,254.
2. The runner compares position by position after dropping gaps from both sides
   (``residue_label_sequence``), so labels must be matched by index. Looking the
   label up by name instead scores 191,649 residues.

With both applied, the per-case count reproduces ``metrics.label_correct`` for
all 2,530 cases, so these per-position numbers share the paper's denominator.

    python3 research/paper/scripts/position_accuracy_2530.py
    python3 research/paper/scripts/position_accuracy_2530.py --out /tmp/check
"""
from __future__ import annotations

import argparse
import csv
import json
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import resolve_run_dir  # noqa: E402

DEFAULT_RUN_ID = "20261020-164533-deepseekv4-flash-guangdong"
RUN_ID = DEFAULT_RUN_ID
# Stored in MySQL (index) and MinIO (traces); materialized into .runtime/runs.
RUN_DIR = Path()
# The cohort travels with the run, so the figure cannot drift away from it.
MEMBERSHIP = Path()
OUT = ROOT / "research/paper/results/figure2/position_accuracy_2530.tsv"
OUT_META = ROOT / "research/paper/results/figure2/position_accuracy_2530.json"

GAP = ("-", "")


def configure(run_id: str = DEFAULT_RUN_ID, out_dir: Path | None = None) -> None:
    """Choose the stored run and, if asked, a scratch directory for the output."""
    global RUN_ID, RUN_DIR, MEMBERSHIP, OUT, OUT_META
    RUN_ID = str(run_id)
    RUN_DIR = resolve_run_dir(RUN_ID)
    MEMBERSHIP = RUN_DIR / "inputs" / "cohort_membership.tsv"
    if out_dir:
        OUT = Path(out_dir) / "position_accuracy_2530.tsv"
        OUT_META = Path(out_dir) / "position_accuracy_2530.json"


def region_for(label: str) -> str:
    if label.startswith("V"):
        return "Variable"
    if label.startswith("-1"):
        return "Acceptor 5'"
    num = ""
    for ch in label:
        if ch.isdigit() or ch == "-":
            num += ch
        else:
            break
    try:
        n = int(num)
    except ValueError:
        return "Other"
    if n <= 9:
        return "Acceptor 5'"
    if 10 <= n <= 26 or label in ("17a", "20a", "20b"):
        return "D"
    if 27 <= n <= 43:
        return "Anticodon"
    if 44 <= n <= 48:
        return "Variable"
    if 49 <= n <= 65:
        return "T"
    if 66 <= n <= 76:
        return "Acceptor 3'"
    return "Other"


def load_frozen_gold_sha() -> tuple[str, str]:
    """The reference file the run started with, kept for provenance only.

    The answers this figure scores against come from the current answer table,
    so the run's own snapshot is reported to show how far the two have moved.
    """
    manifest = json.loads((RUN_DIR / "manifest.json").read_text())
    recorded = manifest["input_files"]["gold"]
    gold_path = RUN_DIR / "inputs" / Path(str(recorded["path"])).name
    if hashlib.sha256(gold_path.read_bytes()).hexdigest() != recorded["sha256"]:
        raise SystemExit(f"gold snapshot digest mismatch: {gold_path}")
    return str(recorded["sha256"]), str(recorded["path"])


def load_active_answers(cases: dict[str, dict]) -> dict[str, tuple[list[str], list[str]]]:
    """Per-sequence answers as the materialized traces carry them.

    A rebuilt run directory attaches the current answer table to every frozen
    prediction, so this figure and the review page grade the same residues.

    The lists are taken exactly as stored: the answer table keeps one entry per
    residue, so pairing by index is what ``score_labels`` does and any extra
    filtering would move the totals.
    """
    answers: dict[str, tuple[list[str], list[str]]] = {}
    for seqname, case in cases.items():
        labels = case.get("gold_labels")
        bases = case.get("gold_bases")
        if labels is None or bases is None:
            raise SystemExit(
                f"{seqname}: the trace carries no active answer; rebuild the run "
                "directory with materialize_run(live_answer=True)"
            )
        answers[seqname] = ([str(v) for v in labels], [str(v) for v in bases])
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID,
                        help="Stored benchmark run to grade (materialized from MySQL and MinIO)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write into this directory instead of research/paper/results/figure2")
    args = parser.parse_args()
    configure(args.run_id, args.out)
    cohort = [r["seqname"] for r in
              csv.DictReader(MEMBERSHIP.open(encoding="utf-8"), delimiter="\t")]
    assert len(cohort) == 2530
    cohort_set = set(cohort)
    frozen_gold_sha, frozen_gold_path = load_frozen_gold_sha()

    cases = {}
    for path in (RUN_DIR / "cases").glob("*.json"):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case["seqname"] in cohort_set:
            cases[case["seqname"]] = case
    assert len(cases) == 2530, len(cases)
    answers = load_active_answers(cases)
    # One cohort member is curated as non-canonical and carries no gold answer;
    # the run itself records it as unscored, so it stays out of every count here.
    unscored = sorted(name for name, case in cases.items()
                      if not case.get("gold_evaluation_allowed", True))

    correct: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    reproduced = 0
    mismatches = []
    res_total = 0
    res_correct = 0

    for seqname in cohort:
        case = cases[seqname]
        if seqname in unscored:
            continue
        gold_lab, gold_bases = answers[seqname]

        lines = [ln for ln in (case.get("csv_content") or "").splitlines()
                 if ln.strip()]
        if len(lines) < 3:
            continue
        numbering, _, target = lines[0].split(","), lines[1], lines[2].split(",")
        pred_lab = [a for a, b in zip(numbering, target) if b != "-"]
        pred_bases = [b for b in target if b != "-"]

        case_correct = 0
        for idx, gold_label in enumerate(gold_lab):
            pred_label = pred_lab[idx] if idx < len(pred_lab) else None
            pred_base = pred_bases[idx] if idx < len(pred_bases) else None
            total[gold_label] += 1
            res_total += 1
            if pred_label == gold_label and pred_base == gold_bases[idx]:
                correct[gold_label] += 1
                res_correct += 1
                case_correct += 1

        if case_correct == case["metrics"]["label_correct"]:
            reproduced += 1
        elif len(mismatches) < 10:
            mismatches.append([seqname, case_correct,
                               case["metrics"]["label_correct"]])

    matrix_total = sum(c["metrics"]["label_total"] for c in cases.values()
                       if c["seqname"] not in unscored)
    assert res_total == matrix_total, (res_total, matrix_total)

    rows = [{"position": label,
             "region": region_for(label),
             "n_scored": total[label],
             "n_correct": correct.get(label, 0),
             "accuracy": round(correct.get(label, 0) / total[label], 6)}
            for label in total]

    def sort_key(r):
        lab = r["position"]
        if lab.startswith("V"):
            return (2, int(lab[1:].split("i")[0]), lab)
        try:
            return (0, int(lab.split("i")[0]), lab)
        except ValueError:
            return (1, 0, lab)

    rows.sort(key=sort_key)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    meta = {"run_id": RUN_ID, "cohort_size": len(cohort),
            "scored_cases": len(cohort) - len(unscored),
            "unscored_cases": unscored,
            "unscored_reason": "curated as a non-canonical tRNA; the run records "
                               "gold_evaluation_allowed=false for these cases",
            "cases_reproducing_published_label_correct": reproduced,
            "cases_not_reproducing": mismatches,
            "scored_residues": res_total, "correct_residues": res_correct,
            "residue_accuracy": round(res_correct / res_total, 6),
            "positions": len(rows),
            "gold_source": "current answer table (mysql trna_gold), attached to the "
                           "frozen prediction of each stored case",
            "run_frozen_gold_sha256": frozen_gold_sha,
            "run_frozen_gold_path": frozen_gold_path,
            "note": "Answers are read per residue in stored order; the count is "
                    "checked against label_total in the same run index, so this "
                    "figure cannot disagree with the reported residue accuracy."}
    OUT_META.write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
