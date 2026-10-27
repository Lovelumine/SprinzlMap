#!/usr/bin/env python3
"""Paired comparison for two numbering benchmark runs."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
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
    parser.add_argument("--left-run", type=Path, required=True)
    parser.add_argument("--right-run", type=Path, required=True)
    parser.add_argument("--left-label", default="left")
    parser.add_argument("--right-label", default="right")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument(
        "--scope",
        choices=("all", "canonical_supported"),
        default="all",
    )
    parser.add_argument(
        "--gold-scope",
        choices=("all", "scored"),
        default="all",
        help="Use every case or only cases whose frozen manifest permits gold scoring.",
    )
    parser.add_argument(
        "--bootstrap-unit",
        choices=("case", "similarity_cluster"),
        default="case",
        help="Resampling unit for paired confidence intervals.",
    )
    parser.add_argument(
        "--broad-source",
        default="",
        help=(
            "Optionally restrict both runs to one frozen benchmark "
            "metadata.broad_source value."
        ),
    )
    parser.add_argument(
        "--exclude-source-label-conflicts",
        action="store_true",
        help="Exclude cases flagged by the frozen manifest as source-label conflicts.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_cases(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    cases_dir = run_dir / "cases"
    rows: Dict[str, Dict[str, Any]] = {}
    for path in sorted(cases_dir.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        seqname = str(row.get("seqname") or "")
        if not seqname:
            raise ValueError(f"missing seqname in {path}")
        if seqname in rows:
            raise ValueError(f"duplicate seqname in run: {seqname}")
        rows[seqname] = row
    if not rows:
        raise ValueError(f"no case JSON files found under {cases_dir}")
    return rows


def disposition(row: Mapping[str, Any]) -> str:
    return str(dict(row.get("agent") or {}).get("disposition") or "unknown")


def is_accepted(row: Mapping[str, Any]) -> bool:
    return disposition(row) == "accepted"


def is_exact(row: Mapping[str, Any]) -> bool:
    return bool(dict(row.get("metrics") or {}).get("exact_match"))


def is_canonical_supported(row: Mapping[str, Any]) -> bool:
    metadata = dict(dict(row.get("benchmark_case") or {}).get("metadata") or {})
    source = str(metadata.get("broad_source") or "").strip().lower()
    amino_acid = str(metadata.get("amino_acid") or "").strip().lower()
    return source in CANONICAL_SOURCES and amino_acid not in {
        "sec",
        "selenocysteine",
    }


def broad_source(row: Mapping[str, Any]) -> str:
    metadata = dict(dict(row.get("benchmark_case") or {}).get("metadata") or {})
    return str(metadata.get("broad_source") or "").strip().lower()


def filter_broad_source(
    left: Mapping[str, Mapping[str, Any]],
    right: Mapping[str, Mapping[str, Any]],
    source: str,
) -> tuple[Dict[str, Mapping[str, Any]], Dict[str, Mapping[str, Any]]]:
    normalized = str(source or "").strip().lower()
    if not normalized:
        return dict(left), dict(right)
    selected = []
    for name in sorted(left):
        left_source = broad_source(left[name])
        right_source = broad_source(right[name])
        if left_source != right_source:
            raise ValueError(f"broad-source metadata differs for {name}")
        if left_source == normalized:
            selected.append(name)
    if not selected:
        raise ValueError(f"broad-source selection is empty: {normalized}")
    return (
        {name: left[name] for name in selected},
        {name: right[name] for name in selected},
    )


def filter_source_label_conflicts(
    left: Mapping[str, Mapping[str, Any]],
    right: Mapping[str, Mapping[str, Any]],
    *,
    exclude: bool,
) -> tuple[Dict[str, Mapping[str, Any]], Dict[str, Mapping[str, Any]]]:
    if not exclude:
        return dict(left), dict(right)
    selected = []
    for name in sorted(left):
        left_conflict = bool(
            dict(left[name].get("benchmark_case") or {}).get(
                "source_label_conflict"
            )
        )
        right_conflict = bool(
            dict(right[name].get("benchmark_case") or {}).get(
                "source_label_conflict"
            )
        )
        if left_conflict != right_conflict:
            raise ValueError(f"source-label-conflict metadata differs for {name}")
        if not left_conflict:
            selected.append(name)
    if not selected:
        raise ValueError("source-label-conflict exclusion removed every case")
    return (
        {name: left[name] for name in selected},
        {name: right[name] for name in selected},
    )


def filter_scope(
    left: Mapping[str, Mapping[str, Any]],
    right: Mapping[str, Mapping[str, Any]],
    scope: str,
) -> tuple[Dict[str, Mapping[str, Any]], Dict[str, Mapping[str, Any]]]:
    if set(left) != set(right):
        raise ValueError("run case sets differ before scope filtering")
    if scope == "all":
        return dict(left), dict(right)
    selected = []
    for name in sorted(left):
        left_in_scope = is_canonical_supported(left[name])
        right_in_scope = is_canonical_supported(right[name])
        if left_in_scope != right_in_scope:
            raise ValueError(f"scope metadata differs for {name}")
        if left_in_scope:
            selected.append(name)
    if not selected:
        raise ValueError(f"scope selection is empty: {scope}")
    return (
        {name: left[name] for name in selected},
        {name: right[name] for name in selected},
    )


def gold_evaluation_allowed(row: Mapping[str, Any]) -> bool:
    if "gold_evaluation_allowed" in row:
        return bool(row.get("gold_evaluation_allowed"))
    benchmark_case = dict(row.get("benchmark_case") or {})
    return bool(benchmark_case.get("gold_evaluation_allowed", True))


def filter_gold_scope(
    left: Mapping[str, Mapping[str, Any]],
    right: Mapping[str, Mapping[str, Any]],
    gold_scope: str,
) -> tuple[Dict[str, Mapping[str, Any]], Dict[str, Mapping[str, Any]]]:
    if set(left) != set(right):
        raise ValueError("run case sets differ before gold-scope filtering")
    if gold_scope == "all":
        return dict(left), dict(right)
    selected: list[str] = []
    for name in sorted(left):
        left_allowed = gold_evaluation_allowed(left[name])
        right_allowed = gold_evaluation_allowed(right[name])
        if left_allowed != right_allowed:
            raise ValueError(f"gold score mask differs for {name}")
        if left_allowed:
            selected.append(name)
    if not selected:
        raise ValueError("gold-scored selection is empty")
    return (
        {name: left[name] for name in selected},
        {name: right[name] for name in selected},
    )


def bootstrap_groups(
    names: Sequence[str],
    rows: Mapping[str, Mapping[str, Any]],
    unit: str,
) -> list[list[str]]:
    if unit == "case":
        return [[name] for name in names]
    grouped: Dict[str, list[str]] = {}
    for name in names:
        benchmark_case = dict(rows[name].get("benchmark_case") or {})
        cluster_id = str(
            dict(benchmark_case.get("similarity_cluster") or {}).get("cluster_id")
            or name
        )
        grouped.setdefault(cluster_id, []).append(name)
    return [sorted(grouped[key]) for key in sorted(grouped)]


def label_counts(row: Mapping[str, Any]) -> tuple[int, int]:
    metrics = dict(row.get("metrics") or {})
    return int(metrics.get("label_correct") or 0), int(metrics.get("label_total") or 0)


def subset_metrics(
    rows: Mapping[str, Mapping[str, Any]],
    seqnames: Sequence[str],
) -> Dict[str, Any]:
    selected = [name for name in seqnames if is_accepted(rows[name])]
    correct = 0
    total = 0
    for name in selected:
        case_correct, case_total = label_counts(rows[name])
        correct += case_correct
        total += case_total
    return {
        "accepted_count": len(selected),
        "exact_match_rate": (
            sum(is_exact(rows[name]) for name in selected) / len(selected)
            if selected
            else None
        ),
        "residue_label_accuracy": correct / total if total else None,
    }


def cohort_metrics(
    rows: Mapping[str, Mapping[str, Any]],
    seqnames: Sequence[str],
) -> Dict[str, Any]:
    output = subset_metrics(rows, seqnames)
    output["accepted_coverage"] = (
        output["accepted_count"] / len(seqnames) if seqnames else None
    )
    output["dispositions"] = dict(
        sorted(Counter(disposition(rows[name]) for name in seqnames).items())
    )
    return output


def percentile_interval(values: list[float]) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    return [
        ordered[int(0.025 * len(ordered))],
        ordered[int(0.975 * len(ordered))],
    ]


def compare_runs(
    left: Mapping[str, Mapping[str, Any]],
    right: Mapping[str, Mapping[str, Any]],
    *,
    left_label: str,
    right_label: str,
    bootstrap_samples: int,
    seed: int,
    bootstrap_unit: str = "case",
) -> Dict[str, Any]:
    left_names = set(left)
    right_names = set(right)
    if left_names != right_names:
        raise ValueError(
            "run case sets differ: "
            f"left_only={len(left_names - right_names)} "
            f"right_only={len(right_names - left_names)}"
        )
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")

    names = sorted(left_names)
    common_accepted = [
        name for name in names if is_accepted(left[name]) and is_accepted(right[name])
    ]
    left_only = [
        name for name in names if is_accepted(left[name]) and not is_accepted(right[name])
    ]
    right_only = [
        name for name in names if is_accepted(right[name]) and not is_accepted(left[name])
    ]
    transition = Counter((disposition(right[name]), disposition(left[name])) for name in names)
    exact_transition = Counter(
        (is_exact(right[name]), is_exact(left[name])) for name in common_accepted
    )

    left_common_metrics = subset_metrics(left, common_accepted)
    right_common_metrics = subset_metrics(right, common_accepted)
    rng = random.Random(seed)
    coverage_groups = bootstrap_groups(names, left, bootstrap_unit)
    common_groups = bootstrap_groups(common_accepted, left, bootstrap_unit)
    coverage_differences: list[float] = []
    exact_differences: list[float] = []
    residue_differences: list[float] = []
    for _ in range(bootstrap_samples):
        sampled_coverage_groups = rng.choices(
            coverage_groups,
            k=len(coverage_groups),
        )
        all_sample = [name for group in sampled_coverage_groups for name in group]
        coverage_differences.append(
            sum(
                is_accepted(left[name]) - is_accepted(right[name])
                for name in all_sample
            )
            / len(all_sample)
        )

        if not common_groups:
            continue
        sampled_common_groups = rng.choices(common_groups, k=len(common_groups))
        common_sample = [name for group in sampled_common_groups for name in group]
        exact_differences.append(
            sum(is_exact(left[name]) - is_exact(right[name]) for name in common_sample)
            / len(common_sample)
        )
        left_correct = 0
        right_correct = 0
        label_total = 0
        for name in common_sample:
            case_left_correct, case_total = label_counts(left[name])
            case_right_correct, _ = label_counts(right[name])
            left_correct += case_left_correct
            right_correct += case_right_correct
            label_total += case_total
        residue_differences.append((left_correct - right_correct) / label_total)

    return {
        "left_label": left_label,
        "right_label": right_label,
        "case_count": len(names),
        "disposition_transition_right_to_left": {
            f"{before}->{after}": count
            for (before, after), count in sorted(transition.items())
        },
        "common_accepted_count": len(common_accepted),
        "left_only_accepted_count": len(left_only),
        "right_only_accepted_count": len(right_only),
        "cohort_metrics": {
            left_label: cohort_metrics(left, names),
            right_label: cohort_metrics(right, names),
        },
        "common_exact_transition_right_to_left": {
            f"{before}->{after}": count
            for (before, after), count in sorted(exact_transition.items())
        },
        "common_accepted_metrics": {
            left_label: left_common_metrics,
            right_label: right_common_metrics,
        },
        "left_only_accepted_metrics": subset_metrics(left, left_only),
        "right_only_accepted_metrics": subset_metrics(right, right_only),
        "paired_bootstrap": {
            "samples": bootstrap_samples,
            "seed": seed,
            "unit": bootstrap_unit,
            "coverage_unit_count": len(coverage_groups),
            "common_accepted_unit_count": len(common_groups),
            "difference_direction": f"{left_label}_minus_{right_label}",
            "accepted_coverage": {
                "estimate": sum(
                    is_accepted(left[name]) - is_accepted(right[name]) for name in names
                )
                / len(names),
                "ci95": percentile_interval(coverage_differences),
            },
            "common_accepted_exact_match_rate": {
                "estimate": (
                    left_common_metrics["exact_match_rate"]
                    - right_common_metrics["exact_match_rate"]
                    if common_accepted
                    else None
                ),
                "ci95": (
                    percentile_interval(exact_differences)
                    if exact_differences
                    else None
                ),
            },
            "common_accepted_residue_label_accuracy": {
                "estimate": (
                    left_common_metrics["residue_label_accuracy"]
                    - right_common_metrics["residue_label_accuracy"]
                    if common_accepted
                    else None
                ),
                "ci95": (
                    percentile_interval(residue_differences)
                    if residue_differences
                    else None
                ),
            },
        },
    }


def run_metadata(run_dir: Path) -> Dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_contract = dict(manifest.get("run_contract") or {})
    return {
        "run_id": manifest.get("run_id"),
        "git_commit": manifest.get("git_commit") or run_contract.get("git_commit"),
        "git_worktree_dirty": (
            manifest.get("git_worktree_dirty")
            if "git_worktree_dirty" in manifest
            else run_contract.get("git_worktree_dirty")
        ),
        "run_contract_sha256": manifest.get("run_contract_sha256"),
        "selected_seqnames_sha256": manifest.get("selected_seqnames_sha256"),
        "full_run_counts": summary.get("counts"),
        "full_run_llm_usage": summary.get("llm_usage"),
        "full_run_timing": summary.get("timing"),
    }


def main() -> None:
    args = parse_args()
    args.left_run = resolve_run_dir(args.left_run)
    args.right_run = resolve_run_dir(args.right_run)
    scoped_left, scoped_right = filter_scope(
        load_cases(args.left_run),
        load_cases(args.right_run),
        args.scope,
    )
    source_left, source_right = filter_broad_source(
        scoped_left,
        scoped_right,
        args.broad_source,
    )
    conflict_left, conflict_right = filter_source_label_conflicts(
        source_left,
        source_right,
        exclude=bool(args.exclude_source_label_conflicts),
    )
    left, right = filter_gold_scope(
        conflict_left,
        conflict_right,
        args.gold_scope,
    )
    comparison = compare_runs(
        left,
        right,
        left_label=args.left_label,
        right_label=args.right_label,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
        bootstrap_unit=args.bootstrap_unit,
    )
    output = {
        "scope": args.scope,
        "broad_source": str(args.broad_source or "").strip().lower(),
        "source_label_conflicts_excluded": bool(
            args.exclude_source_label_conflicts
        ),
        "gold_scope": args.gold_scope,
        "left_run": run_metadata(args.left_run),
        "right_run": run_metadata(args.right_run),
        "comparison": comparison,
    }
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
