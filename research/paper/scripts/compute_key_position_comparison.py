#!/usr/bin/env python3
"""Compare key Sprinzl positions across three benchmark runs."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from typing import Any


VALID_BASES = frozenset({"A", "C", "G", "U", "N"})
GROUPS = (
    ("anticodon_34_36", "反密码子区（34–36）", {"34", "35", "36"}),
    ("variable_boundary_45_48", "可变区边界（45–48）", {"45", "46", "47", "48"}),
    ("position_45", "第45位", {"45"}),
    ("position_46", "第46位", {"46"}),
    ("position_47", "第47位", {"47"}),
    ("position_48", "第48位", {"48"}),
    ("t_arm_49_65", "T臂（49–65）", {str(value) for value in range(49, 66)}),
    ("t_arm_core_51_53", "T臂核心（51–53）", {"51", "52", "53"}),
    ("discriminator_73", "第73位判别碱基", {"73"}),
    ("cca_tail_74_76", "CCA尾（74–76）", {"74", "75", "76"}),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--cm-run", type=Path, required=True)
    parser.add_argument("--fixed-run", type=Path, required=True)
    parser.add_argument("--sprinzlmap-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize_base(value: Any) -> str:
    base = str(value or "").strip().upper().replace("T", "U")
    return base if base in VALID_BASES else "-"


def load_gold(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        labels = header[1:]
        for row in reader:
            result[row[0]] = [
                label
                for label, base in zip(labels, row[1:])
                if normalize_base(base) != "-"
            ]
    return result


def load_run(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case_path in sorted((path / "cases").glob("*.json")):
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        result[str(payload["seqname"])] = payload
    return result


def accepted(payload: dict[str, Any]) -> bool:
    return (
        payload.get("status") == "ok"
        and str(dict(payload.get("agent") or {}).get("disposition")) == "accepted"
    )


def predicted_labels(payload: dict[str, Any]) -> list[str]:
    rows = list(csv.reader(io.StringIO(str(payload.get("csv_content") or ""))))
    if len(rows) < 3:
        return []
    return [
        str(label).strip()
        for label, base in zip(rows[0], rows[2])
        if normalize_base(base) != "-"
    ]


def main() -> None:
    args = parse_args()
    gold = load_gold(args.gold)
    runs = {
        "cm": load_run(args.cm_run),
        "fixed": load_run(args.fixed_run),
        "sprinzlmap": load_run(args.sprinzlmap_run),
    }
    common = set(gold)
    for payloads in runs.values():
        common &= {name for name, payload in payloads.items() if accepted(payload)}

    predicted = {
        run_name: {
            seqname: predicted_labels(payloads[seqname]) for seqname in common
        }
        for run_name, payloads in runs.items()
    }
    rows: list[dict[str, Any]] = []
    for group_id, label, positions in GROUPS:
        scores: dict[str, tuple[int, int, float]] = {}
        for run_name in runs:
            correct = 0
            total = 0
            for seqname in common:
                gold_labels = gold[seqname]
                pred_labels = predicted[run_name][seqname]
                for index, gold_label in enumerate(gold_labels):
                    if gold_label not in positions:
                        continue
                    total += 1
                    correct += int(
                        index < len(pred_labels) and pred_labels[index] == gold_label
                    )
            scores[run_name] = (correct, total, correct / total if total else 0.0)
        rows.append(
            {
                "group_id": group_id,
                "position_or_region": label,
                "common_accepted_cases": len(common),
                "scored_residues": scores["cm"][1],
                "cm_correct": scores["cm"][0],
                "cm_accuracy": scores["cm"][2],
                "fixed_correct": scores["fixed"][0],
                "fixed_accuracy": scores["fixed"][2],
                "sprinzlmap_correct": scores["sprinzlmap"][0],
                "sprinzlmap_accuracy": scores["sprinzlmap"][2],
                "sprinzlmap_minus_cm_pp": 100
                * (scores["sprinzlmap"][2] - scores["cm"][2]),
                "sprinzlmap_minus_fixed_pp": 100
                * (scores["sprinzlmap"][2] - scores["fixed"][2]),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"common_accepted_cases={len(common)} output={args.output}")


if __name__ == "__main__":
    main()
