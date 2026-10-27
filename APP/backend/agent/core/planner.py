# -*- coding: utf-8 -*-
"""
Phase 1: Agent Planner
Implements the Task Ledger, Progress Ledger, and Budgeted BFS search loop.
This is the brain of the Alignment Agent.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .state import (
    ActionBudgetCost,
    AgentAction,
    AgentBudget,
    AgentEvaluation,
    AlignmentAgentState,
    BranchConfig,
    DeltaScore,
    ObservationContext,
    TemplateCandidate,
    TraceEvent,
)
from .evaluator import ObjectiveEvaluator
from .executor import BranchExecutionResult, execute_branch
from .reflection import FailureDiagnosis, reflect_on_evaluation

logger = logging.getLogger(__name__)


# ── Ledger Definitions ──────────────────────────────────────────────────────

@dataclass
class TaskLedger:
    """Section 9.1: What the agent knows, hypothesizes, and plans."""
    known_facts: List[str] = field(default_factory=list)
    current_hypotheses: List[str] = field(default_factory=list)
    candidate_actions: List[AgentAction] = field(default_factory=list)
    current_plan: str = ""
    best_branch_summary: str = ""


@dataclass
class ProgressLedger:
    """Section 9.1: Tracks search progress and budget consumption."""
    current_branch_id: str = "root"
    current_score: float = 0.0
    last_action_gain: float = 0.0
    stall_count: int = 0
    needs_replan: bool = False
    branches_used: int = 0
    evaluations_used: int = 0
    llm_calls_used: int = 0
    wall_time_used: float = 0.0


@dataclass
class SearchResult:
    """Final output of a complete Agent search run."""
    best_branch_id: str
    best_evaluation: Optional[AgentEvaluation]
    all_evaluations: List[AgentEvaluation]
    all_actions: List[AgentAction]
    all_reflections: List[FailureDiagnosis]
    all_trace_events: List[TraceEvent]
    termination_reason: str
    task_ledger: TaskLedger
    progress_ledger: ProgressLedger


# ── Planner ──────────────────────────────────────────────────────────────────

class AlignmentPlanner:
    """
    The core search controller.
    Implements a budgeted BFS over the Action Space defined in Section 8.3.
    """

    MAX_STALL = 3  # Consecutive branches with no gain before re-plan / stop

    def __init__(self, state: AlignmentAgentState):
        self.state = state
        self.task_ledger = TaskLedger()
        self.progress = ProgressLedger()
        self.evaluator = ObjectiveEvaluator(
            target_seq=state.target_seq,
            baseline_eval=state.baseline_result,
        )
        # Accumulated results
        self._evaluations: List[AgentEvaluation] = []
        self._actions: List[AgentAction] = []
        self._reflections: List[FailureDiagnosis] = []
        self._trace_events: List[TraceEvent] = []
        
        # Section 8.5: Initialize best with baseline so we have a fallback
        self._best_eval = state.baseline_result
        if self._best_eval:
            self._best_eval.selected_as_best = True
            self.progress.current_score = self._best_eval.pair_f1
            
        self._action_counter = 0
        self._event_counter = 0

    # ── Public API ───────────────────────────────────────────────────────

    def run_search(self) -> SearchResult:
        """
        Main entry point: Runs the full BFS search within the budget.
        """
        logger.info(f"[Planner] Starting search for case_id={self.state.case_id}")
        self._initialize_task_ledger()
        t_start = time.time()

        # Phase 1: Generate candidate actions from initial state
        candidate_actions = self._generate_candidate_actions()
        self.task_ledger.candidate_actions = candidate_actions
        self.task_ledger.current_plan = (
            f"BFS over {len(candidate_actions)} candidate actions "
            f"within budget of {self.state.budget.max_branches} branches."
        )

        termination_reason = "all_candidates_exhausted"

        for action in candidate_actions:
            # Budget checks (Section 8.5)
            if self.progress.branches_used >= self.state.budget.max_branches:
                termination_reason = "budget_exhausted:max_branches"
                break
            if self.progress.evaluations_used >= self.state.budget.max_evaluations:
                termination_reason = "budget_exhausted:max_evaluations"
                break
            elapsed = time.time() - t_start
            if elapsed >= self.state.budget.max_wall_time_sec:
                termination_reason = "budget_exhausted:max_wall_time"
                break
            if self.progress.stall_count >= self.MAX_STALL:
                termination_reason = "consecutive_stall_limit"
                break

            # Build branch config from action
            config = self._action_to_config(action)

            # Execute
            logger.info(f"[Planner] Executing action {action.action_id}: {action.action_type}")
            exec_result = execute_branch(self.state, action, config)
            self.progress.branches_used += 1
            self.progress.wall_time_used = round(time.time() - t_start, 2)
            self._actions.append(action)

            # Evaluate
            evaluation = self.evaluator.evaluate_branch(
                branch_id=action.branch_id,
                aligned_sequence=exec_result.aligned_sequence,
                aligned_numbering=exec_result.aligned_numbering,
                template_sequence=exec_result.aligned_template,
                pair_f1=0.0,  # will be filled by gold-standard comparison
                map_acc=0.0,
                gap_f1=0.0,
            )
            self.progress.evaluations_used += 1
            self._evaluations.append(evaluation)

            # Track best
            if evaluation.hard_valid:
                if self._best_eval is None or evaluation.pair_f1 > self._best_eval.pair_f1:
                    evaluation.selected_as_best = True
                    if self._best_eval:
                        self._best_eval.selected_as_best = False
                    self._best_eval = evaluation
                    self.progress.current_score = evaluation.pair_f1
                    self.progress.last_action_gain = evaluation.delta_vs_baseline.pair_f1
                    self.progress.stall_count = 0
                    self.task_ledger.best_branch_summary = (
                        f"Branch {action.branch_id} via {action.action_type}: "
                        f"pair_f1={evaluation.pair_f1:.4f}"
                    )
                else:
                    self.progress.stall_count += 1
            else:
                self.progress.stall_count += 1

            # Reflect
            diagnosis = reflect_on_evaluation(evaluation, exec_result.warnings)
            self._reflections.append(diagnosis)

            # Trace event
            self._emit_trace(action, evaluation, exec_result)

            # Log progress
            logger.info(
                f"[Planner] Branch {action.branch_id}: "
                f"valid={evaluation.hard_valid}, "
                f"pair_f1={evaluation.pair_f1:.4f}, "
                f"stall={self.progress.stall_count}/{self.MAX_STALL}"
            )

        # Done
        best_branch_id = self._best_eval.branch_id if self._best_eval else "root"
        logger.info(
            f"[Planner] Search complete: best={best_branch_id}, "
            f"reason={termination_reason}, "
            f"branches_used={self.progress.branches_used}"
        )

        return SearchResult(
            best_branch_id=best_branch_id,
            best_evaluation=self._best_eval,
            all_evaluations=self._evaluations,
            all_actions=self._actions,
            all_reflections=self._reflections,
            all_trace_events=self._trace_events,
            termination_reason=termination_reason,
            task_ledger=self.task_ledger,
            progress_ledger=self.progress,
        )

    # ── Private Helpers ──────────────────────────────────────────────────

    def _initialize_task_ledger(self) -> None:
        self.task_ledger.known_facts = [
            f"target_seq length = {len(self.state.target_seq)}",
            f"anticodon_hint = {self.state.anticodon_hint}",
            f"template_candidates count = {len(self.state.template_candidates)}",
        ]
        if self.state.baseline_result:
            self.task_ledger.known_facts.append(
                f"baseline pair_f1 = {self.state.baseline_result.pair_f1:.4f}"
            )
        self.task_ledger.current_hypotheses = [
            "Current template may not be optimal for this sequence.",
            "Boundary refinement or prompt variant may improve postcheck.",
        ]

    def _generate_candidate_actions(self) -> List[AgentAction]:
        """
        Generates the initial pool of typed candidate actions.
        Phase 1 focuses on: template switching, prompt variants, DP-only fallback.
        """
        actions: List[AgentAction] = []

        # Action 1: Try alternative templates (top-k from retrieval)
        for candidate in self.state.template_candidates:
            if candidate.template_name == self.state.current_config.template_name:
                continue  # skip the one already used in baseline
            self._action_counter += 1
            actions.append(AgentAction(
                action_id=f"act-{self._action_counter:04d}",
                branch_id=f"branch-{self._action_counter:04d}",
                parent_branch_id="root",
                action_type="select_template",
                params={
                    "template_name": candidate.template_name,
                    "rank": candidate.rank,
                    "similarity": candidate.similarity,
                },
                reason=f"Try template ranked #{candidate.rank} (sim={candidate.similarity:.4f})",
                budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=3),
            ))

        # Action 2: Force DP-only (no LLM) with current template
        self._action_counter += 1
        actions.append(AgentAction(
            action_id=f"act-{self._action_counter:04d}",
            branch_id=f"branch-{self._action_counter:04d}",
            parent_branch_id="root",
            action_type="force_dp_only_for_region",
            params={"region": "all"},
            reason="Test if LLM postcheck is hurting rather than helping.",
            budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=2),
        ))

        # Action 3: Re-run with LLM reasoning enabled (deeper chain-of-thought)
        if not self.state.current_config.llm_reasoning:
            self._action_counter += 1
            actions.append(AgentAction(
                action_id=f"act-{self._action_counter:04d}",
                branch_id=f"branch-{self._action_counter:04d}",
                parent_branch_id="root",
                action_type="switch_postcheck_mode",
                params={"region": "all", "preset_name": "llm_reasoning_on"},
                reason="Activate LLM reasoning mode for deeper structural analysis.",
                budget_cost=ActionBudgetCost(llm_calls_reserved=6, expected_wall_time_sec=15),
            ))

        return actions

    def _action_to_config(self, action: AgentAction) -> BranchConfig:
        """Translate an AgentAction into a BranchConfig for the executor."""
        base = self.state.current_config

        if action.action_type == "select_template":
            return BranchConfig(
                template_name=action.params["template_name"],
                use_llm=base.use_llm,
                llm_reasoning=base.llm_reasoning,
                runtime_overrides=base.runtime_overrides,
            )
        elif action.action_type == "force_dp_only_for_region":
            return BranchConfig(
                template_name=base.template_name,
                use_llm=False,
                llm_reasoning=False,
                runtime_overrides=base.runtime_overrides,
            )
        elif action.action_type == "switch_postcheck_mode":
            return BranchConfig(
                template_name=base.template_name,
                use_llm=True,
                llm_reasoning=True,
                runtime_overrides=base.runtime_overrides,
            )
        elif action.action_type == "swap_prompt_variant":
            overrides = dict(base.runtime_overrides)
            overrides[f"prompt_variant_{action.params.get('region', 'all')}"] = (
                action.params.get("prompt_id", "")
            )
            return BranchConfig(
                template_name=base.template_name,
                use_llm=True,
                llm_reasoning=base.llm_reasoning,
                runtime_overrides=overrides,
            )
        else:
            # Default: use base config with minor runtime override
            return BranchConfig(
                template_name=base.template_name,
                use_llm=base.use_llm,
                llm_reasoning=base.llm_reasoning,
                runtime_overrides=base.runtime_overrides,
            )

    def _emit_trace(
        self,
        action: AgentAction,
        evaluation: AgentEvaluation,
        exec_result: BranchExecutionResult,
    ) -> None:
        self._event_counter += 1
        event = TraceEvent(
            trace_id=self.state.trace_id,
            case_id=self.state.case_id,
            event_index=self._event_counter,
            timestamp=datetime.now(timezone.utc).isoformat(),
            branch_id=action.branch_id,
            parent_branch_id=action.parent_branch_id,
            stage="evaluation",
            action_ref=action.action_id,
            summary=(
                f"Action {action.action_type}: "
                f"valid={evaluation.hard_valid}, "
                f"pair_f1={evaluation.pair_f1:.4f}"
            ),
            observation=ObservationContext(
                warning_count=len(exec_result.warnings),
                rollback_triggered=any("rollback" in w.lower() for w in exec_result.warnings),
                llm_regions_touched=[],  # TODO: extract from exec_result in Phase 2
            ),
            evaluation_ref=f"eval-{self._event_counter:04d}",
            termination_hint=None,
        )
        self._trace_events.append(event)


__all__ = ["AlignmentPlanner", "SearchResult", "TaskLedger", "ProgressLedger"]
