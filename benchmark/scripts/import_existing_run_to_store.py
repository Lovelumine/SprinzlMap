#!/usr/bin/env python3
"""Import benchmark runs that were kept on disk into MySQL (index) + MinIO (traces).

Idempotent: re-running replaces the same rows and objects.  The per-case A/B
difference counts are recomputed from the run's own gold input, which is the
same reference the review page renders, so imported runs carry exactly the
columns a live run writes.

Usage:
    python benchmark/scripts/import_existing_run_to_store.py \
        --runs-dir .runtime/runs \
        --run-id <run-id-under-that-directory>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

ROOT = PROJECT_ROOT

from backend.domain.benchmarking.case_index import (  # noqa: E402
    INDEX_FIELDNAMES,
    attach_case_comparison,
)
from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    connect_store,
    default_run_cache_dir,
)

ARTIFACT_FILES = (
    "manifest.json",
    "summary.json",
    "summary.txt",
    "case_results.tsv",
    "REPORT.md",
    "README.md",
    "strata_summary.json",
    "merge_manifest.json",
    "source_map.tsv",
    "controller.log",
    "launch.json",
    "retry_status.json",
    "retry_lineage.json",
    "run.log",
)
ARTIFACT_DIRS = ("inputs", "logs", "provenance", "case_attempts")
PRINT_LOCK = threading.Lock()

CONTENT_TYPES = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".md": "text/markdown",
}


def log(message: str) -> None:
    print(message, flush=True)


def case_digest(seqname: str) -> str:
    return hashlib.sha1(str(seqname).encode("utf-8")).hexdigest()[:16]


def safe_base(raw: Any) -> str:
    base = str(raw or "").strip().upper().replace("T", "U")
    return base if base in {"A", "C", "G", "U", "N"} else "-"


def load_gold_rows(path: Path) -> Dict[str, Tuple[List[str], List[str]]]:
    """Per-residue gold labels and bases, exactly as the runner builds them."""
    out: Dict[str, Tuple[List[str], List[str]]] = {}
    with path.open("r", encoding="utf-8", newline="") as fin:
        reader = csv.reader(fin, delimiter="\t")
        header = next(reader, None)
        if not header or header[0] != "seqname":
            raise ValueError(f"Unexpected gold header in {path}")
        numbering = header[1:]
        for row in reader:
            seqname = str(row[0] or "").strip()
            if not seqname:
                continue
            bases = [safe_base(base) for base in row[1:]]
            labels = [num for num, base in zip(numbering, bases) if base != "-"]
            kept = [base for base in bases if base != "-"]
            out[seqname] = (labels, kept)
    return out


def resolve_gold_path(run_dir: Path, manifest: Dict[str, Any]) -> Path:
    input_files = manifest.get("input_files") or {}
    for candidate in (
        (input_files.get("gold") or {}).get("path") if isinstance(input_files.get("gold"), dict) else input_files.get("gold"),
        manifest.get("input_tsv"),
    ):
        if not candidate:
            continue
        path = Path(str(candidate))
        for option in (path, ROOT / path, run_dir / path.name):
            if option.exists():
                return option
    # The repository no longer keeps a gold table: stage the current answer
    # table first with stage_gold_from_store.py --all-gold, which writes this
    # offline copy under the git-ignored scratch tree.
    fallback = ROOT / ".runtime" / "gold" / "mature_benchmark.curated.tsv"
    if not fallback.exists():
        raise FileNotFoundError(
            f"no gold reference found for this run; stage one into {fallback}"
        )
    return fallback


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def import_cases(store, run_dir: Path, gold: Dict[str, Tuple[List[str], List[str]]], workers: int) -> Dict[str, int]:
    case_files = sorted((run_dir / "cases").glob("*.json"))
    existing = {str(row.get("seqname")): row for row in store.list_rows()}
    stats = {"stored": 0, "skipped": 0, "no_gold": 0, "failed": 0}
    total = len(case_files)
    started = time.time()
    done = {"n": 0}

    def handle(path: Path) -> str:
        result = read_json(path)
        if not isinstance(result, dict):
            return "failed"
        seqname = str(result.get("seqname") or "").strip()
        if not seqname:
            return "failed"
        pair = gold.get(seqname)
        no_gold = pair is None
        labels, bases = pair or ([], [])
        attach_case_comparison(result, gold_labels=labels, gold_bases=bases)
        body = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        previous = existing.get(seqname) or {}
        if previous.get("trace_sha256") == digest and previous.get("diff_count") is not None:
            outcome = "skipped"
        else:
            store.put_case(result, attempt=int(previous.get("attempt") or result.get("attempt") or 1))
            outcome = "no_gold" if no_gold else "stored"
        with PRINT_LOCK:
            done["n"] += 1
            if done["n"] % 250 == 0 or done["n"] == total:
                log(f"    {done['n']}/{total} cases ({time.time() - started:.0f}s)")
        return outcome

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(handle, case_files))
    else:
        outcomes = [handle(path) for path in case_files]

    for kind in outcomes:
        stats[kind] = stats.get(kind, 0) + 1
    log(f"  cases: stored={stats['stored']} skipped={stats['skipped']} no_gold={stats['no_gold']} "
        f"failed={stats['failed']} in {time.time() - started:.1f}s ({total} files)")
    return stats


def import_attempts(store, run_dir: Path) -> int:
    attempts_dir = run_dir / "case_attempts"
    if not attempts_dir.is_dir():
        return 0
    count = 0
    for seqname_dir in sorted(attempts_dir.iterdir()):
        if not seqname_dir.is_dir():
            continue
        for attempt_path in sorted(seqname_dir.glob("attempt-*.json")):
            payload = read_json(attempt_path)
            if not payload:
                continue
            seqname = str(payload.get("seqname") or "").strip()
            if not seqname:
                continue
            number = int(attempt_path.stem.split("-")[1])
            usage = dict(payload.get("llm_usage") or {})
            store.put_attempt(
                seqname,
                number,
                outcome=str(payload.get("status") or ""),
                error=str(payload.get("error") or ""),
                latency_sec=payload.get("elapsed_sec"),
                usage={
                    key: payload.get(key)
                    for key in ("llm_api_call_count", "llm_prompt_tokens", "llm_completion_tokens", "llm_total_tokens")
                    if key in payload
                }
                | {"call_count": usage.get("call_count")},
                object_key=store.object_key("case_attempts", seqname_dir.name, attempt_path.name),
            )
            store.put_artifact(
                f"case_attempts/{seqname_dir.name}/{attempt_path.name}",
                attempt_path.read_bytes(),
                content_type="application/json",
            )
            count += 1
    log(f"  attempts: {count}")
    return count


def upload_artifacts(store, run_dir: Path) -> Tuple[int, List[str]]:
    """Upload run-level files. A failed object is reported, not fatal."""
    uploaded = 0
    failures: List[str] = []
    for name in ARTIFACT_FILES:
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            store.put_artifact(
                name,
                path.read_bytes(),
                content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
            )
            uploaded += 1
        except BenchmarkStoreError as exc:
            failures.append(f"{name}: {exc}")
            log(f"    artifact failed: {name}")
    for sub in ARTIFACT_DIRS:
        base = run_dir / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(run_dir).as_posix()
            try:
                store.put_artifact(
                    rel,
                    path.read_bytes(),
                    content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                )
                uploaded += 1
            except BenchmarkStoreError as exc:
                failures.append(f"{rel}: {exc}")
                log(f"    artifact failed: {rel}")
    log(f"  artifacts: {uploaded} uploaded, {len(failures)} failed")
    return uploaded, failures


def import_run(run_id: str, *, runs_dir: Path, workers: int, dry_run: bool) -> Dict[str, Any]:
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Run directory not found: {run_dir}.  Runs normally live in MySQL and "
            "MinIO; materialize one first with export_run_from_store.py, or pass "
            "--runs-dir at the directory that holds it."
        )
    manifest = read_json(run_dir / "manifest.json") or {}
    summary = read_json(run_dir / "summary.json") or {}
    counts = dict(summary.get("counts") or manifest.get("counts") or {})
    gold_path = resolve_gold_path(run_dir, manifest)
    gold = load_gold_rows(gold_path)
    log(f"[{run_id}] gold={gold_path} rows={len(gold)} cases={len(list((run_dir / 'cases').glob('*.json')))}")

    if dry_run:
        return {"run_id": run_id, "dry_run": True}

    store = connect_store(run_id)
    gold_artifact = "inputs/gold_reference.tsv"
    if run_dir not in gold_path.parents:
        store.put_artifact(
            gold_artifact,
            gold_path.read_bytes(),
            content_type="text/tab-separated-values",
        )
    store.register_run(
        {
            "state": "running",
            "model": manifest.get("model"),
            "provider": manifest.get("provider"),
            "method_backend": manifest.get("method_backend"),
            "git_commit": manifest.get("git_commit"),
            "git_worktree_dirty": manifest.get("git_worktree_dirty"),
            "input_tsv": gold_artifact if run_dir not in gold_path.parents else str(gold_path),
            "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            "selected_seqnames_sha256": manifest.get("selected_seqnames_sha256"),
            "run_contract_sha256": manifest.get("run_contract_sha256"),
            "attempt": manifest.get("attempt") or 1,
            "target_case_count": manifest.get("target_case_count") or len(gold),
            "completed_case_count": 0,
            "pending_case_count": 0,
            "run_contract": manifest.get("run_contract") or {},
            "command": manifest.get("command"),
            "started_at": manifest.get("started_at") or manifest.get("finished_at"),
        }
    )
    stats = import_cases(store, run_dir, gold, workers)
    import_attempts(store, run_dir)
    _uploaded, failures = upload_artifacts(store, run_dir)

    rows = store.list_rows()
    stored_fields = {key for row in rows for key, value in row.items() if value not in (None, "")}
    store.seal_run(
        summary=summary,
        counts=counts,
        manifest_key=store.object_key("manifest.json"),
        finished_at=manifest.get("finished_at"),
    )
    log(f"[{run_id}] sealed: rows={len(rows)} populated_columns={len(stored_fields)}/{len(INDEX_FIELDNAMES)}")
    return {
        "run_id": run_id,
        "rows": len(rows),
        "stats": stats,
        "columns": sorted(stored_fields),
        "artifact_failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", action="append", required=True, help="Run id under --runs-dir; repeatable.")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory holding on-disk run copies. Defaults to the run cache "
             "(.runtime/runs), then the pre-storage location (benchmark/runs).",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="Resolve inputs and counts without writing.")
    args = parser.parse_args()

    if args.runs_dir is None:
        args.runs_dir = next(
            (
                root
                for root in (default_run_cache_dir(), ROOT / "benchmark" / "runs")
                if root.is_dir() and any(child.is_dir() for child in root.iterdir())
            ),
            default_run_cache_dir(),
        )

    results = []
    for run_id in args.run_id:
        results.append(import_run(run_id, runs_dir=args.runs_dir, workers=max(1, args.workers), dry_run=args.dry_run))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except BenchmarkStoreError as exc:
        print(f"store unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2)
