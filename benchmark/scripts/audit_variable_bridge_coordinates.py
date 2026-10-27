#!/usr/bin/env python3
"""Audit Sprinzl 46-48 bridge occupancy in references and benchmark runs."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The application package lives under APP/, so both roots have to be on the path.
for relative in ("APP", "."):
    path = str(PROJECT_ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.domain.alignment.variable_boundary import (  # noqa: E402
    assess_variable_bridge,
)


VALID_BASES = frozenset({"A", "C", "G", "U", "N"})


@dataclass(frozen=True)
class NumberedSequence:
    labels: tuple[str, ...]
    bases: tuple[str, ...]

    @property
    def sequence(self) -> str:
        return "".join(self.bases)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv",
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LABEL=RUN_DIR",
        help="Benchmark run label and directory. Repeat for paired runs.",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-tsv", type=Path, required=True)
    return parser.parse_args()


def normalize_base(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("T", "U")
    return value if value in VALID_BASES else "-"


def occupied_numbering(
    labels: Sequence[Any],
    bases: Sequence[Any],
) -> NumberedSequence:
    occupied_labels: list[str] = []
    occupied_bases: list[str] = []
    for raw_label, raw_base in zip(labels, bases):
        base = normalize_base(raw_base)
        if base == "-":
            continue
        occupied_labels.append(str(raw_label or "").strip())
        occupied_bases.append(base)
    return NumberedSequence(tuple(occupied_labels), tuple(occupied_bases))


def load_gold(path: Path) -> dict[str, NumberedSequence]:
    rows: dict[str, NumberedSequence] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected gold header: {path}")
        labels = header[1:]
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue
            seqname = str(row[0] or "").strip()
            if not seqname:
                continue
            if seqname in rows:
                raise ValueError(f"Duplicate gold seqname at {path}:{line_number}")
            rows[seqname] = occupied_numbering(labels, row[1:])
    if not rows:
        raise ValueError(f"Gold table is empty: {path}")
    return rows


def parse_csv_alignment(raw: Any) -> NumberedSequence:
    try:
        rows = list(csv.reader(io.StringIO(str(raw or ""))))
    except csv.Error:
        return NumberedSequence((), ())
    if len(rows) < 3:
        return NumberedSequence((), ())
    return occupied_numbering(rows[0], rows[2])


def load_run(path: Path) -> dict[str, dict[str, Any]]:
    cases_dir = path / "cases"
    rows: dict[str, dict[str, Any]] = {}
    for case_path in sorted(cases_dir.glob("*.json")):
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        seqname = str(payload.get("seqname") or "").strip()
        if not seqname or seqname in rows:
            raise ValueError(f"Missing or duplicate seqname in {case_path}")
        rows[seqname] = payload
    if not rows:
        raise ValueError(f"No case JSON files found under {cases_dir}")
    return rows


def parse_run_specs(values: Sequence[str]) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    labels: set[str] = set()
    for value in values:
        label, separator, raw_path = str(value).partition("=")
        label = label.strip()
        path = Path(raw_path.strip())
        if not separator or not label or not raw_path.strip():
            raise ValueError(f"Invalid --run value: {value!r}; expected LABEL=RUN_DIR")
        if label in labels:
            raise ValueError(f"Duplicate run label: {label}")
        labels.add(label)
        parsed.append((label, path))
    return parsed


def disposition(payload: Mapping[str, Any]) -> str:
    return str(dict(payload.get("agent") or {}).get("disposition") or "unknown")


def benchmark_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return dict(dict(payload.get("benchmark_case") or {}).get("metadata") or {})


def reference_metadata(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for payload in payloads:
        case = dict(payload.get("benchmark_case") or {})
        if case:
            metadata = benchmark_metadata(payload)
            return {
                "broad_source": str(metadata.get("broad_source") or ""),
                "amino_acid": str(metadata.get("amino_acid") or ""),
                "organism": str(metadata.get("organism") or ""),
                "reference_evidence_tier": str(
                    case.get("reference_evidence_tier") or ""
                ),
                "project_expert_audit_available": bool(
                    case.get("project_expert_audit_available")
                ),
                "curation_state": str(case.get("curation_state") or ""),
            }
    return {
        "broad_source": "",
        "amino_acid": "",
        "organism": "",
        "reference_evidence_tier": "",
        "project_expert_audit_available": False,
        "curation_state": "",
    }


def bridge_evidence(numbered: NumberedSequence) -> dict[str, Any]:
    return assess_variable_bridge(
        numbered.labels,
        numbered.bases,
        require_t_stem_anchor=True,
    )


def compact_labels(values: Sequence[Any]) -> str:
    return ",".join(str(value) for value in values)


def label_window(labels: Sequence[str], index: int, radius: int = 3) -> str:
    start = max(0, index - radius)
    end = min(len(labels), index + radius + 1)
    return compact_labels(labels[start:end])


def counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def write_tsv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("Refusing to write an empty audit TSV")
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def audit(
    gold: Mapping[str, NumberedSequence],
    runs: Sequence[tuple[str, Mapping[str, Mapping[str, Any]]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not runs:
        raise ValueError("At least one run is required")
    run_labels = [label for label, _ in runs]
    common_names = set(gold)
    for _, payloads in runs:
        common_names.intersection_update(payloads)
    if not common_names:
        raise ValueError("Gold and run case sets do not overlap")

    reference_status = Counter()
    reference_conflict_patterns = Counter()
    for numbered in gold.values():
        evidence = bridge_evidence(numbered)
        status = str(evidence.get("status") or "unknown")
        reference_status[status] += 1
        if status == "conflict":
            reference_conflict_patterns[
                f"{compact_labels(evidence.get('observed_labels') or [])}"
                f" -> {compact_labels(evidence.get('expected_labels') or [])}"
            ] += 1

    run_summaries: dict[str, Any] = {}
    for run_label, payloads in runs:
        accepted = 0
        position_47_scored = 0
        position_47_correct = 0
        predicted_at_gold_47 = Counter()
        for seqname in sorted(common_names):
            payload = payloads[seqname]
            if disposition(payload) != "accepted":
                continue
            accepted += 1
            gold_numbered = gold[seqname]
            try:
                gold_47_index = gold_numbered.labels.index("47")
            except ValueError:
                continue
            predicted = parse_csv_alignment(payload.get("csv_content"))
            position_47_scored += 1
            predicted_label = (
                predicted.labels[gold_47_index]
                if gold_47_index < len(predicted.labels)
                else "(missing)"
            )
            predicted_at_gold_47[predicted_label] += 1
            position_47_correct += predicted_label == "47"
        run_summaries[run_label] = {
            "accepted_cases": accepted,
            "gold_position_47_scored_cases": position_47_scored,
            "gold_position_47_correct_cases": position_47_correct,
            "gold_position_47_accuracy": (
                position_47_correct / position_47_scored
                if position_47_scored
                else None
            ),
            "predicted_label_at_gold_position_47": counter_dict(
                predicted_at_gold_47
            ),
        }

    audit_rows: list[dict[str, Any]] = []
    common_accepted_gold_47 = 0
    outcome_signatures = Counter()
    all_wrong_reference_status = Counter()
    all_wrong_reference_patterns = Counter()
    all_wrong_sources = Counter()
    all_wrong_amino_acids = Counter()
    all_wrong_evidence_tiers = Counter()

    for seqname in sorted(common_names):
        gold_numbered = gold[seqname]
        try:
            gold_47_index = gold_numbered.labels.index("47")
        except ValueError:
            continue
        payloads = [run_payloads[seqname] for _, run_payloads in runs]
        metadata = reference_metadata(payloads)
        gold_evidence = bridge_evidence(gold_numbered)
        row: dict[str, Any] = {
            "seqname": seqname,
            **metadata,
            "gold_position_47_residue_index": gold_47_index + 1,
            "gold_window": label_window(gold_numbered.labels, gold_47_index),
            "gold_bridge_status": str(gold_evidence.get("status") or ""),
            "gold_bridge_observed": compact_labels(
                gold_evidence.get("observed_labels") or []
            ),
            "gold_bridge_expected": compact_labels(
                gold_evidence.get("expected_labels") or []
            ),
        }
        outcomes: list[str] = []
        all_accepted = True
        all_wrong = True
        for (run_label, _), payload in zip(runs, payloads):
            run_disposition = disposition(payload)
            predicted = parse_csv_alignment(payload.get("csv_content"))
            predicted_label = (
                predicted.labels[gold_47_index]
                if gold_47_index < len(predicted.labels)
                else "(missing)"
            )
            correct = predicted_label == "47"
            pred_evidence = bridge_evidence(predicted)
            row.update(
                {
                    f"{run_label}_disposition": run_disposition,
                    f"{run_label}_predicted_at_gold_47": predicted_label,
                    f"{run_label}_prediction_window": (
                        label_window(predicted.labels, gold_47_index)
                        if predicted.labels
                        else ""
                    ),
                    f"{run_label}_bridge_status": str(
                        pred_evidence.get("status") or ""
                    ),
                    f"{run_label}_bridge_observed": compact_labels(
                        pred_evidence.get("observed_labels") or []
                    ),
                }
            )
            all_accepted = all_accepted and run_disposition == "accepted"
            all_wrong = all_wrong and not correct
            outcomes.append("correct" if correct else "wrong")

        row["all_runs_accepted"] = all_accepted
        row["all_runs_wrong_at_gold_47"] = all_accepted and all_wrong
        row["reference_review_required"] = (
            str(gold_evidence.get("status") or "")
            in {"conflict", "requires_review", "insufficient_evidence"}
        )
        audit_rows.append(row)

        if not all_accepted:
            continue
        common_accepted_gold_47 += 1
        outcome_signatures[" | ".join(outcomes)] += 1
        if not all_wrong:
            continue
        status = str(gold_evidence.get("status") or "unknown")
        pattern = (
            f"{compact_labels(gold_evidence.get('observed_labels') or [])}"
            f" -> {compact_labels(gold_evidence.get('expected_labels') or [])}"
        )
        all_wrong_reference_status[status] += 1
        all_wrong_reference_patterns[pattern] += 1
        all_wrong_sources[str(metadata["broad_source"] or "unknown")] += 1
        all_wrong_amino_acids[str(metadata["amino_acid"] or "unknown")] += 1
        all_wrong_evidence_tiers[
            str(metadata["reference_evidence_tier"] or "unknown")
        ] += 1

    summary = {
        "schema_version": "1.0",
        "audit_scope": {
            "gold_cases": len(gold),
            "common_gold_run_cases": len(common_names),
            "run_labels": run_labels,
            "gold_position_47_cases": sum(
                "47" in numbered.labels for numbered in gold.values()
            ),
            "common_accepted_gold_position_47_cases": common_accepted_gold_47,
        },
        "sprinzl_rule": {
            "one_bridge_residue": ["48"],
            "two_bridge_residues": ["46", "48"],
            "three_bridge_residues": ["46", "47", "48"],
            "automatic_decision_requires_t_stem_49_65_anchor": True,
        },
        "reference_bridge_audit": {
            "status_counts": counter_dict(reference_status),
            "conflict_patterns": counter_dict(reference_conflict_patterns),
        },
        "runs": run_summaries,
        "common_accepted_position_47_outcomes": counter_dict(outcome_signatures),
        "all_runs_wrong_at_gold_position_47": {
            "count": sum(all_wrong_reference_status.values()),
            "reference_bridge_status": counter_dict(all_wrong_reference_status),
            "reference_bridge_patterns": counter_dict(all_wrong_reference_patterns),
            "broad_source": counter_dict(all_wrong_sources),
            "amino_acid": counter_dict(all_wrong_amino_acids),
            "reference_evidence_tier": counter_dict(all_wrong_evidence_tiers),
        },
        "interpretation_contract": {
            "reference_labels_are_not_rewritten": True,
            "system_predictions_are_not_used_as_gold": True,
            "nonconforming_reference_bridges_require_blinded_review": True,
            "database_reference_accuracy_must_be_reported_separately_from_rule_conformity": True,
        },
    }
    return summary, audit_rows


def main() -> None:
    args = parse_args()
    run_specs = parse_run_specs(args.run)
    gold = load_gold(args.gold)
    runs = [(label, load_run(path)) for label, path in run_specs]
    summary, rows = audit(gold, runs)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_tsv(args.output_tsv, rows)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
