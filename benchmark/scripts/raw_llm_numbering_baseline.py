#!/usr/bin/env python3
"""Raw-LLM control: ask the model to emit Sprinzl numbering straight from sequence.

This is the "no harness, no tools, no retrieval" control for SprinzlMap. The model
receives only the RNA sequence and must return one Sprinzl label per residue.

The answer is turned into the same three-row coordinate table the main pipeline
produces, then scored by the pipeline's own ``evaluate_case``: identical label
comparison, identical hard-guard legality check, identical summary function.  An
answer that cannot be read as one label per residue scores zero rather than being
dropped, because a method that cannot return a usable answer has failed.

A transport failure (timeout, HTTP error, empty completion) is a technical
failure: it is recorded as an attempt row in the execution table and retried, and
the case is only stored with ``status=error`` once every pass has been used up.
Results go to MySQL and MinIO case by case, so the run is never tied to this
machine.

Usage:
    python benchmark/scripts/raw_llm_numbering_baseline.py \
        --run-id 20261023-control-raw-llm \
        --seqnames-file .runtime/flow-controls-20261023/inputs/seqnames.txt \
        --input-tsv .runtime/flow-controls-20261023/inputs/reference.tsv \
        --submission-manifest .runtime/flow-controls-20261023/inputs/case_manifest.jsonl
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "APP") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "APP"))
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backend.infra.llm.json_client import (  # noqa: E402
    JsonCompletionError,
    StructuredJsonLLMClient,
)
from backend.infra.llm.usage_tracker import get_usage_summary  # noqa: E402
from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    case_digest,
    default_run_cache_dir,
    connect_store,
    load_case_jsons,
)
from backend.domain.benchmarking.case_index import attach_case_comparison  # noqa: E402

# Messages that describe the transport, not the model's answer.  The user's rule
# for this project is explicit: a timeout or an empty completion is a technical
# failure, so it is retried and only counted as a failure once the retries run out.
TECHNICAL_MESSAGES = (
    "LLM request failed",
    "LLM returned HTTP",
    "hard time budget",
    "did not return a response",
    "did not contain message content",
)

MAIN_RUN = "20261020-164533-deepseekv4-flash-guangdong"
RUNTIME_CONFIG = PROJECT_ROOT / ".runtime/benchmark-retry-2040/runtime.recovery.private.json"

PROMPT_TEMPLATE = """你是 tRNA 结构专家。下面是一条成熟 tRNA 序列（RNA，5'→3'）：

{sequence}

请给出它在 Sprinzl 编号体系下的逐残基编号：为序列中的每一个残基指定其对应的 Sprinzl 位置。
要求：
- 输出 JSON，格式为 {{"labels": ["...", ...]}}，labels 长度必须等于序列长度，第 i 个元素是第 i 个残基的 Sprinzl 编号
- 标准位置用数字字符串（如 "1","2",...,"76"），插入位用 "20a" 形式，可变区用 "V1".."V21" 形式
- 不要输出解释，只输出 JSON"""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seqnames-file", type=Path, required=True)
    parser.add_argument("--input-tsv", type=Path, required=True, help="Frozen gold used by the main run.")
    parser.add_argument("--submission-manifest", type=Path, default=None,
                        help="Per-case evaluation metadata, including manual exclusions.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--main-run", default=MAIN_RUN,
                        help="Stored run recorded as the cohort source in the lineage")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="With --keep-local: where the dev copy is written")
    parser.add_argument("--runtime-config", type=Path, default=RUNTIME_CONFIG,
                        help="Private JSON with the gateway base URL and key")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--thinking",
        choices=("inherit", "enabled", "disabled"),
        default="inherit",
        help="Reasoning mode for the control. 'inherit' keeps the runtime config, "
             "which is what the main run used; 'enabled' with a small --max-tokens "
             "starves the answer, because reasoning consumes the output budget.",
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--hard-budget", type=float, default=600.0,
                        help="Total wall-clock budget for one completion, including retries")
    parser.add_argument("--retries", type=int, default=2, help="Extra attempts per case inside one pass")
    parser.add_argument("--max-passes", type=int, default=3, help="Whole-pass retries for technical failures")
    parser.add_argument("--retry-delay", type=float, default=20.0)
    parser.add_argument("--no-store", action="store_true", help="Dev only: skip MySQL and MinIO")
    parser.add_argument("--keep-local", action="store_true", help="Also write the run under --out-dir")
    return parser.parse_args()


def load_runtime_values(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def extract_labels(content: str) -> Tuple[List[str], str]:
    """Return (labels, failure_reason). Empty labels with a reason means failure."""
    if not content.strip():
        return [], "empty_content"
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return [], "no_json_object"
    try:
        obj = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return [], "invalid_json"
    if not isinstance(obj, dict):
        return [], "not_an_object"
    labels = obj.get("labels")
    if not isinstance(labels, list):
        return [], "missing_labels"
    return [str(x).strip() for x in labels], ""


def coordinate_table(labels: List[str], sequence: str, usable: bool) -> str:
    """One column per input residue, in the pipeline's three-row coordinate format."""
    if usable:
        numbering = list(labels)
    else:
        numbering = ["-"] * len(sequence)
    template = ["-"] * len(sequence)
    target = list(sequence)
    return "\n".join(
        ",".join(row) for row in (numbering, template, target)
    ) + "\n"


