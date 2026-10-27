# -*- coding: utf-8 -*-
"""
CLI entrypoint for Agent Offline Diagnostics (Phase 1 MVP).
Usage:
    python -m cli.run_agent_diagnostics --seq GGGCCC... --anticodon TTC
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict

# Ensure project root is importable
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agent.core.state import (
    AgentBudget,
    AgentEvaluation,
    AlignmentAgentState,
    AnnotationContext,
    BranchConfig,
    DeltaScore,
    TemplateCandidate,
)
from backend.agent.core.planner import AlignmentPlanner, SearchResult


def build_initial_state(
    target_seq: str,
    anticodon: str,
    template_name: str = "",
    use_llm: bool = True,
    max_branches: int = 8,
    max_wall_time_sec: int = 120,
) -> AlignmentAgentState:
    """Build an AlignmentAgentState for a single diagnostic run."""
    trace_id = f"agent-run-{int(time.time())}"

    # In Phase 1, template_candidates are bootstrapped from the primary template.
    # Phase 2 will inject real retrieval top-k candidates.
    candidates = []
    if template_name:
        candidates.append(
            TemplateCandidate(
                template_name=template_name,
                source="user_specified",
                rank=1,
                similarity=1.0,
            )
        )

    state = AlignmentAgentState(
        case_id=f"diag-{trace_id}",
        target_seq=target_seq,
        anticodon_hint=anticodon,
        template_candidates=candidates,
        baseline_result=None,  # Will be computed as Branch 0
        annotation_context=AnnotationContext(
            annotation_source="unknown",
            empty_count=0,
            empty_regions=[],
            boundary_resolved=False,
        ),
        current_branch_id="root",
        current_config=BranchConfig(
            template_name=template_name,
            use_llm=use_llm,
            llm_reasoning=False,
            runtime_overrides={},
        ),
        budget=AgentBudget(
            max_branches=max_branches,
            max_llm_calls=max_branches * 6,
            max_evaluations=max_branches * 2,
            max_wall_time_sec=max_wall_time_sec,
        ),
        trace_id=trace_id,
    )
    return state


def print_search_result(result: SearchResult) -> None:
    """Pretty-print the search result to stdout."""
    print("\n" + "=" * 70)
    print("AGENT SEARCH RESULT")
    print("=" * 70)

    print(f"\n  Best Branch:        {result.best_branch_id}")
    print(f"  Termination Reason: {result.termination_reason}")

    pl = result.progress_ledger
    print(f"\n  Branches Used:      {pl.branches_used}")
    print(f"  Evaluations Used:   {pl.evaluations_used}")
    print(f"  Wall Time:          {pl.wall_time_used:.1f}s")
    print(f"  Stall Count:        {pl.stall_count}")

    if result.best_evaluation:
        be = result.best_evaluation
        print(f"\n  Best pair_f1:       {be.pair_f1:.4f}")
        print(f"  Best hard_valid:    {be.hard_valid}")
        print(f"  Delta vs Baseline:  {be.delta_vs_baseline.pair_f1:+.4f}")

    print("\n── Action History ──")
    for a in result.all_actions:
        print(f"  [{a.action_id}] {a.action_type}: {a.reason[:60]}")

    print("\n── Evaluations ──")
    for e in result.all_evaluations:
        marker = " ★" if e.selected_as_best else ""
        print(
            f"  [{e.branch_id}] valid={e.hard_valid}, "
            f"pair_f1={e.pair_f1:.4f}, "
            f"delta={e.delta_vs_baseline.pair_f1:+.4f}{marker}"
        )

    print("\n── Reflections ──")
    for r in result.all_reflections:
        print(f"  [{r.branch_id}] {r.summary[:80]}")

    print("\n── Task Ledger ──")
    tl = result.task_ledger
    print(f"  Plan: {tl.current_plan}")
    print(f"  Best: {tl.best_branch_summary}")

    print("\n" + "=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Agent Offline Diagnostics on a single tRNA sequence."
    )
    parser.add_argument("--seq", required=True, help="Target tRNA sequence")
    parser.add_argument("--anticodon", default="", help="Anticodon hint (e.g. TTC)")
    parser.add_argument("--template", default="", help="Initial template name")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM postcheck")
    parser.add_argument("--max-branches", type=int, default=8, help="Max branches budget")
    parser.add_argument("--max-time", type=int, default=120, help="Max wall time (seconds)")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    state = build_initial_state(
        target_seq=args.seq,
        anticodon=args.anticodon,
        template_name=args.template,
        use_llm=not args.no_llm,
        max_branches=args.max_branches,
        max_wall_time_sec=args.max_time,
    )

    planner = AlignmentPlanner(state)
    result = planner.run_search()

    if args.json:
        # Serialize for machine consumption
        output = {
            "best_branch_id": result.best_branch_id,
            "termination_reason": result.termination_reason,
            "branches_used": result.progress_ledger.branches_used,
            "evaluations_used": result.progress_ledger.evaluations_used,
            "wall_time_sec": result.progress_ledger.wall_time_used,
            "best_evaluation": asdict(result.best_evaluation) if result.best_evaluation else None,
            "actions": [asdict(a) for a in result.all_actions],
            "evaluations": [asdict(e) for e in result.all_evaluations],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_search_result(result)


if __name__ == "__main__":
    main()
