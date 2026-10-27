#!/usr/bin/env python3
"""Measure what a variable-arm coordinate generator could still fix.

Reads stored executions and the active answer table only, so it costs no model
calls.  For every case that no sealed arm numbers exactly, it asks:

1. are all remaining errors inside the variable region?
2. does the enumerated set of legal coordinate rows contain the answer tRNAdb
   itself used for those residues?
3. how large is that set, i.e. how hard is the choice the model would face?
4. is the answer even self-consistent in the reference, measured by grouping
   every annotated tRNA by the base string of its variable region?

The output decides whether an extra generator is worth running at all, and it
bounds the gain before any benchmark budget is spent.
"""
from __future__ import annotations

import argparse
import collections
import json
from typing import Any, Dict, List, Sequence

from backend.domain.alignment.variable_pattern_proposal import (
    enumerate_variable_patterns,
)
from backend.domain.benchmarking.case_index import parse_alignment_csv, safe_region
from backend.infra.storage.benchmark_run_store import BenchmarkRunStore
from backend.infra.storage.sequence_repository import connection


VARIABLE_REGION = "Variable loop"
GAP_TOKENS = {"", "-", "."}


def _is_gap(base: Any) -> bool:
    return str(base or "").strip() in GAP_TOKENS


def _variable_columns(labels: Sequence[str]) -> List[int]:
    return [index for index, label in enumerate(labels) if safe_region(str(label)) == VARIABLE_REGION]


def load_executions(run_id: str) -> Dict[str, str]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seqname, case_prediction_csv FROM benchmark_executions "
                "WHERE record_type='case' AND run_id=%s",
                (run_id,),
            )
            return {str(row["seqname"]): row["case_prediction_csv"] or "" for row in cur.fetchall()}


def load_gold() -> Dict[str, tuple[List[str], List[str], int]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT seqname, gold_labels, gold_bases, evaluation_allowed FROM trna_gold")
            return {
                str(row["seqname"]): (
                    json.loads(row["gold_labels"] or "[]"),
                    json.loads(row["gold_bases"] or "[]"),
                    int(row["evaluation_allowed"] or 0),
                )
                for row in cur.fetchall()
            }


def load_trace_keys(run_id: str) -> Dict[str, str]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seqname, case_trace_key FROM benchmark_executions "
                "WHERE record_type='case' AND run_id=%s AND case_trace_key IS NOT NULL",
                (run_id,),
            )
            return {str(row["seqname"]): str(row["case_trace_key"]) for row in cur.fetchall()}


def variable_pair_counts(run_id: str, seqnames: Sequence[str]) -> Dict[str, int]:
    """Base pairs the covariance model observed inside the variable region."""

    store = BenchmarkRunStore(run_id)
    keys = {name: key for name, key in load_trace_keys(run_id).items() if name in set(seqnames)}
    found: Dict[str, int] = {}
    for seqname, key in keys.items():
        body = store.get_object(key)
        if not body:
            continue
        try:
            doc = json.loads(body)
            evidence = doc["scientific_provenance"]["structural_region_evidence"]
            found[seqname] = int(evidence["region_pair_counts"][VARIABLE_REGION])
        except (KeyError, TypeError, ValueError):
            found[seqname] = 0
    return found


def exact_failures(preds: Dict[str, str], gold: Dict[str, Any], other_runs: Sequence[str]) -> List[str]:
    """Cases this run misses and no sealed arm misses either."""

    failures = set()
    for seqname, csv_text in preds.items():
        answer = gold.get(seqname)
        if not answer or not answer[2] or not answer[0]:
            continue
        labels, _template, target = parse_alignment_csv(csv_text)
        predicted = [str(label) for label, base in zip(labels, target) if not _is_gap(base)]
        if len(predicted) != len(answer[0]) or predicted != answer[0]:
            failures.add(seqname)
    if not other_runs:
        return sorted(failures)
    # Each arm is graded from its own frozen prediction here, never from a score
    # written beside it: an answer edited after a run changes the verdict, and
    # the census has to follow.
    solved = set()
    for run_id in other_runs:
        for seqname, csv_text in load_executions(run_id).items():
            answer = gold.get(seqname)
            if not answer or not answer[2] or not answer[0]:
                continue
            labels, _template, target = parse_alignment_csv(csv_text)
            predicted = [str(label) for label, base in zip(labels, target) if not _is_gap(base)]
            if len(predicted) == len(answer[0]) and predicted == answer[0]:
                solved.add(seqname)
    return sorted(seqname for seqname in failures if not solved[seqname])