def main() -> int:
    args = parse_args()
    if args.no_store and not args.keep_local:
        raise SystemExit("--no-store requires --keep-local; a published control run must be stored")
    runtime = load_runtime_values(args.runtime_config)
    if not runtime:
        raise SystemExit(f"no runtime config at {args.runtime_config}")
    model = args.model or runtime.get("DEEPSEEK_MODEL") or "deepseekv4-flash-guangdong"
    overrides = dict(runtime)
    overrides.update({
        "LLM_PROVIDER": "deepseek",
        "LLM_RETRIES": str(args.retries),
        "LLM_READ_TIMEOUT": str(int(args.timeout)),
        "LLM_HARD_BUDGET": str(int(args.hard_budget)),
        "LLM_STREAM": "true",
    })
    if args.thinking != "inherit":
        overrides["DEEPSEEK_THINKING"] = args.thinking

    from run_numbering_benchmark import (
        evaluate_case,
        load_gold_cases,
        load_submission_case_metadata,
        summarize_results,
    )

    seqnames = [line.strip() for line in args.seqnames_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        seqnames = seqnames[: args.limit]
    # The cohort manifest carries the per-sequence curation flags (including the
    # sequences a reviewer excluded from scoring).  Reading it with the same loader
    # as the main runner keeps the control on the identical cohort.
    case_metadata = (
        load_submission_case_metadata(args.submission_manifest) if args.submission_manifest else {}
    )
    cases = load_gold_cases(
        args.input_tsv,
        seqname_filter=set(seqnames),
        case_metadata=case_metadata,
    )
    if not cases:
        raise SystemExit("no benchmark cases selected")

    store = None
    if not args.no_store:
        try:
            store = connect_store(args.run_id)
        except BenchmarkStoreError as exc:
            print(f"store unavailable: {exc}", file=sys.stderr)
            return 2

    if store is not None:
        store.register_run({
            "state": "running",
            "model": model,
            "provider": "deepseek",
            "method_backend": "raw_llm",
            "git_commit": _git_commit(),
            "input_tsv": str(args.input_tsv),
            "gold_sha256": _sha256(args.input_tsv),
            "selected_seqnames_sha256": _sha256(args.seqnames_file),
            "attempt": 1,
            "target_case_count": len(cases),
            "n_completed": 0,
            "n_pending": len(cases),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "command": " ".join(sys.argv),
            "run_contract": {
                "control": "raw-llm-numbering",
                "cohort_source_run": args.main_run,
                "max_tokens": args.max_tokens,
                "hard_budget_s": args.hard_budget,
                "read_timeout_s": args.timeout,
                "thinking": runtime.get("DEEPSEEK_THINKING", ""),
            },
        })

    out_dir: Optional[Path] = None
    if args.keep_local:
        out_dir = args.out_dir or (PROJECT_ROOT / ".runtime" / "runs" / args.run_id)
        out_dir.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Dict[str, Any]] = {}
    if store is not None:
        existing = load_case_jsons(args.run_id, [case.seqname for case in cases])
        existing = {
            name: payload for name, payload in existing.items()
            if str(payload.get("status") or "") == "ok"
        }
        print(f"resumed_from_store={len(existing)} pending={len(cases) - len(existing)}", flush=True)

    client = StructuredJsonLLMClient()
    lock = threading.Lock()
    attempts_seen: Dict[str, int] = {}

    def build_result(case, payload: Dict[str, Any], elapsed_sec: float) -> Dict[str, Any]:
        sequence = case.input_seq
        raw_labels = payload.get("labels")
        if not isinstance(raw_labels, list):
            labels, failure = [], "missing_labels"
        else:
            labels = [str(item).strip() for item in raw_labels]
            if not labels:
                failure = "empty_labels"
            elif len(labels) != len(sequence):
                failure = "length_mismatch"
            else:
                failure = ""
        usable = not failure
        csv_content = coordinate_table(labels, sequence, usable)
        result = evaluate_case(
            case,
            template_name="raw_llm",
            csv_content=csv_content,
            warnings=[],
            meta={},
            use_llm=True,
            llm_reasoning=False,
            runtime_overrides=overrides,
            elapsed_sec=elapsed_sec,
        )
        result["method_backend"] = "raw_llm"
        result["agent_planner_policy"] = ""
        result["agent_llm_postcheck_enabled"] = False
        result["agent_deterministic_boundary_refinement_enabled"] = False
        result["use_llm"] = True
        result["template_name"] = "raw_llm"
        result["csv_content"] = csv_content
        result["raw_llm"] = {
            "usable": usable,
            "failure": failure,
            "pred_len": len(labels),
            "model": model,
        }
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        result["started_at"] = stamp
        result["finished_at"] = stamp
        if case.benchmark_meta.get("gold_evaluation_allowed", True):
            attach_case_comparison(
                result, gold_labels=list(case.gold_labels), gold_bases=list(case.gold_bases)
            )
        return result

    def run(case) -> Dict[str, Any]:
        usage_case_id = case_digest(case.seqname)
        case_overrides = dict(overrides)
        case_overrides.update({
            "LLM_USAGE_RUN_ID": str(args.run_id),
            "LLM_USAGE_CASE_ID": usage_case_id,
            "LLM_USAGE_SEQNAME": case.seqname,
        })
        started = time.monotonic()
        error_text = ""
        completion = None
        try:
            completion = client.complete(
                messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(sequence=case.input_seq)}],
                runtime_values_override=case_overrides,
                source="raw_llm_baseline",
                model_name=model,
                max_tokens=args.max_tokens,
            )
        except JsonCompletionError as exc:
            error_text = str(exc)
            if store is not None:
                with lock:
                    attempts_seen[case.seqname] = attempts_seen.get(case.seqname, 0) + 1
                    attempt_no = attempts_seen[case.seqname]
                store.put_attempt(
                    case.seqname, attempt_no,
                    outcome="technical_error" if _is_technical(error_text) else "unusable_output",
                    error=error_text[:1000],
                    latency_sec=time.monotonic() - started,
                )
        if completion is None:
            if not _is_technical(error_text):
                # The model answered, but the answer is not a usable coordinate list.
                empty_payload: Dict[str, Any] = {}
                result = build_result(case, empty_payload, time.monotonic() - started)
                result["raw_llm"]["error"] = error_text
                if store is not None:
                    with lock:
                        attempts_seen[case.seqname] = attempts_seen.get(case.seqname, 0) + 1
                        attempt_no = attempts_seen[case.seqname]
                    store.put_case(result, attempt=attempt_no)
                return result
            return {
                "status": "error", "seqname": case.seqname,
                "input_len": len(case.input_seq), "input_seq": case.input_seq,
                "use_llm": True, "benchmark_case": dict(case.benchmark_meta or {}),
                "method_backend": "raw_llm", "provider": "deepseek", "model": model,
                "elapsed_sec": round(time.monotonic() - started, 4),
                "error": error_text[:1000],
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        result = build_result(case, completion.payload, completion.elapsed_sec)
        result["llm_usage"] = get_usage_summary(args.run_id, usage_case_id, include_calls=True)
        if store is not None:
            with lock:
                attempts_seen[case.seqname] = attempts_seen.get(case.seqname, 0) + 1
                attempt_no = attempts_seen[case.seqname]
            store.put_case(result, attempt=attempt_no)
        return result

    results: List[Dict[str, Any]] = list(existing.values())
    pending = [case for case in cases if case.seqname not in existing]
    print(f"model={model} workers={args.workers} cases={len(pending)}", flush=True)

    error_rows: List[Dict[str, Any]] = []
    for current_pass in range(1, args.max_passes + 1):
        if not pending:
            break
        if current_pass > 1:
            print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
                  f"pass={current_pass} technical_retry_cases={len(pending)}", flush=True)
            time.sleep(args.retry_delay)
        new_errors: List[Dict[str, Any]] = []
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(run, case): case for case in pending}
            for future in as_completed(futures):
                record = future.result()
                done += 1
                if str(record.get("status") or "") != "ok":
                    new_errors.append(record)
                else:
                    results.append(record)
                if done % 25 == 0 or done == len(pending):
                    print(f"[pass {current_pass}] {done}/{len(pending)} done, "
                          f"technical_errors={len(new_errors)}", flush=True)
                    if store is not None:
                        store.checkpoint(
                            completed=len(results),
                            pending=max(0, len(cases) - len(results)),
                        )
        error_rows = new_errors
        failed_names = {str(row["seqname"]) for row in error_rows}
        pending = [case for case in cases if case.seqname in failed_names]

    for row in error_rows:
        # Every pass failed technically: keep the case visible as a failure.
        if store is not None:
            store.put_case(row, attempt=attempts_seen.get(str(row["seqname"]), 1))
    if error_rows:
        print(f"unresolved_technical_failures={len(error_rows)}: "
              f"{[row['seqname'] for row in error_rows][:10]}", file=sys.stderr)

    rows = [row for row in results if str(row.get("status") or "") == "ok"]
    summary = summarize_results(rows)
    summary["run_id"] = args.run_id
    summary["method_backend"] = "raw_llm"
    summary["provider"] = "deepseek"
    summary["model"] = model
    summary["input_tsv"] = str(args.input_tsv)
    summary["cohort_source_run"] = args.main_run
    summary["unresolved_technical_failures"] = len(error_rows)
    usable_rows = [row for row in rows if (row.get("raw_llm") or {}).get("usable")]
    summary["raw_llm"] = {
        "usable_cases": len(usable_rows),
        "unusable_cases": len(rows) - len(usable_rows),
        "failure_counts": _failure_counts(rows),
        "usable_residue_label_accuracy": _ratio(usable_rows),
        "usable_exact_match_rate": (
            sum(1 for row in usable_rows if (row.get("metrics") or {}).get("exact_match")) / len(usable_rows)
            if usable_rows else None
        ),
    }

    lineage = {
        "run_id": args.run_id,
        "cohort_source_run": args.main_run,
        "control": "raw-llm-numbering",
        "model": model,
        "max_tokens": args.max_tokens,
        "cases": len(rows),
        "requested_cases": len(cases),
        "unresolved_technical_failures": len(error_rows),
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if store is not None:
        store.put_artifact("summary.json", summary)
        store.put_artifact("control_lineage.json", lineage)
        store.seal_run(summary=summary, counts=dict(summary.get("counts") or {}),
                       manifest_key=store.object_key("summary.json"),
                       finished_at=lineage["recorded_at"])
        store.write_pointer(default_run_cache_dir() / args.run_id / "RUN.json")
    if out_dir is not None:
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        (out_dir / "control_lineage.json").write_text(json.dumps(lineage, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8")
        _write_case_tsv(out_dir / "case_results.tsv", rows)
    print(json.dumps({"counts": summary.get("counts"), "metrics": summary.get("metrics"),
                      "raw_llm": summary["raw_llm"]}, ensure_ascii=False, indent=2))
    return 1 if error_rows else 0


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return ""


def _is_technical(message: str) -> bool:
    return any(marker in message for marker in TECHNICAL_MESSAGES)


def _failure_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        failure = str((row.get("raw_llm") or {}).get("failure") or "")
        if failure:
            counts[failure] = counts.get(failure, 0) + 1
    return counts


def _ratio(rows: List[Dict[str, Any]]) -> Optional[float]:
    correct = sum(int((row.get("metrics") or {}).get("label_correct") or 0) for row in rows)
    total = sum(int((row.get("metrics") or {}).get("label_total") or 0) for row in rows)
    return (correct / total) if total else None


def _write_case_tsv(path: Path, rows: List[Dict[str, Any]]) -> None:
    from backend.domain.benchmarking.case_index import INDEX_FIELDNAMES, flatten_case_result

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDNAMES, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten_case_result(row))


if __name__ == "__main__":
    raise SystemExit(main())
