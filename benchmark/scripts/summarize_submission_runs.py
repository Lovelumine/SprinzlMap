#!/usr/bin/env python3
"""Summarize submission-v2 runs by scientific scope and template distance."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "APP") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import resolve_run_dir  # noqa: E402

CANONICAL_SOURCES = frozenset(
    {
        "archaea",
        "bacteria",
        "chloroplast",
        "cyanelle",
        "cytosolic_eukaryote",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LABEL=RUN_DIR",
        help="Named benchmark run; repeat for each system.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_run(value: str) -> tuple[str, Path]:
    label, separator, path = str(value or "").partition("=")
    if not separator or not label.strip() or not path.strip():
        raise ValueError(f"Expected LABEL=RUN_DIR, got: {value}")
    return label.strip(), resolve_run_dir(path.strip())


def load_cases(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for path in sorted((run_dir / "cases").glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        seqname = str(row.get("seqname") or "")
        if not seqname or seqname in rows:
            raise ValueError(f"Missing or duplicate seqname in {path}")
        benchmark_case = dict(row.get("benchmark_case") or {})
        if str(benchmark_case.get("schema_version") or "") != "2.0":
            raise ValueError(f"Case is not bound to submission-v2: {path}")
        rows[seqname] = row
    if not rows:
        raise ValueError(f"No cases found under {run_dir}")
    return rows


def is_canonical_supported(row: Mapping[str, Any]) -> bool:
    metadata = dict(dict(row.get("benchmark_case") or {}).get("metadata") or {})
    source = str(metadata.get("broad_source") or "").strip().lower()
    amino_acid = str(metadata.get("amino_acid") or "").strip().lower()
    return source in CANONICAL_SOURCES and amino_acid not in {
        "sec",
        "selenocysteine",
    }


def template_identity_bin(row: Mapping[str, Any]) -> str:
    benchmark_case = dict(row.get("benchmark_case") or {})
    identity = float(benchmark_case.get("nearest_template_identity") or 0.0)
    if identity >= 0.95:
        return "0.95_to_below_1.00"
    if identity >= 0.90:
        return "0.90_to_below_0.95"
    return "below_0.90"


def _weighted_accuracy(
    rows: Sequence[Mapping[str, Any]],
    correct_field: str,
    total_field: str,
) -> float | None:
    correct = sum(int(dict(row.get("metrics") or {}).get(correct_field) or 0) for row in rows)
    total = sum(int(dict(row.get("metrics") or {}).get(total_field) or 0) for row in rows)
    return correct / total if total else None


def summarize_cohort(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    accepted = [
        row
        for row in rows
        if str(dict(row.get("agent") or {}).get("disposition") or "")
        == "accepted"
    ]
    dispositions = Counter(
        str(dict(row.get("agent") or {}).get("disposition") or "unknown")
        for row in rows
    )
    return {
        "case_count": len(rows),
        "dispositions": dict(sorted(dispositions.items())),
        "accepted_count": len(accepted),
        "accepted_coverage": len(accepted) / len(rows) if rows else None,
        "accepted_exact_match_rate": (
            sum(bool(dict(row.get("metrics") or {}).get("exact_match")) for row in accepted)
            / len(accepted)
            if accepted
            else None
        ),
        "accepted_residue_label_accuracy": _weighted_accuracy(
            accepted,
            "label_correct",
            "label_total",
        ),
        "accepted_variable_accuracy": _weighted_accuracy(
            accepted,
            "variable_correct",
            "variable_total",
        ),
        "accepted_anticodon_accuracy": _weighted_accuracy(
            accepted,
            "anticodon_correct",
            "anticodon_total",
        ),
        "accepted_cca_accuracy": _weighted_accuracy(
            accepted,
            "cca_correct",
            "cca_total",
        ),
    }


def summarize_run(run_dir: Path, rows: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    all_rows = list(rows.values())
    canonical = [row for row in all_rows if is_canonical_supported(row)]
    specialized = [row for row in all_rows if not is_canonical_supported(row)]
    identity_strata = {
        label: summarize_cohort(
            [row for row in canonical if template_identity_bin(row) == label]
        )
        for label in (
            "below_0.90",
            "0.90_to_below_0.95",
            "0.95_to_below_1.00",
        )
    }
    return {
        "run_id": str(manifest.get("run_id") or ""),
        "git_commit": str(manifest.get("git_commit") or ""),
        "git_worktree_dirty": bool(manifest.get("git_worktree_dirty")),
        "run_contract_sha256": str(manifest.get("run_contract_sha256") or ""),
        "selected_seqnames_sha256": str(
            manifest.get("selected_seqnames_sha256") or ""
        ),
        "llm_endpoint": dict(manifest.get("llm_endpoint") or {}),
        "llm_usage": dict(summary.get("llm_usage") or {}),
        "timing": dict(summary.get("timing") or {}),
        "cohorts": {
            "all_primary_references": summarize_cohort(all_rows),
            "canonical_supported_scope": summarize_cohort(canonical),
            "specialized_out_of_scope": summarize_cohort(specialized),
        },
        "canonical_template_identity_strata": identity_strata,
    }


def main() -> None:
    args = parse_args()
    named_runs = [parse_run(value) for value in args.run]
    labels = [label for label, _ in named_runs]
    if len(labels) != len(set(labels)):
        raise ValueError("Run labels must be unique")

    loaded = {label: load_cases(path) for label, path in named_runs}
    reference_names = set(next(iter(loaded.values())))
    for label, rows in loaded.items():
        if set(rows) != reference_names:
            raise ValueError(f"Run case sets differ for {label}")

    output = {
        "schema_version": "submission_v2_results_v1",
        "case_count": len(reference_names),
        "scope_definition": {
            "canonical_sources": sorted(CANONICAL_SOURCES),
            "excluded_from_canonical_scope": [
                "mitochondrial",
                "virus",
                "selenocysteine",
            ],
        },
        "systems": {
            label: summarize_run(path, loaded[label])
            for label, path in named_runs
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
