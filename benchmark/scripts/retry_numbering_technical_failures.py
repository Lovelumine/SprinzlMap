#!/usr/bin/env python3
"""Run fresh evaluations or retry subsets with strict failure accounting."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "APP") not in sys.path:
    sys.path.insert(0, str(ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    BenchmarkRunStore,
    connect_store,
    default_run_cache_dir,
    load_case_jsons,
)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_bytes(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-tsv", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--expected-cases", type=int, required=True)
    parser.add_argument("--seqnames-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--full-rerun", action="store_true",
                        help="Rerun the selected cohort on new code using a fresh MySQL reference snapshot.")
    parser.add_argument("--submission-manifest", type=Path,
                        help="Optional per-case evaluation metadata, including manual exclusions.")
    # Pipeline variant.  A technical retry reuses the source run's configuration;
    # a flow control (fixed planner, no-Agent, direct CM) overrides it, and the
    # override is frozen into retry_lineage.json so the run can never drift.
    parser.add_argument("--variant", default="",
                        help="Short control label recorded in the lineage and run notes")
    parser.add_argument("--method-backend", choices=("agent", "cm"), default="agent")
    parser.add_argument("--agent-planner-policy", choices=("llm", "fixed"), default="llm")
    parser.add_argument("--no-llm", action="store_true",
                        help="Deterministic root candidate only: no planner loop")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseekv4-flash-guangdong")
    parser.add_argument("--cm-model-root", default="research/third_party/trnaviz/engine")
    parser.add_argument("--agent-canonical-root-strategy", default="agent_toolbox",
                        choices=("agent_toolbox", "cm_first", "cm_only", "legacy_only"),
                        help="Initial tool portfolio that builds the root candidate")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--fallback-workers", default="6,4")
    parser.add_argument("--max-passes", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=30)
    args = parser.parse_args()
    use_llm = args.method_backend == "agent" and not args.no_llm
    if (Path(args.run_id).name != args.run_id or args.run_id == args.source_run
            or Path(args.source_run).name != args.source_run):
        parser.error("Use a new run ID without path separators")
    schedule = [args.workers] + [int(x) for x in args.fallback_workers.split(",") if x]
    if min(schedule) < 1 or args.max_passes < 1 or args.expected_cases < 1 or args.retry_delay < 0:
        parser.error("Workers/passes must be positive; retry delay cannot be negative")
    args.input_tsv = args.input_tsv.resolve()
    args.runtime_config = args.runtime_config.resolve()
    args.seqnames_file = args.seqnames_file.resolve()
    # The source run lives in MySQL and MinIO, so these checks read it from the
    # store rather than expecting benchmark/runs/<id>/ on disk.
    try:
        source_store = connect_store(args.source_run, create_schema=False)
    except BenchmarkStoreError as exc:
        print(f"source run store unavailable: {exc}", file=sys.stderr)
        return 2
    manifest_body = source_store.get_artifact("manifest.json")
    if manifest_body is None:
        raise ValueError(f"No manifest.json recorded for run {args.source_run}")
    source_manifest = json.loads(manifest_body.decode("utf-8"))
    if not args.full_rerun and digest(args.input_tsv) != source_manifest["input_files"]["gold"]["sha256"]:
        raise ValueError("Input differs from the frozen source-run input")
    names = args.seqnames_file.read_text().splitlines()
    if len(names) != args.expected_cases or len(set(names)) != args.expected_cases:
        raise ValueError(f"Retry selection must contain exactly {args.expected_cases} distinct sequences")
    target_names = set(names)
    source_names = {
        str(row.get("seqname") or "")
        for row in source_store.case_rows(active_answer=False, columns=["status"])
    }
    if not target_names <= source_names:
        raise ValueError("Retry selection contains sequences absent from the source run")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_numbering_benchmark import load_gold_cases
    if args.full_rerun:
        selected_cases = load_gold_cases(args.input_tsv, seqname_filter=target_names)
        if len(selected_cases) != len(target_names) or {case.seqname for case in selected_cases} != target_names:
            raise ValueError("Fresh reference snapshot must contain every selected sequence exactly once")
        originals = load_case_jsons(args.source_run, target_names)
        for case in selected_cases:
            original = originals.get(case.seqname)
            if original is None:
                raise ValueError(f"Stored trace is missing for {case.seqname}")
            if case.input_seq != original.get("input_seq"):
                raise ValueError(f"Input sequence changed relative to the selected cohort: {case.seqname}")
    if args.submission_manifest:
        args.submission_manifest = args.submission_manifest.resolve()

    out = default_run_cache_dir() / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / "retry.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("A retry controller is already running for this run") from None
    lineage = {
        "source_run": args.source_run,
        "source_manifest_sha256": digest_bytes(manifest_body),
        "input_sha256": digest(args.input_tsv),
        "selection_sha256": digest(args.seqnames_file),
        "target_case_count": len(names),
        "variant": args.variant or "agent-llm",
        "method_backend": args.method_backend,
        "agent_planner_policy": args.agent_planner_policy,
        "agent_canonical_root_strategy": args.agent_canonical_root_strategy,
        "use_llm": use_llm,
        "provider": args.provider,
        "model": args.model,
        "reason": "Full cohort rerun after code fixes; latest MySQL reference snapshot" if args.full_rerun else "Audited failed requests or deterministic planner fallbacks",
        "failure_policy": (
            "Any failed LLM request/decision invalidates its case attempt; "
            "earlier attempts stay in the execution table as attempt records"
        ),
    }
    if args.full_rerun:
        lineage["evaluation_mode"] = "full_rerun"
    if args.submission_manifest:
        lineage["submission_manifest_sha256"] = digest(args.submission_manifest)
    lineage_path = out / "retry_lineage.json"
    if lineage_path.exists() and read_json(lineage_path) != lineage:
        raise ValueError("Retry lineage differs from the existing run")
    write_json(lineage_path, lineage)
    state_path = out / "retry_status.json"
    previous = read_json(state_path) if state_path.exists() else {}
    start_pass = int(previous.get("pass", 1)) + int(previous.get("status") in {"retry_wait", "exhausted"})
    ever_failed = set(previous.get("encountered_failure_seqnames", []))
    started_at = previous.get("started_at") or now()
    # The runner writes one index row per finished case, so progress is read
    # from MySQL; there is no longer a cases/ directory to poll.
    progress_store = BenchmarkRunStore.readonly()
    auth_error_re = re.compile(r"HTTP (401|402|403)")

    def snapshot(pass_number, workers, status, runner_pid=None):
        # Progress needs run-time facts only, so the rows are read ungraded and
        # without hauling every stored prediction across the network.
        rows = progress_store.case_rows(
            run_id=args.run_id,
            active_answer=False,
            columns=["status", "error", "technical_issue_count"],
        )
        rows = [row for row in rows if str(row.get("seqname") or "") in target_names]
        failures = [
            row for row in rows
            if str(row.get("status") or "") != "ok" or int(row.get("technical_issue_count") or 0) > 0
        ]
        ever_failed.update(row["seqname"] for row in failures)
        rejected = Counter(
            match.group(1)
            for row in rows
            for match in [auth_error_re.search(str(row.get("error") or ""))]
            if match
        )
        state = {
            "run_id": args.run_id, "controller_pid": os.getpid(), "runner_pid": runner_pid,
            "status": status, "started_at": started_at, "updated_at": now(),
            "pass": pass_number, "workers": workers, "max_passes": args.max_passes,
            "target": len(names), "written": len(rows), "ok": len(rows) - len(failures),
            "errors": len(failures), "missing": len(names) - len(rows),
            "credential_rejections": dict(rejected),
            "failure_examples": [
                {"seqname": str(row.get("seqname") or ""), "status": str(row.get("status") or ""),
                 "technical_issue_count": int(row.get("technical_issue_count") or 0),
                 "error": str(row.get("error") or "")[:200]}
                for row in failures[:5]
            ],
            "encountered_failure_seqnames": sorted(ever_failed),
            "encountered_failure_cases": len(ever_failed),
        }
        write_json(state_path, state)
        return state

    child = None
    pass_number, workers = start_pass, schedule[min(start_pass - 1, len(schedule) - 1)]
    try:
        for pass_number in range(start_pass, args.max_passes + 1):
            workers = schedule[min(pass_number - 1, len(schedule) - 1)]
            command = [
                sys.executable, str(ROOT / "benchmark/scripts/run_numbering_benchmark.py"),
                "--input-tsv", str(args.input_tsv), "--seqnames-file", str(args.seqnames_file),
                "--runtime-config", str(args.runtime_config), "--run-id", args.run_id,
                "--method-backend", args.method_backend, "--provider", args.provider,
                "--model", args.model, "--agent-planner-policy", args.agent_planner_policy,
                "--cm-model-root", args.cm_model_root,
                "--agent-canonical-root-strategy", args.agent_canonical_root_strategy,
                "--agent-llm-triage-mode", "dual_trigger",
                "--strict-llm-failures", "--max-workers", str(workers),
                "--llm-global-max-workers", str(workers), "--llm-max-inflight", str(workers),
                "--resume", "--rerun-failed", "--progress-every", "10",
                "--notes",
                (
                    f"{'Full cohort rerun following' if args.full_rerun else 'Technical retry subset of'}"
                    f" {args.source_run}; variant={args.variant or 'agent-llm'};"
                    f" backend={args.method_backend}; planner={args.agent_planner_policy};"
                    f" root={args.agent_canonical_root_strategy};"
                    f" use_llm={int(use_llm)}; streaming; strict failure accounting; see retry_lineage.json"
                ),
            ]
            if args.no_llm:
                command.append("--no-llm")
            if args.submission_manifest:
                command.extend(["--submission-manifest", str(args.submission_manifest)])
            print(f"{now()} retry pass={pass_number} workers={workers}", flush=True)
            child = subprocess.Popen(command, cwd=ROOT)
            while child.poll() is None:
                state = snapshot(pass_number, workers, "running", child.pid)
                rejected = sum(state["credential_rejections"].values())
                if rejected >= 2 and state["ok"] == 0:
                    child.terminate()
                    child.wait(timeout=30)
                    snapshot(pass_number, workers, "credential_error")
                    print("Gateway rejected credentials; stopped pending work.", flush=True)
                    return 2
                time.sleep(5)
            state = snapshot(pass_number, workers, "pass_finished")
            print(f"{now()} pass_finished exit={child.returncode} ok={state['ok']} errors={state['errors']} missing={state['missing']}", flush=True)
            if child.returncode != 0:
                snapshot(pass_number, workers, "runner_error")
                return 1
            if state["ok"] == len(names):
                snapshot(pass_number, workers, "completed")
                return 0
            if pass_number < args.max_passes:
                snapshot(pass_number, workers, "retry_wait")
                time.sleep(args.retry_delay)
        snapshot(pass_number, workers, "exhausted")
        print("Passes exhausted; unresolved cases remain failures. Increase --max-passes to continue.", flush=True)
        return 1
    except BaseException:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        snapshot(pass_number, workers, "interrupted")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
