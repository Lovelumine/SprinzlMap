#!/usr/bin/env python3
"""Audit 74-76 coordinate accuracy without conflating it with CCA status."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable


VALID_BASES = frozenset({"A", "C", "G", "U", "N"})
CLASSIC_SOURCES = frozenset({"archaea", "bacteria", "cytosolic_eukaryote"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--fixed-run", type=Path, required=True)
    parser.add_argument("--sprinzlmap-run", type=Path, required=True)
    parser.add_argument(
        "--sprinzlmap-overlay",
        type=Path,
        action="append",
        default=[],
        help="Optional targeted reruns applied after the main SprinzlMap run.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize_base(value: Any) -> str:
    base = str(value or "").strip().upper().replace("T", "U")
    return base if base in VALID_BASES else "-"


def load_run(path: Path) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for case_path in sorted((path / "cases").glob("*.json")):
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        payloads[str(payload["seqname"])] = payload
    return payloads


def predicted_labels(payload: dict[str, Any]) -> list[str]:
    rows = list(csv.reader(io.StringIO(str(payload.get("csv_content") or ""))))
    if len(rows) < 3:
        return []
    return [
        str(label).strip()
        for label, base in zip(rows[0], rows[2])
        if normalize_base(base) != "-"
    ]


def safely_handled_terminal_anomaly(payload: dict[str, Any]) -> bool:
    """Treat abstention/investigation as safe handling of an invalid tail."""
    disposition = str(dict(payload.get("agent") or {}).get("disposition") or "")
    if disposition != "accepted":
        return True
    return not set(predicted_labels(payload)).intersection({"74", "75", "76"})


def category_for(gold_by_label: dict[str, str]) -> str:
    tail = [gold_by_label.get(label, "-") for label in ("74", "75", "76")]
    occupied = [base for base in tail if base != "-"]
    if tail == ["C", "C", "A"]:
        return "complete_cca"
    if not occupied:
        return "no_74_76"
    if len(occupied) < 3:
        return "incomplete_terminal" if tail in (
            ["C", "-", "-"],
            ["C", "C", "-"],
        ) else "non_cca_reference"
    return "non_cca_reference"


def score(
    gold_labels: list[str],
    predicted: list[str],
    positions: Iterable[str],
) -> tuple[int, int]:
    selected = set(positions)
    correct = 0
    total = 0
    for index, gold_label in enumerate(gold_labels):
        if gold_label not in selected:
            continue
        total += 1
        correct += int(index < len(predicted) and predicted[index] == gold_label)
    return correct, total


def main() -> None:
    args = parse_args()
    fixed = load_run(args.fixed_run)
    sprinzlmap = load_run(args.sprinzlmap_run)
    for overlay in args.sprinzlmap_overlay:
        sprinzlmap.update(load_run(overlay))

    rows_by_category: dict[
        str, list[tuple[list[str], list[str], list[str], bool, bool]]
    ] = {}
    with args.gold.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        labels = list(reader.fieldnames or [])[1:]
        for raw in reader:
            seqname = str(raw.get("seqname") or "")
            if seqname not in fixed or seqname not in sprinzlmap:
                continue
            metadata = dict(
                dict(sprinzlmap[seqname].get("benchmark_case") or {}).get(
                    "metadata"
                )
                or {}
            )
            amino_acid = str(metadata.get("amino_acid") or "").lower()
            if (
                str(metadata.get("broad_source") or "") not in CLASSIC_SOURCES
                or amino_acid in {"sec", "selenocysteine"}
            ):
                continue
            gold_by_label = {
                label: normalize_base(raw.get(label)) for label in labels
            }
            gold_labels = [
                label for label in labels if gold_by_label[label] != "-"
            ]
            category = category_for(gold_by_label)
            rows_by_category.setdefault(category, []).append(
                (
                    gold_labels,
                    predicted_labels(fixed[seqname]),
                    predicted_labels(sprinzlmap[seqname]),
                    safely_handled_terminal_anomaly(fixed[seqname]),
                    safely_handled_terminal_anomaly(sprinzlmap[seqname]),
                )
            )

    output_rows: list[dict[str, Any]] = []
    labels = {
        "complete_cca": "完整CCA（74-76）",
        "incomplete_terminal": "不完整末端（不计入74-76准确率）",
        "non_cca_reference": "非CCA末端参考记录（不计入74–76准确率）",
        "no_74_76": "参考不含74-76",
    }
    for category in (
        "complete_cca",
        "incomplete_terminal",
        "non_cca_reference",
        "no_74_76",
    ):
        selected = rows_by_category.get(category, [])
        fixed_correct = fixed_total = 0
        sprinzlmap_correct = sprinzlmap_total = 0
        fixed_non_cca_without_tail = 0
        sprinzlmap_non_cca_without_tail = 0
        fixed_non_cca_safe = 0
        sprinzlmap_non_cca_safe = 0
        for (
            gold_labels,
            fixed_labels,
            sprinzlmap_labels,
            fixed_safe,
            sprinzlmap_safe,
        ) in selected:
            if category == "complete_cca":
                correct, total = score(
                    gold_labels, fixed_labels, {"74", "75", "76"}
                )
                fixed_correct += correct
                fixed_total += total
                correct, total = score(
                    gold_labels,
                    sprinzlmap_labels,
                    {"74", "75", "76"},
                )
                sprinzlmap_correct += correct
                sprinzlmap_total += total
            elif category in {"incomplete_terminal", "non_cca_reference"}:
                fixed_non_cca_safe += int(fixed_safe)
                sprinzlmap_non_cca_safe += int(sprinzlmap_safe)
                fixed_non_cca_without_tail += int(
                    not set(fixed_labels).intersection({"74", "75", "76"})
                )
                sprinzlmap_non_cca_without_tail += int(
                    not set(sprinzlmap_labels).intersection({"74", "75", "76"})
                )
        fixed_accuracy = fixed_correct / fixed_total if fixed_total else None
        sprinzlmap_accuracy = (
            sprinzlmap_correct / sprinzlmap_total if sprinzlmap_total else None
        )
        output_rows.append(
            {
                "category": category,
                "terminal_category": labels[category],
                "cases": len(selected),
                "scored_coordinates": fixed_total,
                "fixed_correct": fixed_correct,
                "fixed_accuracy": fixed_accuracy,
                "sprinzlmap_correct": sprinzlmap_correct,
                "sprinzlmap_accuracy": sprinzlmap_accuracy,
                "sprinzlmap_minus_fixed_pp": (
                    100.0 * (sprinzlmap_accuracy - fixed_accuracy)
                    if fixed_accuracy is not None and sprinzlmap_accuracy is not None
                    else None
                ),
                "fixed_non_cca_without_74_76": fixed_non_cca_without_tail
                if category in {"incomplete_terminal", "non_cca_reference"}
                else None,
                "fixed_non_cca_without_74_76_rate": (
                    fixed_non_cca_without_tail / len(selected)
                    if category in {"incomplete_terminal", "non_cca_reference"} and selected
                    else None
                ),
                "sprinzlmap_non_cca_without_74_76": sprinzlmap_non_cca_without_tail
                if category in {"incomplete_terminal", "non_cca_reference"}
                else None,
                "sprinzlmap_non_cca_without_74_76_rate": (
                    sprinzlmap_non_cca_without_tail / len(selected)
                    if category in {"incomplete_terminal", "non_cca_reference"} and selected
                    else None
                ),
                "fixed_non_cca_safely_handled": fixed_non_cca_safe
                if category in {"incomplete_terminal", "non_cca_reference"}
                else None,
                "fixed_non_cca_safely_handled_rate": (
                    fixed_non_cca_safe / len(selected)
                    if category in {"incomplete_terminal", "non_cca_reference"} and selected
                    else None
                ),
                "sprinzlmap_non_cca_safely_handled": sprinzlmap_non_cca_safe
                if category in {"incomplete_terminal", "non_cca_reference"}
                else None,
                "sprinzlmap_non_cca_safely_handled_rate": (
                    sprinzlmap_non_cca_safe / len(selected)
                    if category in {"incomplete_terminal", "non_cca_reference"} and selected
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
    print(f"output={args.output} cases={sum(len(rows) for rows in rows_by_category.values())}")


if __name__ == "__main__":
    main()
