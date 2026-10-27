#!/usr/bin/env python3
"""Compare fixed planning and SprinzlMap on one identical case set."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.alignment.number_to_region import number_to_region  # noqa: E402


VALID_BASES = frozenset({"A", "C", "G", "U", "N"})
MetricPredicate = Callable[[str], bool]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--fixed-run", type=Path, required=True)
    parser.add_argument("--sprinzlmap-run", type=Path, required=True)
    parser.add_argument("--seqnames-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize_base(value: Any) -> str:
    base = str(value or "").strip().upper().replace("T", "U")
    return base if base in VALID_BASES else "-"


def safe_region(label: str) -> str:
    try:
        return number_to_region(str(label or "").strip())
    except ValueError:
        return "Unknown"


def load_seqnames(path: Path) -> list[str]:
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    names = [name for name in names if name]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate seqnames in {path}")
    if not names:
        raise ValueError(f"Empty seqname file: {path}")
    return names


def load_gold(path: Path, selected: set[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        labels = header[1:]
        for row in reader:
            if not row or row[0] not in selected:
                continue
            result[row[0]] = [
                label
                for label, base in zip(labels, row[1:])
                if normalize_base(base) != "-"
            ]
    missing = selected - set(result)
    if missing:
        raise ValueError(f"Gold is missing {len(missing)} selected cases")
    return result


def load_run(path: Path, selected: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case_path in sorted((path / "cases").glob("*.json")):
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        seqname = str(payload.get("seqname") or "")
        if seqname in selected:
            result[seqname] = payload
    missing = selected - set(result)
    if missing:
        raise ValueError(f"{path} is missing {len(missing)} selected cases")
    errors = [
        seqname
        for seqname, payload in result.items()
        if str(payload.get("status") or "") != "ok"
    ]
    if errors:
        raise ValueError(f"{path} contains {len(errors)} non-ok selected cases")
    return result


def predicted_labels(payload: dict[str, Any]) -> list[str]:
    rows = list(csv.reader(io.StringIO(str(payload.get("csv_content") or ""))))
    if len(rows) < 3:
        return []
    return [
        str(label).strip()
        for label, base in zip(rows[0], rows[2])
        if normalize_base(base) != "-"
    ]


def score_residues(
    seqnames: Iterable[str],
    gold: dict[str, list[str]],
    predictions: dict[str, list[str]],
    predicate: MetricPredicate,
) -> tuple[int, int]:
    correct = 0
    total = 0
    for seqname in seqnames:
        gold_labels = gold[seqname]
        predicted = predictions[seqname]
        for index, gold_label in enumerate(gold_labels):
            if not predicate(gold_label):
                continue
            total += 1
            correct += int(index < len(predicted) and predicted[index] == gold_label)
    return correct, total


def score_exact(
    seqnames: Iterable[str],
    gold: dict[str, list[str]],
    predictions: dict[str, list[str]],
) -> tuple[int, int]:
    names = list(seqnames)
    return sum(predictions[name] == gold[name] for name in names), len(names)


def score_validity(
    seqnames: Iterable[str],
    payloads: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    names = list(seqnames)
    correct = sum(
        bool(dict(payloads[name].get("metrics") or {}).get("legality_passed"))
        for name in names
    )
    return correct, len(names)


def main() -> None:
    args = parse_args()
    seqnames = load_seqnames(args.seqnames_file)
    selected = set(seqnames)
    gold = load_gold(args.gold, selected)
    fixed = load_run(args.fixed_run, selected)
    sprinzlmap = load_run(args.sprinzlmap_run, selected)
    payloads = {"fixed": fixed, "sprinzlmap": sprinzlmap}
    predictions = {
        run_name: {
            seqname: predicted_labels(run[seqname]) for seqname in seqnames
        }
        for run_name, run in payloads.items()
    }

    metrics: list[tuple[str, str, MetricPredicate | None]] = [
        ("per_base", "逐残基编号准确率", lambda _label: True),
        ("full_sequence", "整条序列完全一致率", None),
        (
            "d_region",
            "D区编号准确率",
            lambda label: safe_region(label) == "D loop + D stem",
        ),
        (
            "variable_region",
            "可变区编号准确率",
            lambda label: safe_region(label) == "Variable loop",
        ),
        ("anticodon_34_36", "反密码子区（34–36）", lambda label: label in {"34", "35", "36"}),
        ("position_73", "第73位判别碱基", lambda label: label == "73"),
        ("terminal_74_76", "末端坐标（74–76）", lambda label: label in {"74", "75", "76"}),
        ("validity", "编号合法性通过率", None),
    ]

    output_rows: list[dict[str, Any]] = []
    for metric_id, label, predicate in metrics:
        scores: dict[str, tuple[int, int]] = {}
        for run_name in ("fixed", "sprinzlmap"):
            if metric_id == "full_sequence":
                scores[run_name] = score_exact(
                    seqnames,
                    gold,
                    predictions[run_name],
                )
            elif metric_id == "validity":
                scores[run_name] = score_validity(seqnames, payloads[run_name])
            else:
                assert predicate is not None
                scores[run_name] = score_residues(
                    seqnames,
                    gold,
                    predictions[run_name],
                    predicate,
                )
        fixed_correct, fixed_total = scores["fixed"]
        agent_correct, agent_total = scores["sprinzlmap"]
        if fixed_total != agent_total:
            raise ValueError(f"Metric denominator mismatch: {metric_id}")
        fixed_rate = fixed_correct / fixed_total if fixed_total else None
        agent_rate = agent_correct / agent_total if agent_total else None
        output_rows.append(
            {
                "metric_id": metric_id,
                "metric": label,
                "cases": len(seqnames),
                "denominator": fixed_total,
                "fixed_correct": fixed_correct,
                "fixed_rate": fixed_rate,
                "sprinzlmap_correct": agent_correct,
                "sprinzlmap_rate": agent_rate,
                "sprinzlmap_minus_fixed_pp": (
                    100.0 * (agent_rate - fixed_rate)
                    if fixed_rate is not None and agent_rate is not None
                    else None
                ),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(output_rows[0]),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"cases={len(seqnames)} output={args.output}")


if __name__ == "__main__":
    main()
