# -*- coding: utf-8 -*-
"""
Phase 4: Agent Gateway
Optional enhancement layer that wraps the standard alignment call.
If the Agent policy determines the initial result is low-confidence,
it triggers a bounded Agent search and returns the best result.

CRITICAL DESIGN PRINCIPLES:
1. The gateway is DISABLED by default (AgentPolicy.enabled = False)
2. The standard pipeline is ALWAYS called first as the baseline
3. If Agent search fails or produces worse results, the baseline is returned
4. The original perform_full_alignment behavior is NEVER altered

Usage (opt-in only):
    from backend.agent.gateway import agent_enhanced_alignment

    result = agent_enhanced_alignment(
        target_seq="GGGCCC...",
        anticode="TTC",
        use_llm=True,
    )
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .core.policy import AgentPolicy, should_trigger_agent
from .core.state import (
    AgentBudget,
    AgentEvaluation,
    AlignmentAgentState,
    AnnotationContext,
    BranchConfig,
    DeltaScore,
    TemplateCandidate,
)
from .core.planner import AlignmentPlanner

logger = logging.getLogger(__name__)


def agent_enhanced_alignment(
    target_seq: str,
    output_csv_path: str = "/dev/null",
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: Optional[str] = None,
    policy: Optional[AgentPolicy] = None,
) -> Tuple[str, str, List[str]]:
    """
    Production-safe alignment with optional Agent enhancement.

    1. Always runs the standard pipeline first.
    2. If the Agent policy triggers, runs a bounded search.
    3. Returns the best result (standard or Agent-improved).
    4. If anything goes wrong, always falls back to the standard result.

    Returns: (template_name, csv_raw, warnings)
    """
    # Resolve policy
    if policy is None:
        policy = AgentPolicy.from_env()

    # Step 1: ALWAYS run the standard pipeline first
    from backend.domain.alignment.align import perform_full_alignment

    baseline_tpl, baseline_csv, baseline_warnings = perform_full_alignment(
        target_seq=target_seq,
        output_csv_path=output_csv_path,
        anticode=anticode,
        use_llm=use_llm,
        llm_reasoning=llm_reasoning,
        template_name=template_name,
    )

    # Step 2: Check if Agent should be triggered
    if not should_trigger_agent(policy, baseline_warnings, baseline_csv):
        return baseline_tpl, baseline_csv, baseline_warnings

    # Step 3: Run Agent search (with safety wrapper)
    logger.info(f"[Gateway] Agent triggered for seq length={len(target_seq)}")
    try:
        agent_tpl, agent_csv, agent_warnings = _run_agent_search(
            target_seq=target_seq,
            anticode=anticode,
            baseline_template=baseline_tpl,
            baseline_csv=baseline_csv,
            baseline_warnings=baseline_warnings,
            use_llm=use_llm,
            policy=policy,
        )
        return agent_tpl, agent_csv, agent_warnings
    except Exception as e:
        # Agent failed — fall back to baseline (Section 7.2: always safe fallback)
        logger.warning(f"[Gateway] Agent search failed, falling back to baseline: {e}")
        baseline_warnings.append(f"Agent search failed (fallback to baseline): {e}")
        return baseline_tpl, baseline_csv, baseline_warnings


def _run_agent_search(
    target_seq: str,
    anticode: str,
    baseline_template: str,
    baseline_csv: str,
    baseline_warnings: List[str],
    use_llm: bool,
    policy: AgentPolicy,
) -> Tuple[str, str, List[str]]:
    """
    Internal: constructs AgentState from baseline results and runs a bounded search.
    """
    state = AlignmentAgentState(
        case_id=f"online-{int(time.time())}",
        target_seq=target_seq,
        anticodon_hint=anticode,
        template_candidates=[
            TemplateCandidate(
                template_name=baseline_template,
                source="baseline_pipeline",
                rank=1,
                similarity=1.0,
            )
        ],
        baseline_result=AgentEvaluation(
            branch_id="root",
            hard_valid=True,
            pair_f1=0.0,   # Unknown in online mode (no gold standard)
        ),
        annotation_context=AnnotationContext(
            annotation_source="online",
            empty_count=0,
            empty_regions=[],
            boundary_resolved=False,
        ),
        current_branch_id="root",
        current_config=BranchConfig(
            template_name=baseline_template,
            use_llm=use_llm,
            llm_reasoning=False,
            runtime_overrides={},
        ),
        budget=AgentBudget(
            max_branches=policy.online_max_branches,
            max_llm_calls=policy.online_max_llm_calls,
            max_evaluations=policy.online_max_branches * 2,
            max_wall_time_sec=policy.online_max_wall_time_sec,
        ),
        trace_id=f"online-{int(time.time())}",
    )

    planner = AlignmentPlanner(state)
    result = planner.run_search()

    # If Agent found something better, use it
    if result.best_evaluation and result.best_evaluation.hard_valid:
        # In online mode, we can't directly compare pair_f1 (no gold),
        # so we rely on the hard_valid gate and structural checks.
        # For now, return baseline — in Phase 2+ the executor will capture CSV output.
        logger.info(
            f"[Gateway] Agent search completed: "
            f"best_branch={result.best_branch_id}, "
            f"termination={result.termination_reason}, "
            f"branches_used={result.progress_ledger.branches_used}"
        )

    # Fall back to baseline for now — the Agent's value in online mode
    # is primarily in the trace/memory it generates for later offline analysis.
    warnings = list(baseline_warnings)
    warnings.append(
        f"Agent search completed ({result.progress_ledger.branches_used} branches, "
        f"reason={result.termination_reason})"
    )
    return baseline_template, baseline_csv, warnings


__all__ = ["agent_enhanced_alignment"]
