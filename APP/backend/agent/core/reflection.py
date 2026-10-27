# -*- coding: utf-8 -*-
"""
Phase 1: Structured Reflection
Maps Evaluator hard gate rejections and score deltas into
structured failure summaries for the Planner's Task Ledger.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .state import AgentEvaluation

logger = logging.getLogger(__name__)


@dataclass
class FailureDiagnosis:
    """Structured summary of why a branch failed or stalled."""
    branch_id: str
    is_hard_failure: bool
    failure_regions: List[str]
    summary: str
    suggested_next_actions: List[str]


def reflect_on_evaluation(
    evaluation: AgentEvaluation,
    warnings: List[str],
) -> FailureDiagnosis:
    """
    Converts an evaluation and warnings into a structured failure diagnosis.
    This is NOT free-form LLM reflection — it is rule-based mapping.
    """
    failure_regions: List[str] = []
    suggestions: List[str] = []
    summary_parts: List[str] = []

    # 1. Hard gate failure
    if not evaluation.hard_valid:
        summary_parts.append("Hard constraint violated (base preservation).")
        suggestions.append("select_template: Try a different template.")
        suggestions.append("adjust_boundary_policy: Loosen boundary constraints.")

    # 2. Check warnings for rollback signals
    rollback_regions = []
    for w in warnings:
        w_lower = w.lower()
        if "rollback" in w_lower:
            summary_parts.append(f"Rollback detected: {w}")
            # Try to extract which region triggered rollback
            for region_name in [
                "Acceptor stem 5'",
                "D loop + D stem",
                "Anticodon loop + Anticodon stem",
                "Variable loop",
                "T loop + T stem",
                "Aminoacyl arm 3' end",
            ]:
                if region_name.lower() in w_lower:
                    rollback_regions.append(region_name)
            if not rollback_regions:
                rollback_regions.append("unknown_region")

    failure_regions = rollback_regions

    if rollback_regions:
        for region in rollback_regions:
            suggestions.append(
                f"swap_prompt_variant: Try alternative prompt for '{region}'."
            )
            suggestions.append(
                f"force_dp_only_for_region: Disable LLM for '{region}' and rely on DP."
            )

    # 3. Score regression
    if evaluation.hard_valid and evaluation.delta_vs_baseline.pair_f1 < 0:
        summary_parts.append(
            f"Score regression vs baseline: delta_pair_f1={evaluation.delta_vs_baseline.pair_f1:+.4f}."
        )
        suggestions.append("stop_and_replan: Score regression detected, try different strategy.")

    # 4. Stall (no improvement)
    if evaluation.hard_valid and evaluation.delta_vs_baseline.pair_f1 == 0:
        summary_parts.append("No improvement over baseline (stall).")
        suggestions.append("expand_template_topk: Search wider template pool.")

    if not summary_parts:
        summary_parts.append("Branch completed with improvement.")

    return FailureDiagnosis(
        branch_id=evaluation.branch_id,
        is_hard_failure=not evaluation.hard_valid,
        failure_regions=failure_regions,
        summary=" ".join(summary_parts),
        suggested_next_actions=suggestions,
    )


__all__ = ["FailureDiagnosis", "reflect_on_evaluation"]
