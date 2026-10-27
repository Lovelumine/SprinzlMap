#!/usr/bin/env python3
"""Delete an unwanted benchmark run from MySQL and MinIO.

Intended for interrupted or mislabelled runs.  A sealed run is refused unless
``--allow-sealed`` is given, so a run that backs a reported number cannot be
removed by accident.

Usage:
    python benchmark/scripts/discard_benchmark_run.py --run-id 20261023-041409-control-cm-direct
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    EXECUTIONS_TABLE,
    BenchmarkStoreError,
    BenchmarkRunStore,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-sealed", action="store_true",
                        help="Also delete a run that has been sealed")
    args = parser.parse_args()

    store = BenchmarkRunStore(args.run_id)
    row = store.run_row()
    if row is None:
        print(f"no such run in MySQL: {args.run_id}")
        return 0
    if row.get("state") == "sealed" and not args.allow_sealed:
        print(f"{args.run_id} is sealed; pass --allow-sealed to discard it", file=sys.stderr)
        return 2

    counts = store.record_counts()
    keys = list(store.list_object_keys())

    def remove(key: str) -> None:
        # MinIO rejects the batch call without a Content-MD5 header, so delete
        # one object at a time; the counts are small enough that this is fine.
        store._s3.delete_object(Bucket=store.bucket, Key=key)  # noqa: SLF001

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(remove, keys))
    with store.tx() as cursor:
        cursor.execute(f"DELETE FROM {EXECUTIONS_TABLE} WHERE run_id=%s", (args.run_id,))
    print(
        f"discarded {args.run_id}: {counts.get('case', 0)} case rows, "
        f"{counts.get('attempt', 0)} attempt rows, {len(keys)} MinIO objects"
    )
    leftover = store.run_row()
    if leftover is not None:
        print(f"run row for {args.run_id} still present", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2)
