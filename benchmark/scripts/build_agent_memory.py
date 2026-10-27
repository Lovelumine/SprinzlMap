#!/usr/bin/env python3
"""Build a frozen, answer-free Agent memory snapshot from development runs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "APP"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent.memory.retrieval import (  # noqa: E402
    MEMORY_ACTION_ALLOWLIST,
    PatternRetriever,
)
from backend.agent.memory.store import PatternStore  # noqa: E402
from backend.agent.orchestrator.context_engineering import (  # noqa: E402
    answer_free_context_signature,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/agent_memory/patterns.v2.jsonl"),
    )
    parser.add_argument("--expected-split", default="development")
    parser.add_argument(
        "--exclude-development-fold",
        type=int,
        choices=(1, 2, 3, 4, 5),
        help="Build a fold-local snapshot without cases from this validation fold.",
    )
    parser.add_argument(
        "--allow-unscored",
        action="store_true",
        help="Allow cases without manually reviewed gold. Disabled by default.",
    )
    parser.add_argument("--min-label-accuracy", type=float, default=0.99)
    parser.add_argument("--min-trials", type=int, default=3)
    parser.add_argument("--min-success-rate", type=float, default=0.67)
    return parser.parse_args()


def _context_signature(case: Dict[str, Any]) -> Dict[str, Any]:
    agent = dict(case.get("agent") or {})
    trace = dict(case.get("agent_trace") or {})
    context = dict(trace.get("context_summary") or {})
    input_len = int(case.get("input_len") or 0)
    return answer_free_context_signature({
        "target_length": input_len,
        "difficulty_tier": str(agent.get("difficulty_tier") or ""),
        "route": str(agent.get("route") or ""),
        "numbering_backend": str(context.get("numbering_backend") or case.get("method_backend") or ""),
        "domain_group": str(dict(case.get("benchmark_case") or {}).get("metadata", {}).get("broad_source") or "unknown"),
        "input_type": str(context.get("input_type") or "unknown"),
        "intron_status": str(context.get("intron_status") or "unknown"),
        "cm_evidence_present": bool(context.get("cm_evidence_present")),
        "warning_types": sorted({str(item) for item in list(context.get("warning_types") or []) if str(item)}),
        "failure_regions": sorted({str(item) for item in list(context.get("failure_regions") or []) if str(item)}),
        "validation_reasons": list(context.get("validation_reasons") or []),
    })


def main() -> None:
    args = parse_args()
    cases_dir = args.run_dir / "cases"
    if not cases_dir.is_dir():
        raise SystemExit(f"Missing cases directory: {cases_dir}")

    aggregates: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"successes": 0, "trials": 0, "gain": 0.0, "case_count": 0}
    )
    skipped_split = 0
    skipped_fold = 0
    skipped_unscored = 0
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        benchmark_case = dict(case.get("benchmark_case") or {})
        split = str(
            benchmark_case.get("benchmark_partition")
            or benchmark_case.get("retrospective_split")
            or benchmark_case.get("selected_split")
            or ""
        )
        if split != args.expected_split:
            skipped_split += 1
            continue
        if (
            args.exclude_development_fold is not None
            and int(benchmark_case.get("development_fold") or 0)
            == int(args.exclude_development_fold)
        ):
            skipped_fold += 1
            continue
        if not bool(args.allow_unscored) and not bool(
            benchmark_case.get("gold_evaluation_allowed", True)
        ):
            skipped_unscored += 1
            continue
        metrics = dict(case.get("metrics") or {})
        case_verified = (
            str(case.get("status") or "") == "ok"
            and float(metrics.get("label_accuracy") or 0.0)
            >= float(args.min_label_accuracy)
            and str(dict(case.get("agent") or {}).get("disposition") or "")
            == "accepted"
        )
        case_signature = _context_signature(case)
        actions = list(
            dict(case.get("agent_trace") or {})
            .get("work_state_summary", {})
            .get("action_history", [])
        )
        for action in actions:
            action_type = str(action.get("action_type") or "")
            if action_type not in MEMORY_ACTION_ALLOWLIST:
                continue
            signature = answer_free_context_signature(
                action.get("context_signature") or case_signature
            )
            gain = float(action.get("score_after") or 0.0) - float(action.get("score_before") or 0.0)
            record = {
                "trigger_signature": signature,
                "regions": sorted({str(item) for item in list(action.get("target_regions") or []) if str(item)}),
                "recommended_actions": [
                    {
                        "action_type": action_type,
                        "params": {"focus_regions": list(action.get("target_regions") or [])},
                        "allowed_regions": list(action.get("target_regions") or []),
                    }
                ],
            }
            key = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            aggregates[key]["trials"] += 1
            aggregates[key]["case_count"] += 1
            action_succeeded = (
                case_verified
                and bool(action.get("accepted"))
                and bool(action.get("valid_after"))
                and gain > 0
            )
            if action_succeeded:
                aggregates[key]["successes"] += 1
                aggregates[key]["gain"] += gain

    output_manifest = args.output.with_suffix(args.output.suffix + ".manifest.json")
    args.output.unlink(missing_ok=True)
    output_manifest.unlink(missing_ok=True)
    store = PatternStore(
        str(args.output),
        mode="development_write",
        require_frozen=False,
    )
    retriever = PatternRetriever(store)
    for key, stats in sorted(aggregates.items()):
        if stats["trials"] < max(1, int(args.min_trials)):
            continue
        success_rate = stats["successes"] / stats["trials"]
        if stats["successes"] < 1 or success_rate < float(args.min_success_rate):
            continue
        record = json.loads(key)
        retriever.record_verified_pattern(
            trigger_signature=record["trigger_signature"],
            regions=record["regions"],
            recommended_actions=record["recommended_actions"],
            score_gain=stats["gain"] / stats["successes"],
            dataset_id=args.dataset_id,
            verified_successes=stats["successes"],
            verified_trials=stats["trials"],
            source_case_count=stats["case_count"],
        )
    store.deduplicate()
    manifest = store.freeze(dataset_id=args.dataset_id)
    manifest.update(
        {
            "source_benchmark_partition": args.expected_split,
            "excluded_validation_fold": args.exclude_development_fold,
            "allow_unscored": bool(args.allow_unscored),
            "skipped_non_development_cases": skipped_split,
            "skipped_validation_fold_cases": skipped_fold,
            "skipped_unscored_cases": skipped_unscored,
        }
    )
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "pattern_count": manifest["pattern_count"],
                "memory_sha256": manifest["memory_sha256"],
                "source_split": manifest["source_split"],
                "skipped_non_development_cases": skipped_split,
                "excluded_validation_fold": args.exclude_development_fold,
                "skipped_validation_fold_cases": skipped_fold,
                "skipped_unscored_cases": skipped_unscored,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
