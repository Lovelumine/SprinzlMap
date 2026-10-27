#!/usr/bin/env python3
"""Stage the active answer table (MySQL ``trna_gold``) as a run input.

Writes ``reference.tsv`` plus ``reference.provenance.json``, which records the
per-sequence answer fingerprint and where that answer came from.  The TSV is a
freeze of the moment of staging: a run keeps its own copy in MinIO, while the
library answer itself stays single and mutable.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for relative in ("APP", "benchmark/scripts"):
    if str(PROJECT_ROOT / relative) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / relative))

from backend.domain.gold_curation import service  # noqa: E402
from backend.infra.storage import benchmark_run_store as store_lib  # noqa: E402
from backend.infra.storage import sequence_repository as repo  # noqa: E402


def stage_gold(
    seqnames: Iterable[str],
    out_dir: Path,
    *,
    include_excluded: bool = False,
) -> Dict[str, Any]:
    names = [str(v).strip() for v in seqnames if str(v).strip()]
    if not names:
        raise ValueError("stage_gold needs at least one seqname")
    answers = repo.gold_rows(names)
    missing = sorted(set(names) - set(answers))
    if missing:
        raise ValueError(
            f"{len(missing)} sequences have no answer row; run "
            f"benchmark/scripts/rebuild_gold_table.py first (e.g. {missing[:3]})"
        )
    kept: Dict[str, Dict[str, Any]] = {}
    dropped: List[str] = []
    for seqname in names:
        row = answers[seqname]
        if row["gold_source"] == "excluded" and not include_excluded:
            dropped.append(seqname)
            continue
        kept[seqname] = row

    fieldnames, matrix = service.table_from_gold_rows(kept.values())
    out_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = out_dir / "reference.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fieldnames), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for seqname in [name for name in names if name in matrix]:
            writer.writerow({"seqname": seqname, **matrix[seqname]})

    provenance = {
        "table": "mysql://trna_gold",
        "table_sha256": repo.gold_table_digest(kept.keys()),
        "sequences": {
            seqname: {
                "gold_sha256": str(row["gold_sha256"]),
                "gold_source": str(row["gold_source"]),
                "decided_at": service._iso_or_none(row.get("decided_at")),
                "decision_hash": row.get("decision_hash"),
                "decision_run_id": row.get("decision_run_id"),
                "evaluation_allowed": bool(row.get("evaluation_allowed", True)),
            }
            for seqname, row in kept.items()
        },
    }
    provenance_path = out_dir / "reference.provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "reference_tsv": tsv_path,
        "reference_sha256": hashlib.sha256(tsv_path.read_bytes()).hexdigest(),
        "provenance_tsv": provenance_path,
        "sequences": len(kept),
        "excluded_from_cohort": dropped,
        "decided": sum(1 for row in kept.values() if row["gold_source"] != "raw"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    roster = parser.add_mutually_exclusive_group()
    roster.add_argument("--seqnames-file", type=Path, help="One seqname per line")
    roster.add_argument("--run-id", help="Reuse the roster of an existing stored run")
    roster.add_argument("--all-gold", action="store_true",
                        help="Write the whole active answer table instead of one cohort")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--include-excluded",
        action="store_true",
        help="Keep sequences a reviewer excluded from scoring (they stay flagged)",
    )
    return parser.parse_args()


def main() -> None:
    if not repo.enabled():
        raise SystemExit("set SEQUENCE_REPOSITORY_ENABLED=1 to stage the answer table")
    args = parse_args()
    if args.all_gold:
        # The flat whole-library view: what the audit scripts read when they
        # work offline.  It is a copy, so it lives under .runtime, not in git.
        out = args.out_dir
        out.mkdir(parents=True, exist_ok=True)
        result = service.export_curated_tsv(out / "mature_benchmark.curated.tsv",
                                           include_excluded=args.include_excluded)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not args.seqnames_file and not args.run_id:
        raise SystemExit("choose --seqnames-file, --run-id or --all-gold")
    if args.run_id:
        runner_store = store_lib.connect_store(args.run_id, create_schema=False)
        names = [
            str(row["seqname"])
            for row in runner_store.case_rows(active_answer=False, columns=["status"])
        ]
    else:
        names = [
            line.strip()
            for line in args.seqnames_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    staged = stage_gold(names, args.out_dir, include_excluded=args.include_excluded)
    print(
        json.dumps(
            {
                "reference_tsv": str(staged["reference_tsv"]),
                "reference_sha256": staged["reference_sha256"],
                "provenance": str(staged["provenance_tsv"]),
                "sequences": staged["sequences"],
                "excluded_from_cohort": len(staged["excluded_from_cohort"]),
                "decided": staged["decided"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
