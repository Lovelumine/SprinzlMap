#!/usr/bin/env python3
"""Re-run the flow controls for the 2,530-sequence cohort against one sealed main run.

The main agent run is the reference point; every control must see exactly the
same sequences, the same frozen gold and the same per-case evaluation metadata,
so the cohort is fetched from MinIO and hash-checked against the main run's
manifest before anything starts.

Controls:
  cm-direct            plain cmalign numbering: no Harness, no rules, no tools
  harness-cm-root      Harness rules and guards, root candidate from CM with a
                       retrieval/DP fallback, no planner loop
  harness-retrieval-dp Harness rules and guards, root candidate from template
                       retrieval and regional DP only, no planner loop
  fixed-planner        the main run's toolbox root and planner loop, but with a
                       deterministic action selector instead of the language model

The sealed main run (LLM planner, agent_toolbox root) is the fifth rung and is
not re-run here; every control above must reproduce its cohort exactly.

Full runs go through the retry controller, which writes every finished case to
MySQL and MinIO as it completes and stops immediately if either backend is
unreachable.  Nothing is written under benchmark/.

Usage:
    python benchmark/scripts/run_flow_control_suite.py                # full cohort, stored
    python benchmark/scripts/run_flow_control_suite.py --canary 12    # smoke test, not stored
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkRunStore,
    BenchmarkStoreError,
    connect_store,
)
from backend.domain.benchmarking.case_index import summarize_case_rows  # noqa: E402

MAIN_RUN = "20261020-164533-deepseekv4-flash-guangdong"
RUNTIME_CONFIG = PROJECT_ROOT / ".runtime/benchmark-retry-2040/runtime.recovery.private.json"
CONTROLLER = PROJECT_ROOT / "benchmark/scripts/retry_numbering_technical_failures.py"
RUNNER = PROJECT_ROOT / "benchmark/scripts/run_numbering_benchmark.py"

VARIANTS: Dict[str, Dict[str, Any]] = {
    "cm-direct": {
        "method_backend": "cm",
        "agent_planner_policy": "llm",
        "no_llm": False,
        "root_strategy": "agent_toolbox",
        "workers": 48,
        "purpose": "covariance-model numbering with no Harness",
    },
    "harness-cm-root": {
        "method_backend": "agent",
        # Every Agent worker keeps its own copy of the 169,256-row template
        # table (about 1.2 GB), so a large worker count spends the run loading
        # references and pushing the machine into memory pressure.
        "workers": 16,
        "agent_planner_policy": "fixed",
        "no_llm": True,
        "root_strategy": "cm_first",
        "purpose": "CM root plus retrieval/DP fallback, guarded by the Harness, no planner loop",
    },
    "harness-retrieval-dp": {
        "method_backend": "agent",
        # Every Agent worker keeps its own copy of the 169,256-row template
        # table (about 1.2 GB), so a large worker count spends the run loading
        # references and pushing the machine into memory pressure.
        "workers": 16,
        "agent_planner_policy": "fixed",
        "no_llm": True,
        "root_strategy": "agent_toolbox",
        "purpose": "template retrieval and regional DP root only, guarded by the Harness, no planner loop",
    },
    "fixed-planner": {
        "method_backend": "agent",
        # Every Agent worker keeps its own copy of the 169,256-row template
        # table (about 1.2 GB), so a large worker count spends the run loading
        # references and pushing the machine into memory pressure.
        "workers": 16,
        "agent_planner_policy": "fixed",
        "no_llm": False,
        "root_strategy": "agent_toolbox",
        "purpose": "main-run root and planner loop with a deterministic action selector, no LLM call",
    },
}


def print_status(main_run: str) -> int:
    """Read control progress straight from MySQL; the runs never live on disk."""
    store = BenchmarkRunStore.readonly()
    runs = store.run_rows(like="202610%", limit=100)
    runs = [
        run for run in runs
        if run["run_id"] == main_run or "-control-" in str(run["run_id"])
    ]
    for run in sorted(runs, key=lambda item: str(item["run_id"])):
        # Scores come from re-grading the stored predictions against the current
        # answer table, so this line reports what today's gold says, not what
        # the run was graded against when it was sealed.
        summary = summarize_case_rows(store.case_rows(run_id=str(run["run_id"])))
        counts = summary["counts"]
        metrics = summary["metrics"]
        activity = summary["agent_activity"]
        cases = int(counts["total_cases"])
        residue = (
            f"{100.0 * metrics['residue_label_accuracy']:.2f}%"
            if metrics["residue_label_accuracy"] is not None
            else "-"
        )
        print(
            f"{run['run_id']}  state={run['state']}  cases={cases}/{run['n_targets']}  "
            f"failed={counts['error_cases']}  technical={activity['technical_issue_cases']}  "
            f"accepted={counts['accepted_cases']}  scored={counts['gold_scored_cases']}  "
            f"residue={residue}"
        )
        if counts["gold_scored_cases"]:
            print(
                f"    exact={100.0 * (metrics['exact_match_rate'] or 0.0):.2f}%  "
                f"legality={100.0 * (metrics['legality_pass_rate'] or 0.0):.2f}%  "
                f"planner_llm_calls={activity['planner_llm_call_count']}"
            )
    return 0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-run", default=MAIN_RUN, help="Sealed agent run whose cohort the controls reuse")
    parser.add_argument("--suite-id", default="", help="Label for this campaign (default: today's date)")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="Comma-separated control names")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--fallback-workers", default="48,32")
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--canary", type=int, default=0,
                        help="Run N sequences per control without touching MySQL or MinIO")
    parser.add_argument("--status", action="store_true",
                        help="Print progress and pooled metrics for the controls of a suite and exit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        parser.error(f"unknown controls: {', '.join(unknown)}")
    args.variant_names = names
    return args


def stage_inputs(store, suite_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Path]:
    """Pull the main run's cohort files out of MinIO and verify their hashes."""
    inputs_dir = suite_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        name: (manifest["input_files"][key]["path"], manifest["input_files"][key]["sha256"])
        for name, key in (
            ("reference.tsv", "gold"),
            ("seqnames.txt", "seqnames_file"),
            ("case_manifest.jsonl", "submission_manifest"),
        )
    }
    staged: Dict[str, Path] = {}
    for name, (source_path, sha256) in expected.items():
        body = store.get_object(f"{store.object_prefix.rstrip('/')}/inputs/{name}")
        if body is None:
            raise BenchmarkStoreError(f"main run {store.run_id} has no inputs/{name} in MinIO")
        target = inputs_dir / name
        if not target.exists() or digest(target) != sha256:
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(body)
            os.replace(temporary, target)
        if digest(target) != sha256:
            raise ValueError(f"inputs/{name} does not match the hash recorded in the main-run manifest")
        staged[name] = target
        print(f"verified inputs/{name} sha256={sha256[:16]}… bytes={len(body)}", flush=True)
    roster = store.get_object(f"{store.object_prefix.rstrip('/')}/inputs/cohort_membership.tsv")
    if roster is not None:
        (inputs_dir / "cohort_membership.tsv").write_bytes(roster)
        staged["cohort_membership.tsv"] = inputs_dir / "cohort_membership.tsv"
    return staged


