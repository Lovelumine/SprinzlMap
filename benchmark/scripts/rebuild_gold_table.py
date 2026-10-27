#!/usr/bin/env python3
"""Materialize ``trna_gold``: one active answer per sequence.

Raw tRNAdb numbering plus the newest committed curation decision.  Run this
after importing decisions, and with ``--verify`` to confirm that the answer
table and the audit log still agree.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.domain.gold_curation import service  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument(
        "--seqnames-file",
        type=Path,
        default=None,
        help="Optional list of seqnames to refresh instead of the whole library",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    names = None
    if args.seqnames_file:
        names = [line.strip() for line in args.seqnames_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    stats = service.rebuild_gold_table(write=not args.dry_run, seqnames=names)
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
