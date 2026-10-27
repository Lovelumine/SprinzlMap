#!/usr/bin/env python3
"""Materialize a stored run back onto disk.

Runs normally live only in MySQL (index) and MinIO (traces).  A figure or
report script that expects ``benchmark/runs/<run_id>/`` can be pointed at a
temporary copy produced here; that directory is gitignored, so the repository
never accumulates run output again.

By default the copy is graded against the current answer table: the traces keep
their frozen predictions, and ``case_results.tsv``, ``summary.json`` and the
per-case files carry the scores today's answers produce.  ``--as-stored`` writes
exactly the objects the run itself stored.

Usage:
    python benchmark/scripts/export_run_from_store.py \
        --run-id 20261020-164533-deepseekv4-flash-guangdong
    python benchmark/scripts/export_run_from_store.py \
        --run-id 20261020-164533-deepseekv4-flash-guangdong --as-stored
    python benchmark/scripts/export_run_from_store.py --list-runs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    BenchmarkRunStore,
    materialize_run,
)


def list_runs(like: str, limit: int) -> int:
    """Print the runs recorded in MySQL, newest first."""
    try:
        rows = BenchmarkRunStore.readonly().run_rows(like=like, limit=limit)
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        return 2
    for row in rows:
        print(
            f"{row['run_id']}\t{row['state']}\t"
            f"{row['n_completed']}/{row['n_targets']}\t{row.get('model_id') or ''}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id")
    parser.add_argument("--list-runs", action="store_true",
                        help="Print every run recorded in MySQL and exit")
    parser.add_argument("--like", default="", help="Filter --list-runs by run id substring")
    parser.add_argument("--limit", type=int, default=200, help="Row cap for --list-runs")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Destination. Defaults to .runtime/runs/<run-id>, which git ignores.",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--as-stored", action="store_true",
                        help="Write the run's own objects unchanged instead of re-grading them")
    args = parser.parse_args()

    if args.list_runs:
        raise SystemExit(list_runs(args.like, args.limit))
    if not args.run_id:
        parser.error("--run-id is required unless --list-runs is given")

    try:
        target = materialize_run(
            args.run_id, args.out_dir, workers=args.workers,
            live_answer=not args.as_stored,
        )
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2)

    print(json.dumps({
        "run_id": args.run_id,
        "out_dir": str(target),
        "answer_basis": "as-stored" if args.as_stored else "current trna_gold answer table",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