def run_controller(variant: str, config: Dict[str, Any], run_id: str, staged: Dict[str, Path],
                   args: argparse.Namespace, suite_dir: Path) -> int:
    command = [
        sys.executable, str(CONTROLLER),
        "--input-tsv", str(staged["reference.tsv"]),
        "--seqnames-file", str(staged["seqnames.txt"]),
        "--submission-manifest", str(staged["case_manifest.jsonl"]),
        "--runtime-config", str(RUNTIME_CONFIG),
        "--source-run", args.main_run,
        "--run-id", run_id,
        "--expected-cases", str(len(staged["seqnames.txt"].read_text().splitlines())),
        "--full-rerun",
        "--variant", variant,
        "--method-backend", config["method_backend"],
        "--agent-planner-policy", config["agent_planner_policy"],
        "--agent-canonical-root-strategy", config["root_strategy"],
        "--workers", str(min(args.workers, int(config["workers"]))),
        "--fallback-workers", args.fallback_workers,
        "--max-passes", str(args.max_passes),
    ]
    if config["no_llm"]:
        command.append("--no-llm")
    log_path = suite_dir / f"{run_id}.log"
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} launching {variant} -> {run_id}", flush=True)
    with log_path.open("w", encoding="utf-8") as handle:
        return subprocess.call(command, cwd=PROJECT_ROOT, stdout=handle, stderr=subprocess.STDOUT)


