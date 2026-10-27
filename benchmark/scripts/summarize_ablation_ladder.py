#!/usr/bin/env python3
"""Collect the ablation ladder for one cohort straight from MySQL and MinIO.

Every rung of the ladder is a stored benchmark run: the covariance-model
alignment, the deterministic Harness controls, the full SprinzlMap planner and
the raw-language-model control.  The script reads the run metadata and the
frozen run contract from the execution table, then recomputes every number from
the stored predictions against the current answer table, so an answer corrected
after a run finished moves the rungs without replaying the model.  It refuses to
put two runs in the same table unless they share the frozen gold hash, the
selected-sequence hash and the cohort size.  That check is the reason a reported
delta can always be read as a paired comparison on identical questions.

    python benchmark/scripts/summarize_ablation_ladder.py \
        --runs 20261023-041911-control-cm-direct,20261023-...-control-harness-retrieval-dp,
               20261020-164533-deepseekv4-flash-guangdong \
        --labels "CM 直接比对,检索+DP,智能体规划"

Writes ``ablation_ladder.tsv`` and ``ablation_ladder_provenance.json`` under
``research/paper/results``; the runs themselves stay in the store.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkRunStore,
    BenchmarkStoreError,
)
from backend.domain.benchmarking.case_index import summarize_case_rows  # noqa: E402

OUT_DIR = PROJECT_ROOT / "research/paper/results"

# ladder order used by the figure; labels are the short paper-facing names
DEFAULT_ORDER = [
    "raw-llm",
    "cm",
    "harness-retrieval-dp",
    "harness-cm-root",
    "fixed-planner",
    "agent-planner",
]

METRIC_COLUMNS = [
    ("residue_label_accuracy", "residue_accuracy"),
    ("exact_match_rate", "exact_sequence_accuracy"),
    ("legality_pass_rate", "legality_pass_rate"),
    ("base_preservation_rate", "input_preservation_rate"),
    ("anticodon_accuracy", "anticodon_accuracy"),
    ("discriminator_accuracy", "discriminator_73_accuracy"),
    ("cca_accuracy", "cca_74_76_accuracy"),
    ("variable_accuracy", "variable_region_accuracy"),
    ("region_accuracy", "core_region_accuracy"),
]

RUNG_KEYS = (
    "method_backend",
    "agent_planner_policy",
    "agent_canonical_root_strategy",
    "use_llm",
    "agent_llm_postcheck_enabled",
    "agent_deterministic_boundary_refinement_enabled",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", required=True, help="Comma-separated stored run ids")
    parser.add_argument("--labels", default="", help="Comma-separated short labels, same order as --runs")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Include a run that has not been sealed yet")
    parser.add_argument("--require-sealed", action="store_true",
                        help="Refuse a run that has not been sealed and fully scored")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--stem", default="ablation_ladder_2530")
    return parser.parse_args()


def raw_llm_split(run_id: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What happened to each attempt of the model-only control.

    The control is scored over every sequence, but the interesting split is by
    outcome: how many attempts produced one label per residue at all, and how
    those usable answers compare with the coordinate guards.  ``rows`` are the
    already re-graded case rows for this run.
    """
    answered = [r for r in rows if str(r.get("status")) == "ok"]
    usable = [
        r for r in answered
        if int(r.get("label_total") or 0) > 0
        and int(r.get("prediction_residue_count") or 0) == int(r.get("input_len") or 0)
    ]
    latencies = sorted(float(r["elapsed_sec"]) for r in answered if r.get("elapsed_sec") is not None)

    def rate(group: list, key: str) -> Optional[float]:
        if not group:
            return None
        return sum(int(r.get(key) or 0) for r in group) / len(group)

    def accuracy(group: list) -> Optional[float]:
        total = sum(int(r.get("label_total") or 0) for r in group)
        if not total:
            return None
        return sum(int(r.get("label_correct") or 0) for r in group) / total

    return {
        "run_id": run_id,
        "n_cases": len(rows),
        "answered": len(answered),
        "unanswered_or_technical": len(rows) - len(answered),
        "usable": len(usable),
        "label_count_mismatch": len(answered) - len(usable),
        "residue_accuracy_all": accuracy(rows),
        "residue_accuracy_usable": accuracy(usable),
        "exact_rate_all": rate(rows, "exact_match"),
        "exact_rate_usable": rate(usable, "exact_match"),
        "legality_rate_usable": rate(usable, "legality_passed"),
        "median_answer_sec": latencies[len(latencies) // 2] if latencies else None,
        "max_answer_sec": latencies[-1] if latencies else None,
    }


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return ""


def rung_name(method: Dict[str, Any], run: Dict[str, Any]) -> str:
    """Name the ladder rung from the frozen run contract, never from the label.

    Two controls share ``policy=fixed`` and the same root strategy and differ
    only in whether the language model still answers the local prompts, so the
    recorded switches are the only safe way to tell them apart.
    """
    backend = str(method.get("method_backend") or run.get("method_backend") or "")
    policy = str(method.get("agent_planner_policy") or "")
    root = str(method.get("agent_canonical_root_strategy") or "")
    uses_llm = bool(method.get("use_llm"))
    if backend == "raw_llm":
        return "raw-llm"
    if backend == "cm":
        return "cm"
    if backend == "agent":
        if policy not in ("llm", "fixed"):
            # A run recorded before the planner switch existed cannot be placed
            # on the ladder without guessing.
            return "unknown"
        if policy == "llm" and uses_llm:
            return "agent-planner"
        if root == "cm_first":
            return "harness-cm-root"
        return "fixed-planner" if uses_llm else "harness-retrieval-dp"
    return backend or "unknown"


def main() -> int:
    args = parse_args()
    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()]
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    if labels and len(labels) != len(run_ids):
        raise SystemExit("--labels must have the same number of entries as --runs")

    store = BenchmarkRunStore.readonly()
    rows: List[Dict[str, Any]] = []
    fingerprints: Dict[str, Any] = {}
    graded: Dict[str, List[Dict[str, Any]]] = {}
    moved: Dict[str, Dict[str, int]] = {}

    for index, run_id in enumerate(run_ids):
        run = store.run_row(run_id)
        if run is None:
            raise SystemExit(f"{run_id}: no such run in MySQL")
        # The seal-time summary is kept only to recover the run contract; the
        # numbers below are recomputed, because the answer table may have been
        # corrected after this run finished.
        summary_raw = run.get("summary_json")
        sealed = json.loads(summary_raw) if isinstance(summary_raw, str) else (summary_raw or {})
        if not sealed and not args.allow_partial:
            raise SystemExit(
                f"{run_id}: the run has not been sealed yet (pass --allow-partial)"
            )
        case_rows = store.case_rows(run_id=run_id)
        if not case_rows:
            raise SystemExit(f"{run_id}: no stored case predictions to score")
        graded[run_id] = case_rows
        summary = summarize_case_rows(case_rows)
        counts = summary["counts"]
        moved[run_id] = store.revised_answers(run_id)
        if args.require_sealed and str(run.get("state")) != "sealed":
            raise SystemExit(f"{run_id}: state={run.get('state')}")
        if int(counts.get("error_cases") or 0) or int(summary["agent_activity"]["technical_issue_cases"] or 0):
            raise SystemExit(
                f"{run_id}: unresolved technical failures "
                f"(error_cases={counts.get('error_cases')}); rerun before publishing this ladder"
            )

        # The frozen run contract, not the summary, records which candidate the
        # Harness started from; that is what separates the deterministic rungs.
        config = json.loads(run["config_json"]) if isinstance(run.get("config_json"), str) else (run.get("config_json") or {})
        method = config.get("method") or sealed.get("method") or {}
        rung = rung_name(method, run)
        # The cohort is the registered target list, not the number of rows written
        # so far: a run that is still going describes the same questions.
        key = (str(run.get("gold_sha256") or ""), str(run.get("selected_seqnames_sha256") or ""),
               int(run.get("n_targets") or 0))
        if not fingerprints:
            fingerprints = {"gold_sha256": key[0], "selected_seqnames_sha256": key[1],
                            "total_cases": key[2]}
        elif fingerprints != {"gold_sha256": key[0], "selected_seqnames_sha256": key[1], "total_cases": key[2]}:
            raise SystemExit(
                f"{run_id}: cohort/gold fingerprint differs from {run_ids[0]} — "
                f"these runs cannot be compared in one table"
            )

        metrics = summary.get("metrics") or {}
        agent = summary.get("agent_activity") or {}
        timing = summary.get("timing") or {}
        llm = summary.get("llm_usage") or {}
        guard = summary.get("guard_challenge_audit") or {}
        total_tokens = int((llm.get("tokens") or {}).get("llm_total_tokens") or 0)

        row: Dict[str, Any] = {
            "run_id": run_id,
            "rung": rung,
            "label": labels[index] if labels else rung,
            "state": run.get("state"),
            "method_backend": run.get("method_backend"),
            "agent_planner_policy": sealed.get("agent_planner_policy") or method.get("agent_planner_policy") or "",
            "agent_canonical_root_strategy": method.get("agent_canonical_root_strategy") or "",
            "use_llm": method.get("use_llm"),
            "model_id": run.get("model_id") or "",
            "code_commit": run.get("code_commit") or "",
            "cases": key[2] or counts.get("total_cases"),
            "scored_cases": counts.get("gold_scored_cases"),
            "accepted": counts.get("accepted_cases"),
            "investigate": (counts.get("dispositions") or {}).get("investigate"),
            "planner_llm_calls": agent.get("planner_llm_call_count", llm.get("call_count", 0)),
            "llm_total_tokens": total_tokens,
            "mean_elapsed_sec": timing.get("elapsed_mean_sec"),
            "median_elapsed_sec": timing.get("elapsed_median_sec"),
            "guard_challenges": guard.get("challenge_count"),
            "guard_rejection_rate": guard.get("rejection_rate"),
            "finished_at": str(run.get("finished_at") or ""),
        }
        for src, dst in METRIC_COLUMNS:
            row[dst] = metrics.get(src)
        flow = summary["flow_groups"]
        row["auto_accept_coverage"] = (
            int(counts.get("accepted_cases") or 0) / key[2] if key[2] else None
        )
        row["accepted_exact_rate"] = (
            flow["accepted_exact"] / flow["accepted_scored"] if flow["accepted_scored"] else None
        )
        row["review_exact_rate"] = (
            flow["review_exact"] / flow["review_scored"] if flow["review_scored"] else None
        )
        row["accepted_scored_cases"] = flow["accepted_scored"]
        row["review_scored_cases"] = flow["review_scored"]
        row["unscored_cases"] = counts["gold_unscored_cases"]
        for entry in RUNG_KEYS:
            row.setdefault(entry, method.get(entry))
        rows.append(row)

    rows.sort(key=lambda r: DEFAULT_ORDER.index(r["rung"]) if r["rung"] in DEFAULT_ORDER else 99)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tsv = args.out_dir / f"{args.stem}.tsv"
    columns = list(rows[0])
    with tsv.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(columns) + "\n")
        for row in rows:
            handle.write("\t".join("" if row.get(c) is None else str(row.get(c)) for c in columns) + "\n")

    raw_rows = [r for r in rows if r["rung"] == "raw-llm"]
    if raw_rows:
        split = raw_llm_split(raw_rows[0]["run_id"], graded[raw_rows[0]["run_id"]])
        (args.out_dir / f"{args.stem}_raw_llm.json").write_text(
            json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"raw-model control: {split['usable']}/{split['n_cases']} usable, "
            f"{split['unanswered_or_technical']} unanswered, "
            f"exact among usable {pct(split['exact_rate_usable'])}, "
            f"guard pass among usable {pct(split['legality_rate_usable'])}"
        )

    provenance = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "benchmark/scripts/summarize_ablation_ladder.py",
        "code_commit": git_commit(),
        "store": {"mysql_database": "sprinzlmap", "minio_bucket": "llm-trnaalign-artifacts"},
        "cohort": fingerprints,
        "runs": [r["run_id"] for r in rows],
        "score_basis": (
            "predictions frozen in benchmark_executions, graded against the current "
            "trna_gold answer table; no model call was replayed"
        ),
        "answers_revised_after_seal": moved,
        "notes": "no local run directories; run metadata from the execution table",
    }
    (args.out_dir / f"{args.stem}_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    width = max(len(r["label"]) for r in rows)
    for row in rows:
        print(f"{row['label']:<{width}}  residue={pct(row['residue_accuracy'])}  exact={pct(row['exact_sequence_accuracy'])}"
              f"  legal={pct(row['legality_pass_rate'])}  accepted={row['accepted']}"
              f"  llm_calls={row['planner_llm_calls']}  median={row['median_elapsed_sec']}s")
    print(f"\nwrote {tsv}")
    return 0


def pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
