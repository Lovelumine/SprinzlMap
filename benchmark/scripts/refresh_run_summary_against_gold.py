#!/usr/bin/env python3
"""Restate one stored run against the current answer table.

A finished run is a frozen set of predictions.  Its scores are not frozen: every
reader grades those predictions against ``trna_gold`` while it reads, so the
application and the paper figures already show today's numbers.  The one place a
run still carries its own scores is the aggregate written when it was sealed
(``summary_json`` and ``counts_json``), and that aggregate is what this tool
refreshes.  Nothing here calls the model, and the traces in MinIO are left
byte-identical.

Dry run by default; ``--apply`` writes.

Usage:
    python benchmark/scripts/refresh_run_summary_against_gold.py \
        --run-id 20261020-164533-deepseekv4-flash-guangdong
    python benchmark/scripts/refresh_run_summary_against_gold.py --run-id ... --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "APP") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.domain.benchmarking.case_index import (  # noqa: E402
    summarize_case_rows,
)
from backend.infra.storage import sequence_repository as repo  # noqa: E402
from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    connect_store,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Write the freshly graded summary back to MySQL")
    parser.add_argument("--seqname", action="append", default=None,
                        help="Limit the re-grade to named cases")
    parser.add_argument("--limit", type=int, default=0, help="Re-grade only the first N cases")
    return parser.parse_args()


def headline(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics = dict(summary.get("metrics") or {})
    counts = dict(summary.get("counts") or {})
    return {
        "residue_accuracy": metrics.get("residue_label_accuracy"),
        "exact_match_rate": metrics.get("exact_match_rate"),
        "legality_pass_rate": metrics.get("legality_pass_rate"),
        "variable_loop_accuracy": metrics.get("variable_accuracy"),
        "scored_cases": counts.get("gold_scored_cases"),
        "unscored_cases": counts.get("gold_unscored_cases"),
        "accepted_cases": counts.get("accepted_cases"),
    }


def main() -> int:
    args = parse_args()
    try:
        store = connect_store(args.run_id, create_schema=False)
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        return 2

    run = store.run_row() or {}
    stored_summary_raw = run.get("summary_json")
    stored_summary = (
        json.loads(stored_summary_raw)
        if isinstance(stored_summary_raw, str)
        else (stored_summary_raw or {})
    )

    # ``case_rows()`` grades on read, so these rows carry the scores the current
    # answers give to predictions that have not moved since the run finished.
    live_rows = store.case_rows()
    if not live_rows:
        raise SystemExit(f"run {args.run_id} has no stored cases")

    names = [str(row["seqname"]) for row in live_rows]
    if args.seqname:
        wanted = {str(value) for value in args.seqname}
        names = [name for name in names if name in wanted]
    if args.limit:
        names = names[: args.limit]
    by_name = {str(row["seqname"]): row for row in live_rows}
    subset = [by_name[name] for name in names]
    live_summary = summarize_case_rows(subset)

    report = {
        "run_id": args.run_id,
        "state": run.get("state"),
        "model_id": run.get("model_id"),
        "cases": len(names),
        "answers_adopted_from_review": sum(
            1 for row in subset if str(row.get("gold_source") or "") not in ("", "raw")
        ),
        "answers_revised_after_seal": int(
            (store.revised_answers() or {}).get("revised") or 0
        ),
        "sealed_summary": headline(stored_summary) if stored_summary else None,
        "current_summary": headline(live_summary),
        "applied": bool(args.apply),
    }
    if not args.apply:
        report["note"] = "dry run: nothing written"
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    summary = dict(live_summary)
    summary["summary_refresh"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gold_table": "mysql://trna_gold",
        "gold_table_sha256": repo.gold_table_digest(names),
        "model_calls_reused": True,
        "cases": len(names),
    }
    store.refresh_run_summary(summary)
    refreshed = store.run_row() or {}
    refreshed_summary = refreshed.get("summary_json")
    if isinstance(refreshed_summary, str):
        refreshed_summary = json.loads(refreshed_summary)
    report["sealed_summary_after_refresh"] = headline(refreshed_summary or {})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2)