def reference_self_consistency(gold: Dict[str, Any]) -> Dict[str, Any]:
    counts: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for labels, bases, allowed in gold.values():
        if not allowed or not labels or len(labels) != len(bases):
            continue
        region = [(label, base) for label, base in zip(labels, bases) if safe_region(str(label)) == VARIABLE_REGION]
        if len(region) < 6:
            continue
        counts["".join(str(base) for _label, base in region)]["|".join(str(label) for label, _base in region)] += 1
    long_arm = sum(sum(counter.values()) for counter in counts.values())
    duplicated = sum(sum(c.values()) for c in counts.values() if len(c) > 1)
    with_partner = sum(sum(c.values()) for c in counts.values() if sum(c.values()) > 1)
    return {
        "long_arm_cases": long_arm,
        "distinct_region_sequences": len(counts),
        "sequences_numbered_more_than_one_way": sum(1 for c in counts.values() if len(c) > 1),
        "cases_affected_by_inconsistency": duplicated,
        "cases_with_a_shared_sequence": with_partner,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sealed-runs", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--prune-by-observed-pairs", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()

    preds = load_executions(args.run_id)
    gold = load_gold()
    failures = exact_failures(preds, gold, args.sealed_runs)

    bucket = collections.Counter()
    candidate_sizes: List[int] = []
    reached: List[str] = []
    pairs = variable_pair_counts(args.run_id, failures)
    for seqname in failures:
        answer = gold.get(seqname)
        if not answer or not answer[0]:
            bucket["no_answer"] += 1
            continue
        labels, template, target = parse_alignment_csv(preds[seqname])
        predicted = [str(label) for label, base in zip(labels, target) if not _is_gap(base)]
        gold_labels = answer[0]
        if len(predicted) != len(gold_labels):
            bucket["residue_count_mismatch"] += 1
            continue
        diffs = [i for i, (left, right) in enumerate(zip(predicted, gold_labels)) if left != right]
        if not diffs:
            bucket["already_exact"] += 1
            continue
        columns = _variable_columns(labels)
        touched = {
            i
            for i in diffs
            if safe_region(predicted[i]) == VARIABLE_REGION
            or safe_region(gold_labels[i]) == VARIABLE_REGION
        }
        if not touched:
            bucket["diffs_outside_variable_region"] += 1
            continue
        if len(touched) != len(diffs):
            bucket["diffs_span_variable_and_other_regions"] += 1
            continue
        block_labels = [str(labels[i]) for i in columns]
        block_target = [str(target[i]) for i in columns]
        region_residues = [
            i for i in range(len(predicted)) if safe_region(predicted[i]) == VARIABLE_REGION
        ]
        wanted = [gold_labels[i] for i in region_residues]
        observed = pairs.get(seqname) if args.prune_by_observed_pairs else None
        candidates = enumerate_variable_patterns(
            block_labels,
            block_target,
            observed_pairs=observed,
            limit=args.limit,
        )
        bucket["variable_only_diffs"] += 1
        candidate_sizes.append(len(candidates))
        if any(list(item.coordinates) == wanted for item in candidates):
            bucket["enumeration_reaches_the_reference_answer"] += 1
            reached.append(seqname)
        else:
            bucket["enumeration_misses_the_reference_answer"] += 1

    summary = {
        "run_id": args.run_id,
        "cases_in_run": len(preds),
        "cases_no_sealed_arm_numbers_exactly": len(failures),
        "pruned_by_observed_pairs": bool(args.prune_by_observed_pairs),
        "buckets": dict(sorted(bucket.items())),
        "candidate_set_median": (
            sorted(candidate_sizes)[len(candidate_sizes) // 2] if candidate_sizes else None
        ),
        "candidate_set_max": max(candidate_sizes) if candidate_sizes else None,
        "reference_self_consistency": reference_self_consistency(gold),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"summary": summary, "reached_seqnames": reached}, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