def run_canary(variant: str, config: Dict[str, Any], names: List[str], staged: Dict[str, Path],
               args: argparse.Namespace, suite_dir: Path) -> int:
    canary_seqnames = suite_dir / f"canary-{variant}-seqnames.txt"
    canary_seqnames.write_text("\n".join(names) + "\n", encoding="utf-8")
    out_dir = suite_dir / f"canary-{variant}"
    command = [
        sys.executable, str(RUNNER),
        "--input-tsv", str(staged["reference.tsv"]),
        "--seqnames-file", str(canary_seqnames),
        "--submission-manifest", str(staged["case_manifest.jsonl"]),
        "--runtime-config", str(RUNTIME_CONFIG),
        "--run-id", out_dir.name,
        "--out-dir", str(out_dir),
        "--no-store", "--keep-local", "--progress-every", "1",
        "--method-backend", config["method_backend"],
        "--agent-planner-policy", config["agent_planner_policy"],
        "--agent-canonical-root-strategy", config["root_strategy"],
        "--max-workers", "4",
    ]
    if config["no_llm"]:
        command.append("--no-llm")
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} canary {variant}: {len(names)} sequences", flush=True)
    if subprocess.call(command, cwd=PROJECT_ROOT) != 0:
        return 1
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    counts = dict(summary.get("counts") or {})
    metrics = dict(summary.get("metrics") or {})
    llm_calls = 0
    llm_tokens = 0
    for case_path in sorted((out_dir / "cases").glob("*.json")):
        usage = json.loads(case_path.read_text(encoding="utf-8")).get("llm_usage") or {}
        llm_calls += int(usage.get("call_count") or 0)
        llm_tokens += int(((usage.get("tokens") or {}).get("total_tokens") or 0))
    report = {
        "variant": variant,
        "cases": counts.get("total_cases"),
        "ok": counts.get("ok_cases"),
        "errors": counts.get("error_cases"),
        "accepted": counts.get("accepted_cases"),
        "dispositions": counts.get("dispositions"),
        "residue_label_accuracy": metrics.get("residue_label_accuracy"),
        "exact_match_rate": metrics.get("exact_match_rate"),
        "legality_pass_rate": metrics.get("legality_pass_rate"),
        "llm_call_count": llm_calls,
        "llm_total_tokens": llm_tokens,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    (suite_dir / f"canary-{variant}-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    args = parse_args()
    if args.status:
        return print_status(args.main_run)
    if not RUNTIME_CONFIG.is_file():
        print(f"missing private runtime config: {RUNTIME_CONFIG}", file=sys.stderr)
        return 2
    suite_id = args.suite_id or datetime.now().astimezone().strftime("%Y%m%d")
    suite_dir = PROJECT_ROOT / ".runtime" / f"flow-controls-{suite_id}"
    suite_dir.mkdir(parents=True, exist_ok=True)

    try:
        store = connect_store(args.main_run, create_schema=False)
        manifest_body = store.get_artifact("manifest.json")
    except BenchmarkStoreError as exc:
        print(f"store unavailable for {args.main_run}: {exc}", file=sys.stderr)
        return 2
    if manifest_body is None:
        print(f"no manifest.json recorded for {args.main_run}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_body.decode("utf-8"))
    if int(manifest["counts"]["total_cases"]) < 100 and not args.canary:
        raise ValueError("the main run is not a full cohort")

    try:
        staged = stage_inputs(store, suite_dir, manifest)
    except (BenchmarkStoreError, ValueError) as exc:
        print(f"cohort staging failed: {exc}", file=sys.stderr)
        return 2

    names = [line.strip() for line in staged["seqnames.txt"].read_text().splitlines() if line.strip()]
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    campaign = {
        "suite_id": suite_id,
        "main_run": args.main_run,
        "main_run_manifest_sha256": hashlib.sha256(manifest_body).hexdigest(),
        "cohort_size": len(names),
        "cohort_sha256": digest(staged["seqnames.txt"]),
        "gold_sha256": digest(staged["reference.tsv"]),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "canary_cases": args.canary,
        "controls": {},
    }
    for variant in args.variant_names:
        config = VARIANTS[variant]
        run_id = f"{stamp}-control-{variant}"
        campaign["controls"][variant] = {
            "run_id": run_id if not args.canary else None,
            "method_backend": config["method_backend"],
            "agent_planner_policy": config["agent_planner_policy"],
            "root_strategy": config["root_strategy"],
            "no_llm": config["no_llm"],
            "workers": min(args.workers, int(config["workers"])),
            "purpose": config["purpose"],
        }
    (suite_dir / "campaign.json").write_text(
        json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.dry_run:
        print(json.dumps(campaign, ensure_ascii=False, indent=2))
        return 0

    failures = []
    for variant in args.variant_names:
        config = VARIANTS[variant]
        if args.canary:
            code = run_canary(variant, config, names[: args.canary], staged, args, suite_dir)
        else:
            code = run_controller(variant, config, campaign["controls"][variant]["run_id"], staged, args, suite_dir)
        campaign["controls"][variant]["exit_code"] = code
        (suite_dir / "campaign.json").write_text(
            json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if code != 0:
            failures.append(variant)
            print(f"{variant} failed with exit {code}; stopping the suite", file=sys.stderr, flush=True)
            break
    campaign["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    campaign["failed_controls"] = failures
    (suite_dir / "campaign.json").write_text(
        json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
