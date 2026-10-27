#!/usr/bin/env python3
"""Compute exact nearest-template identity for the submission-v2 cohort."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.benchmarking.protocol import (  # noqa: E402
    atomic_write_json,
    atomic_write_jsonl,
    sha256_file,
)
from backend.domain.benchmarking.submission_protocol import (  # noqa: E402
    group_templates_by_length,
    load_gold_label_records,
    load_template_identity_audit,
    load_unique_template_sequences,
    nearest_template_match,
)


_TEMPLATES_BY_LENGTH: dict[int, tuple[tuple[str, str], ...]] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("benchmark/manifests/submission_v2/cases.jsonl"),
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv",
    )
    parser.add_argument("--templates", type=Path, default=Path("data/tRNAs_flat.tsv"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark/manifests/submission_v2/template_identity_audit.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("benchmark/manifests/submission_v2/template_identity_audit.summary.json"),
    )
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 1, 16))
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def _initialize_worker(templates_path: str) -> None:
    global _TEMPLATES_BY_LENGTH
    templates = load_unique_template_sequences(Path(templates_path))
    _TEMPLATES_BY_LENGTH = group_templates_by_length(templates)


def _audit_one(task: tuple[str, str]) -> dict[str, Any]:
    sequence_digest, sequence = task
    match = nearest_template_match(sequence, _TEMPLATES_BY_LENGTH)
    return {
        "schema_version": "1.0",
        "sequence_sha256": sequence_digest,
        "query_length": len(sequence),
        "nearest_template_identity": round(float(match.identity), 12),
        "edit_distance": int(match.edit_distance),
        "template_length": int(match.template_length),
        "template_sequence_sha256": match.template_sequence_sha256,
        "metric": "one_minus_global_levenshtein_over_max_length",
        "status": "audited",
    }


def _load_tasks(cases_path: Path, gold_path: Path) -> list[tuple[str, str]]:
    gold = load_gold_label_records(gold_path)
    tasks: list[tuple[str, str]] = []
    with cases_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            seqname = str(payload.get("representative_seqname") or "")
            digest = str(payload.get("sequence_sha256") or "")
            record = gold.get(seqname)
            if record is None or record.sequence_sha256 != digest:
                raise ValueError(
                    f"Gold sequence mismatch for submission case {cases_path}:{line_number}"
                )
            tasks.append((digest, record.sequence))
    return sorted(tasks)


def main() -> None:
    args = parse_args()
    workers = max(1, int(args.workers))
    tasks = _load_tasks(args.cases, args.gold)
    completed: dict[str, dict[str, Any]] = {}
    if args.resume and args.output.exists():
        completed = load_template_identity_audit(args.output)
    pending = [task for task in tasks if task[0] not in completed]

    if pending:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_initialize_worker,
            initargs=(str(args.templates),),
        ) as executor:
            for index, row in enumerate(
                executor.map(_audit_one, pending, chunksize=1),
                start=1,
            ):
                completed[str(row["sequence_sha256"])] = row
                if args.progress_every > 0 and index % args.progress_every == 0:
                    print(f"audited {index}/{len(pending)} pending cases", flush=True)

    expected = {digest for digest, _ in tasks}
    if set(completed) != expected:
        raise RuntimeError("Identity audit did not cover the exact submission cohort")
    rows = [completed[digest] for digest in sorted(completed)]
    atomic_write_jsonl(args.output, rows)

    bins = Counter()
    for row in rows:
        identity = float(row["nearest_template_identity"])
        if identity >= 1.0:
            bins["exact_1.00"] += 1
        elif identity >= 0.95:
            bins["0.95_to_below_1.00"] += 1
        elif identity >= 0.90:
            bins["0.90_to_below_0.95"] += 1
        else:
            bins["below_0.90"] += 1
    summary = {
        "schema_version": "1.0",
        "metric": "one_minus_global_levenshtein_over_max_length",
        "query_count": len(rows),
        "unique_template_count": len(load_unique_template_sequences(args.templates)),
        "identity_bins": dict(sorted(bins.items())),
        "workers": workers,
        "inputs": {
            "cases_sha256": sha256_file(args.cases),
            "gold_sha256": sha256_file(args.gold),
            "templates_sha256": sha256_file(args.templates),
        },
        "output_sha256": sha256_file(args.output),
    }
    atomic_write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
