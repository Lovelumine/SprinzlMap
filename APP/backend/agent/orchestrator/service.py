from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from ..contracts import (
    ActionRecord,
    AgentWorkState,
    AlignmentRequest,
    AlignmentResponse,
    CandidateAction,
    RootSnapshot,
    SearchBudget,
    SearchResult,
)
from ..evaluation import (
    HardGuards,
    OnlineRanker,
    assess_acceptor_tail_ambiguity,
    assess_local_coordinate_ambiguity,
    assess_variable_boundary_ambiguity,
    assess_variable_boundary_reference_agreement,
    candidate_local_resolution,
)
from ..execution import BranchExecutor, RootSnapshotBuilder
from ..tools import CMNumberingTool
from .context_engineering import answer_free_context_signature
from .difficulty import DifficultyAssessor
from .llm_action_planner import (
    ConstrainedLLMActionPlanner,
    PlannerDecision,
)
from .planner import AgentPlannerV2
from .progress import emit_progress, record_trace, extend_trace
from .scheduler import ParallelBranchScheduler
from .specialized import SpecializedRouteAssessor
from backend.domain.alignment.special_routes import (
    SpecialRouteEvidence,
    SpecialRouteEvidenceService,
)
from backend.domain.alignment.canonical_like_structure import (
    assess_canonical_like_structure,
)
from backend.domain.alignment.input_evidence import assess_input_evidence
from backend.domain.alignment.engine import STANDARD_ALIGNMENT_REGIONS
from backend.domain.alignment.sprinzl_numbering_manual import compact_row_view
from backend.domain.literature import agent_lookup


def _reference_tool_not_applicable(exc: Exception) -> bool:
    return bool(
        isinstance(exc, ValueError)
        and "does not show a standard cloverleaf structure" in str(exc).lower()
    )


class AgentAlignmentService:
    """
    Scientific Agent entrypoint.

    The LLM Agent selects Harness-allowed scientific tools. Tools generate
    candidates; deterministic guards and ranking accept, roll back or abstain.
    """

    def __init__(
        self,
        root_builder: RootSnapshotBuilder | None = None,
        difficulty_assessor: DifficultyAssessor | None = None,
        route_assessor: SpecializedRouteAssessor | None = None,
        planner: AgentPlannerV2 | None = None,
        action_planner: ConstrainedLLMActionPlanner | None = None,
        branch_executor: BranchExecutor | None = None,
        hard_guards: HardGuards | None = None,
        online_ranker: OnlineRanker | None = None,
        scheduler: ParallelBranchScheduler | None = None,
        special_route_service: SpecialRouteEvidenceService | None = None,
    ) -> None:
        self.root_builder = root_builder or RootSnapshotBuilder()
        self.difficulty_assessor = difficulty_assessor or DifficultyAssessor()
        self.route_assessor = route_assessor or SpecializedRouteAssessor()
        self.planner = planner or AgentPlannerV2()
        self.action_planner = action_planner or ConstrainedLLMActionPlanner()
        self.branch_executor = branch_executor or BranchExecutor(self.root_builder)
        self.cm_numbering_tool = getattr(
            self.branch_executor,
            "cm_numbering_tool",
            CMNumberingTool(),
        )
        self.hard_guards = hard_guards or HardGuards()
        self.online_ranker = online_ranker or OnlineRanker()
        self.special_route_service = special_route_service or SpecialRouteEvidenceService()
        self.scheduler = scheduler or ParallelBranchScheduler(
            branch_executor=self.branch_executor,
            difficulty_assessor=self.difficulty_assessor,
            route_assessor=self.route_assessor,
            hard_guards=self.hard_guards,
            online_ranker=self.online_ranker,
        )

    def run(self, request: AlignmentRequest) -> SearchResult:
        emit_progress(request, {"stage": "input_classified"})
        special_evidence = self._run_special_preflight(request)
        if special_evidence is not None:
            return self._result_from_special_evidence(request, special_evidence)
        root_trace_prefix: List[Dict[str, Any]] = [
            {
                "stage": "input_classified",
                "input_type": request.input_type,
                "domain_hint": request.domain_hint,
                "trna_class": request.trna_class,
                "intron_status": request.intron_status,
                "input_contract": self._input_contract_observation(request),
            }
        ]
        (
            root_snapshot,
            initial_trace,
            initial_failures,
            initial_planner_calls,
        ) = self._initialize_root_from_agent(request)
        root_trace_prefix.extend(initial_trace)
        if root_snapshot is None:
            return self._failed_initial_tools_result(
                request,
                failures=initial_failures,
                trace_events=root_trace_prefix,
            )
        root_diagnostics = self.difficulty_assessor.build_diagnostics(root_snapshot)
        root_difficulty = self.difficulty_assessor.assess(root_diagnostics, root_snapshot)
        root_route = self.route_assessor.assess(root_snapshot, root_diagnostics)
        root_guard = self.hard_guards.validate(request, root_snapshot)
        root_score = self.online_ranker.score(root_snapshot, root_diagnostics, root_route, root_guard)
        root_search_summaries: List[Dict[str, Any]] = []
        root_search_events: List[Dict[str, Any]] = []

        if not self._planning_enabled(request):
            stop_reason = (
                "use_llm_false_root_only"
                if "AGENT_ENABLE_PLANNING" not in dict(request.runtime_values_override or {})
                else "planning_disabled_root_only"
            )
            return SearchResult(
                root_snapshot=root_snapshot,
                best_snapshot=root_snapshot,
                diagnostics=root_diagnostics,
                difficulty=root_difficulty,
                route=root_route,
                root_diagnostics=root_diagnostics,
                root_difficulty=root_difficulty,
                root_route=root_route,
                expanded=False,
                trace_events=[
                    *root_trace_prefix,
                    {
                        "stage": "root_snapshot_built",
                        "root_source": root_snapshot.root_source,
                        "template_name": root_snapshot.template_name,
                    },
                    {
                        "stage": "search_stopped",
                        "reason": stop_reason,
                    },
                ],
                branch_summaries=[
                    {
                        "snapshot_id": root_snapshot.snapshot_id,
                        "source_action": root_snapshot.source_action,
                        "template_name": root_snapshot.template_name,
                        "route": root_route.route,
                        "route_confidence": root_route.confidence,
                        "in_canonical_benchmark": root_route.in_canonical_benchmark,
                        "out_of_scope": root_route.out_of_scope,
                        "difficulty_tier": root_difficulty.tier,
                        "difficulty_score": root_difficulty.difficulty_score,
                        "hard_valid": root_guard.valid,
                        "score": root_score,
                        "reasons": list(root_guard.reasons),
                    }
                ],
                context_summary={},
                memory_hits=[],
                work_state_summary={
                    "stop_reason": stop_reason,
                    "best_score": float(root_score),
                    "best_valid": bool(root_guard.valid),
                    "best_reasons": list(root_guard.reasons),
                    "disposition": self._guard_disposition(root_snapshot, root_guard),
                },
            )

        (
            root_snapshot,
            root_diagnostics,
            root_difficulty,
            root_route,
            root_guard,
            root_score,
            root_search_summaries,
            root_search_events,
        ) = self._maybe_select_better_root(
            request=request,
            root_snapshot=root_snapshot,
            root_diagnostics=root_diagnostics,
            root_difficulty=root_difficulty,
            root_route=root_route,
            root_guard=root_guard,
            root_score=root_score,
        )

        diagnostics = root_diagnostics
        difficulty = root_difficulty
        route = root_route
        best_snapshot = root_snapshot
        branch_summaries = [
            {
                "snapshot_id": root_snapshot.snapshot_id,
                "source_action": root_snapshot.source_action,
                "template_name": root_snapshot.template_name,
                "route": root_route.route,
                "route_confidence": root_route.confidence,
                "in_canonical_benchmark": root_route.in_canonical_benchmark,
                "out_of_scope": root_route.out_of_scope,
                "difficulty_tier": root_difficulty.tier,
                "difficulty_score": root_difficulty.difficulty_score,
                "hard_valid": root_guard.valid,
                "score": root_score,
                "reasons": list(root_guard.reasons),
            }
        ]
        trace_events = [
            *root_trace_prefix,
            {
                "stage": "root_snapshot_built",
                "root_source": root_snapshot.root_source,
                "template_name": root_snapshot.template_name,
            },
            {
                "stage": "difficulty_assessed",
                "tier": root_difficulty.tier,
                "hard_case": root_difficulty.hard_case,
                "difficulty_score": root_difficulty.difficulty_score,
                "reasons": list(root_difficulty.reasons),
            },
            {
                "stage": "route_assessed",
                "route": root_route.route,
                "confidence": root_route.confidence,
                "out_of_scope": root_route.out_of_scope,
                "reasons": list(root_route.reasons),
            },
        ]
        if root_search_summaries:
            branch_summaries.extend(root_search_summaries)
        for event in trace_events[len(root_trace_prefix):]:
            emit_progress(request, event)
        if root_search_events:
            extend_trace(request, trace_events, root_search_events)
        work_state = AgentWorkState.from_root(
            input_sequence=request.normalized_target_seq(),
            request_flags={
                "use_llm": bool(request.use_llm),
                "llm_reasoning": bool(request.llm_reasoning),
                "template_name": str(request.template_name or ""),
                "runtime_values_override": dict(request.runtime_values_override or {}),
            },
            snapshot=root_snapshot,
            diagnostics=root_diagnostics,
            difficulty=root_difficulty,
            route=root_route,
            valid=bool(root_guard.valid),
            reasons=list(root_guard.reasons),
            score=float(root_score),
            budget=self._build_budget(request),
            issues=self._infer_issues(root_snapshot, root_diagnostics, list(root_guard.reasons)),
        )
        work_state.budget.used_planner_calls = int(initial_planner_calls)
        expanded = False
        context_summary: Dict[str, Any] = {}
        memory_hits: List[Dict[str, Any]] = []
        planning_round = 0
        reviewed_soft_risk_codes: set[str] = set()
        observed_soft_risk_evidence: List[Dict[str, Any]] = []
        soft_review_decisions: List[Dict[str, Any]] = []
        current_soft_risk_evidence = self._soft_risk_evidence(request, work_state)
        self._merge_soft_risk_evidence(
            observed_soft_risk_evidence,
            current_soft_risk_evidence,
        )
        soft_risk_evidence = self._next_soft_risk_review_batch(
            current_soft_risk_evidence,
            reviewed_codes=reviewed_soft_risk_codes,
        )
        soft_review_pending = bool(soft_risk_evidence)
        soft_review_decision: Dict[str, Any] = {}
        if soft_review_pending:
            record_trace(request, trace_events,
                {
                    "stage": "soft_risk_review_scheduled",
                    "candidate_valid": True,
                    "reasons": list(soft_risk_evidence),
                    "policy": "escalation_only_with_original_candidate_rollback",
                }
            )
        # Carried across rounds: the answer the model gave before
        # the Harness required one attempt at a repair it was offered.
        deferred_decision: PlannerDecision | None = None
        force_tool_round = False
        while True:
            if not soft_review_pending:
                current_soft_risk_evidence = self._soft_risk_evidence(
                    request,
                    work_state,
                )
                self._merge_soft_risk_evidence(
                    observed_soft_risk_evidence,
                    current_soft_risk_evidence,
                )
                soft_risk_evidence = self._next_soft_risk_review_batch(
                    current_soft_risk_evidence,
                    reviewed_codes=reviewed_soft_risk_codes,
                )
                soft_review_pending = bool(soft_risk_evidence)
                if soft_review_pending:
                    record_trace(request, trace_events,
                        {
                            "stage": "soft_risk_review_scheduled",
                            "candidate_valid": bool(work_state.current_valid),
                            "reasons": list(soft_risk_evidence),
                            "policy": (
                                "sequential_evidence_review_with_original_candidate_rollback"
                            ),
                            "review_index": len(soft_review_decisions) + 1,
                        }
                    )
            triage_mode = self._llm_triage_mode(request)
            stop_reason = self._stop_reason(
                work_state,
                allow_quality_stop=(
                    not soft_review_pending
                    and (
                        not bool(request.use_llm)
                        or triage_mode in {"uncertain_only", "dual_trigger"}
                    )
                ),
            )
            if stop_reason:
                work_state.stop_reason = stop_reason
                if (
                    request.use_llm
                    and triage_mode in {"uncertain_only", "dual_trigger"}
                    and stop_reason in {"legal_and_no_open_issues", "easy_and_stable"}
                ):
                    record_trace(request, trace_events,
                        {
                            "stage": "harness_fast_path_accepted",
                            "reason": stop_reason,
                            "llm_called": False,
                            "candidate_valid": bool(work_state.current_valid),
                        }
                    )
                record_trace(request, trace_events,
                    {
                        "stage": "search_stopped",
                        "reason": stop_reason,
                        "work_state": work_state.summary(),
                    }
                )
                break
            if work_state.current_route.out_of_scope:
                work_state.stop_reason = "route_out_of_scope"
                record_trace(request, trace_events,
                    {
                        "stage": "search_stopped",
                        "reason": "route_out_of_scope",
                        "work_state": work_state.summary(),
                    }
                )
                break

            if hasattr(self.planner, "plan_from_work_state"):
                context, memory_hits, actions = self.planner.plan_from_work_state(
                    request=request,
                    work_state=work_state,
                )
            else:
                context, memory_hits, actions = self.planner.plan(
                    request,
                    work_state.current_snapshot,
                    work_state.current_diagnostics,
                    work_state.current_difficulty,
                    work_state.current_route,
                    work_state.current_reasons,
                )
            actions = [
                action
                for action in actions
                if self._component_action_enabled(context, action)
                and self._can_execute_action(work_state, action)
            ]
            if soft_review_pending:
                actions = self._with_soft_risk_actions(
                    actions,
                    work_state=work_state,
                    evidence=soft_risk_evidence,
                )
            allowed_outcomes = self._allowed_agent_outcomes(work_state)
            phase = "soft_risk_review" if soft_review_pending else "evidence_review"
            emit_progress(request, {"stage": "evidence_review"})
            observation = self._planner_observation(
                request=request,
                work_state=work_state,
                context_summary=context.summary(),
            )
            if soft_review_pending:
                observation["soft_risk_review"] = {
                    "reasons": list(soft_risk_evidence),
                    "allowed_effect": "keep_current_candidate_or_request_allowed_tool",
                    "original_candidate_preserved": True,
                }
            # A punt on the first look is the cheapest decision an Agent can
            # make, and on 2530 cases it is the most common one: 1219 of them
            # ended without a single action.  So escalation is deferred once
            # while a repair that nobody tried is still on the list, and the
            # same round is asked again with only the tools available.
            if (
                force_tool_round
                and self._untried_repair_actions(work_state, actions)
            ):
                allowed_outcomes = []
                observation["escalation_policy"] = {
                    "reason": "repair_offered_but_not_attempted",
                    "untried_actions": [
                        action.action_type
                        for action in self._untried_repair_actions(work_state, actions)
                    ],
                    "allowed_effect": "choose_one_action_id",
                }
                phase = "repair_required"
            decision = self.action_planner.decide(
                request=request,
                phase=phase,
                observation=observation,
                actions=actions,
                allowed_outcomes=allowed_outcomes,
            )
            if (
                decision.decision != "tool"
                and force_tool_round
                and deferred_decision is not None
            ):
                # The forced round still refused.  Honour what the model first
                # asked for instead of inventing an outcome it never chose.
                decision = deferred_decision
                force_tool_round = False
                deferred_decision = None
            # The round has been asked; a tool answer here means the attempt
            # happened, and any later refusal is the model's own again.
            force_tool_round = False
            work_state.budget.used_planner_calls += int(bool(decision.llm_called))
            planning_round += 1
            work_state.memory_hits = list(memory_hits)
            context_summary = context.summary()
            review_was_pending = soft_review_pending
            soft_review_pending = False
            if review_was_pending:
                soft_review_decision = {
                    **decision.trace(),
                    "reasons": list(soft_risk_evidence),
                }
                soft_review_decisions.append(dict(soft_review_decision))
            record_trace(request, trace_events,
                {
                    "stage": "planning",
                    "round": planning_round,
                    "phase": phase,
                    "context_summary": context_summary,
                    "memory_hit_count": len(memory_hits),
                    "agent_decision": decision.trace(),
                    "soft_risk_review": (
                        {
                            "reasons": list(soft_risk_evidence),
                            "original_candidate_preserved": True,
                        }
                        if review_was_pending
                        else {}
                    ),
                    "planned_actions": [
                        {
                            "action_id": action.action_id,
                            "action_type": action.action_type,
                            "specialist": action.specialist,
                            "scope": action.scope,
                            "target_region": action.target_region,
                            "params": dict(action.params or {}),
                            "reason": action.reason,
                            "execution_class": self._action_execution_class(action),
                            "budget": {
                                "llm_calls_reserved": int(
                                    action.budget_cost.llm_calls_reserved
                                ),
                                "expected_wall_time_sec": int(
                                    action.budget_cost.expected_wall_time_sec
                                ),
                            },
                        }
                        for action in actions
                    ],
                }
            )
            if decision.decision != "tool":
                untried = self._untried_repair_actions(work_state, actions)
                if (
                    untried
                    and decision.decision in {"investigate", "abstain"}
                    and work_state.escalation_deferrals < 1
                    and self._escalation_requires_attempt(request)
                ):
                    work_state.escalation_deferrals += 1
                    deferred_decision = decision
                    force_tool_round = True
                    record_trace(request, trace_events,
                        {
                            "stage": "escalation_deferred_pending_attempt",
                            "round": planning_round,
                            "requested_disposition": decision.decision,
                            "reason_code": decision.reason_code,
                            "untried_actions": [
                                action.action_type for action in untried
                            ],
                            "work_state": work_state.summary(),
                        },
                    )
                    emit_progress(
                        request,
                        {
                            "stage": "escalation_deferred_pending_attempt",
                            "untried_actions": [
                                action.action_type for action in untried
                            ],
                        },
                    )
                    continue
                if review_was_pending and decision.decision == "accept":
                    retained_codes = {
                        str(item.get("code") or "")
                        for item in soft_risk_evidence
                        if str(item.get("code") or "")
                    }
                    reviewed_soft_risk_codes.update(retained_codes)
                    record_trace(request, trace_events,
                        {
                            "stage": "soft_risk_candidate_retained",
                            "round": planning_round,
                            "reason_codes": sorted(retained_codes),
                            "disposition": "retained_current_candidate",
                            **decision.trace(),
                            "work_state": work_state.summary(),
                        }
                    )
                    continue
                work_state.stop_reason = f"agent_{decision.decision}"
                record_trace(request, trace_events,
                    {
                        "stage": "agent_disposition_requested",
                        "round": planning_round,
                        "disposition": decision.decision,
                        **decision.trace(),
                        "work_state": work_state.summary(),
                    }
                )
                break
            chosen_action = next(
                (action for action in actions if action.action_id == decision.action_id),
                None,
            )
            if chosen_action is None:
                work_state.stop_reason = "no_actionable_actions"
                record_trace(request, trace_events,
                    {
                        "stage": "search_stopped",
                        "reason": "no_actionable_actions",
                        "work_state": work_state.summary(),
                    }
                )
                break

            if review_was_pending:
                addressed_codes = {
                    str(code)
                    for code in list(
                        dict(chosen_action.params or {}).get(
                            "soft_risk_reason_codes"
                        )
                        or []
                    )
                    if str(code)
                }
                if not addressed_codes:
                    addressed_codes = {
                        str(item.get("code") or "")
                        for item in soft_risk_evidence
                        if str(item.get("code") or "")
                    }
                reviewed_soft_risk_codes.update(addressed_codes)

            expanded = True
            before_score = float(work_state.current_score)
            before_valid = bool(work_state.current_valid)
            parent_snapshot_id = str(work_state.current_snapshot.snapshot_id or "")
            parent_template_name = str(work_state.current_snapshot.template_name or "")
            parent_numbering_backend = dict(
                work_state.current_snapshot.raw_meta or {}
            ).get("numbering_backend")
            signature = self._action_signature(chosen_action)
            action_context_signature = answer_free_context_signature(context_summary)
            emit_progress(request, {"stage": "tool_started", "tool": chosen_action.action_type, "action_id": chosen_action.action_id})
            if chosen_action.action_type == "search_literature":
                # A reference note answers a question about the biology; it does
                # not move a coordinate.  The candidate, its score and its guard
                # result stay exactly as they were, and the next planning round
                # sees the note in the observation.
                self._apply_literature_action(
                    request=request,
                    action=chosen_action,
                    work_state=work_state,
                    observation=observation,
                    context_summary=context_summary,
                    parent_snapshot_id=parent_snapshot_id,
                    before_score=before_score,
                    before_valid=before_valid,
                    signature=signature,
                    action_context_signature=action_context_signature,
                    trace_events=trace_events,
                    planning_round=planning_round,
                    branch_summaries=branch_summaries,
                )
                continue
            try:
                candidate_snapshot = self._execute_planned_action(
                    request=request,
                    parent_snapshot=work_state.current_snapshot,
                    action=chosen_action,
                    branch_id=f"step-{work_state.budget.used_action_steps + 1:04d}",
                    work_state=work_state,
                )
            except Exception as exc:
                self._consume_budget(work_state, chosen_action)
                work_state.attempted_signatures.append(signature)
                failure_note = f"tool_error:{type(exc).__name__}:{exc}"
                work_state.action_history.append(
                    ActionRecord(
                        action_id=str(chosen_action.action_id or ""),
                        action_type=str(chosen_action.action_type or ""),
                        context_signature=action_context_signature,
                        target_regions=self._action_regions(chosen_action),
                        accepted=False,
                        score_before=before_score,
                        score_after=before_score,
                        valid_before=before_valid,
                        valid_after=False,
                        notes=[failure_note],
                    )
                )
                self._register_no_gain(
                    work_state,
                    before_score=before_score,
                    after_score=before_score,
                )
                record_trace(request, trace_events,
                    {
                        "stage": "action_failed",
                        "round": planning_round,
                        "action_id": chosen_action.action_id,
                        "action_type": chosen_action.action_type,
                        "tool_name": (
                            "align_numbering_cm"
                            if chosen_action.action_type == "align_numbering_cm"
                            else chosen_action.action_type
                        ),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "disposition": "rejected",
                        "parent_snapshot_id": parent_snapshot_id,
                        "work_state": work_state.summary(),
                    }
                )
                continue
            (
                candidate_diagnostics,
                candidate_difficulty,
                candidate_route,
                candidate_guard,
                candidate_score,
            ) = self._evaluate_snapshot(request, candidate_snapshot)
            minimum_score_gain = self._candidate_minimum_score_gain(
                request=request,
                parent_snapshot=work_state.current_snapshot,
                candidate_snapshot=candidate_snapshot,
                action=chosen_action,
            )
            score_gain = float(candidate_score) - before_score
            acceptor_endpoint_precedence = (
                self._accept_acceptor_endpoint_candidate(
                    action=chosen_action,
                    candidate_snapshot=candidate_snapshot,
                    candidate_valid=bool(candidate_guard.valid),
                    candidate_reasons=list(candidate_guard.reasons),
                    parent_reasons=list(work_state.current_reasons or []),
                )
            )
            acceptor_template_precedence = (
                self._accept_acceptor_template_consensus_candidate(
                    action=chosen_action,
                    candidate_snapshot=candidate_snapshot,
                    candidate_valid=bool(candidate_guard.valid),
                )
            )
            variable_boundary_precedence = (
                self._accept_variable_boundary_candidate(
                    action=chosen_action,
                    candidate_snapshot=candidate_snapshot,
                    candidate_valid=bool(candidate_guard.valid),
                )
            )
            accepted = (
                acceptor_endpoint_precedence
                or acceptor_template_precedence
                or variable_boundary_precedence
                or self._should_accept_candidate(
                    current_valid=before_valid,
                    current_score=before_score,
                    candidate_valid=bool(candidate_guard.valid),
                    candidate_score=float(candidate_score),
                    minimum_score_gain=minimum_score_gain,
                )
            )
            self._consume_budget(work_state, chosen_action)
            work_state.attempted_signatures.append(signature)
            candidate_notes = list(dict(candidate_snapshot.raw_meta or {}).get("agent_branch", {}).get("notes") or [])
            work_state.action_history.append(
                ActionRecord(
                    action_id=str(chosen_action.action_id or ""),
                    action_type=str(chosen_action.action_type or ""),
                    context_signature=action_context_signature,
                    target_regions=self._action_regions(chosen_action),
                    accepted=bool(accepted),
                    score_before=before_score,
                    score_after=float(candidate_score),
                    valid_before=before_valid,
                    valid_after=bool(candidate_guard.valid),
                    notes=candidate_notes,
                )
            )
            self._update_region_edit_counts(work_state, chosen_action)
            if accepted:
                self._accept_current_state(
                    work_state,
                    snapshot=candidate_snapshot,
                    diagnostics=candidate_diagnostics,
                    difficulty=candidate_difficulty,
                    route=candidate_route,
                    valid=bool(candidate_guard.valid),
                    reasons=list(candidate_guard.reasons),
                    score=float(candidate_score),
                )
            else:
                self._register_no_gain(work_state, before_score=before_score, after_score=float(candidate_score))

            if (
                accepted
                and (
                    acceptor_endpoint_precedence
                    or acceptor_template_precedence
                    or variable_boundary_precedence
                    or float(candidate_score) > float(work_state.best_score)
                )
            ):
                self._accept_best_state(
                    work_state,
                    snapshot=candidate_snapshot,
                    diagnostics=candidate_diagnostics,
                    difficulty=candidate_difficulty,
                    route=candidate_route,
                    valid=bool(candidate_guard.valid),
                    reasons=list(candidate_guard.reasons),
                    score=float(candidate_score),
                )

            candidate_meta = dict(candidate_snapshot.raw_meta or {})
            candidate_comparison_policy = self._candidate_comparison_policy(
                candidate_snapshot
            )
            cm_tool_meta = dict(candidate_meta.get("cm_tool") or {})
            legacy_tool_meta = dict(candidate_meta.get("legacy_reference_tool") or {})
            coordinate_proposal_meta = dict(candidate_meta.get("coordinate_proposal") or {})
            if chosen_action.action_type == "propose_coordinate_revision":
                # Whatever the guards said about this row is the only feedback
                # the model can act on, so it is carried into the next proposal.
                work_state.coordinate_proposal_attempts.append(
                    {
                        "round": int(planning_round),
                        "accepted": bool(accepted),
                        "status": str(coordinate_proposal_meta.get("status") or ""),
                        "changed_count": int(
                            coordinate_proposal_meta.get("changed_count") or 0
                        ),
                        "proposal_error": str(coordinate_proposal_meta.get("error") or ""),
                        "violations": []
                        if accepted
                        else list(candidate_guard.reasons),
                    }
                )
            acceptor_tool_meta = dict(
                candidate_meta.get("acceptor_tail_reconciliation") or {}
            )
            acceptor_template_tool_meta = dict(
                candidate_meta.get("acceptor_tail_template_consensus") or {}
            )
            variable_boundary_tool_meta = dict(
                candidate_meta.get("variable_boundary_reconciliation") or {}
            )
            multi_template_reference_search = dict(
                candidate_meta.get("multi_template_reference_search") or {}
            )
            if (
                chosen_action.action_type == "repair_acceptor_tail_with_structure"
                and not accepted
                and str(acceptor_tool_meta.get("status") or "") == "unresolved"
                and str(
                    dict(acceptor_tool_meta.get("result") or {}).get("reason")
                    or ""
                )
                == "acceptor_pairs_do_not_define_one_mirrored_seven_residue_arm"
            ):
                # The coordinate candidate is rolled back, but the scientific
                # tool observation remains part of the Agent state. It opens
                # the distinct-template endpoint consensus action next round.
                current_meta = dict(work_state.current_snapshot.raw_meta or {})
                current_meta["acceptor_tail_reconciliation"] = deepcopy(
                    acceptor_tool_meta
                )
                work_state.current_snapshot.raw_meta = current_meta
                reviewed_soft_risk_codes.discard(
                    "cm_acceptor_tail_boundary_ambiguity"
                )
            cm_portfolio = dict(candidate_meta.get("cm_portfolio") or {})
            scientific_contract = dict(
                candidate_meta.get("regional_candidate_contract")
                or candidate_meta.get("boundary_refinement_contract")
                or candidate_meta.get("acceptor_tail_reconciliation")
                or candidate_meta.get("acceptor_tail_template_consensus")
                or candidate_meta.get("variable_boundary_reconciliation")
                or {}
            )
            branch_summary = {
                "snapshot_id": candidate_snapshot.snapshot_id,
                "source_action": candidate_snapshot.source_action,
                "template_name": candidate_snapshot.template_name,
                "route": candidate_route.route,
                "route_confidence": candidate_route.confidence,
                "in_canonical_benchmark": candidate_route.in_canonical_benchmark,
                "out_of_scope": candidate_route.out_of_scope,
                "difficulty_tier": candidate_difficulty.tier,
                "difficulty_score": candidate_difficulty.difficulty_score,
                "hard_valid": candidate_guard.valid,
                "score": candidate_score,
                "reasons": list(candidate_guard.reasons),
                "accepted_as_current": bool(accepted),
                "numbering_backend": candidate_meta.get("numbering_backend"),
                "cm_model_group": dict(candidate_meta.get("numbering_model") or {}).get("group"),
                "cm_raw_bit_score_margin": cm_portfolio.get("raw_bit_score_margin"),
                "cm_score_margin_calibrated": cm_portfolio.get("score_margin_calibrated"),
                "scientific_contract": scientific_contract,
                "multi_template_reference_search": multi_template_reference_search,
                "acceptor_tail_reconciliation": acceptor_tool_meta,
                "acceptor_tail_template_consensus": acceptor_template_tool_meta,
                "variable_boundary_reconciliation": variable_boundary_tool_meta,
                "coordinate_proposal": coordinate_proposal_meta,
                "candidate_comparison": {
                    "policy": candidate_comparison_policy,
                    "score_gain": round(score_gain, 4),
                    "minimum_score_gain": round(minimum_score_gain, 4),
                    "passed": bool(accepted),
                    "acceptor_endpoint_precedence": bool(
                        acceptor_endpoint_precedence
                    ),
                    "acceptor_template_precedence": bool(
                        acceptor_template_precedence
                    ),
                    "variable_boundary_precedence": bool(
                        variable_boundary_precedence
                    ),
                    "parent_backend": parent_numbering_backend,
                    "candidate_backend": candidate_meta.get("numbering_backend"),
                },
            }
            branch_summaries.append(branch_summary)
            record_trace(request, trace_events,
                {
                    "stage": "action_executed",
                    "round": planning_round,
                    "action_id": chosen_action.action_id,
                    "action_type": chosen_action.action_type,
                    "accepted_as_current": bool(accepted),
                    "candidate_snapshot_id": candidate_snapshot.snapshot_id,
                    "candidate_template_name": candidate_snapshot.template_name,
                    "candidate_score": float(candidate_score),
                    "candidate_valid": bool(candidate_guard.valid),
                    "candidate_reasons": list(candidate_guard.reasons),
                    "disposition": (
                        "accepted"
                        if accepted and candidate_guard.valid
                        else (
                            "accepted_for_continuation"
                            if accepted
                            else "rejected"
                        )
                    ),
                    "tool_name": (
                        variable_boundary_tool_meta.get("tool_name")
                        or acceptor_template_tool_meta.get("tool_name")
                        or acceptor_tool_meta.get("tool_name")
                        or cm_tool_meta.get("tool_name")
                        or legacy_tool_meta.get("tool_name")
                        or chosen_action.action_type
                    ),
                    "tool_provenance": (
                        dict(
                            variable_boundary_tool_meta.get("tool_observation")
                            or {}
                        )
                        or dict(
                            acceptor_template_tool_meta.get("tool_observation")
                            or {}
                        )
                        or dict(acceptor_tool_meta.get("tool_observation") or {})
                        or dict(cm_tool_meta.get("tool_provenance") or {})
                        or legacy_tool_meta
                    ),
                    "tool_observations": (
                        [dict(variable_boundary_tool_meta.get("result") or {})]
                        if variable_boundary_tool_meta
                        else list(
                            acceptor_template_tool_meta.get("candidates") or []
                        )
                        if acceptor_template_tool_meta
                        else [dict(acceptor_tool_meta.get("result") or {})]
                        if acceptor_tool_meta
                        else list(cm_tool_meta.get("trace_events") or [])
                    ),
                    "scientific_contract": scientific_contract,
                    "multi_template_reference_search": multi_template_reference_search,
                    "acceptor_tail_reconciliation": acceptor_tool_meta,
                    "acceptor_tail_template_consensus": (
                        acceptor_template_tool_meta
                    ),
                    "variable_boundary_reconciliation": (
                        variable_boundary_tool_meta
                    ),
                    "coordinate_proposal": coordinate_proposal_meta,
                    "candidate_comparison": {
                        "policy": candidate_comparison_policy,
                        "score_gain": round(score_gain, 4),
                        "minimum_score_gain": round(minimum_score_gain, 4),
                        "passed": bool(accepted),
                        "acceptor_endpoint_precedence": bool(
                            acceptor_endpoint_precedence
                        ),
                        "acceptor_template_precedence": bool(
                            acceptor_template_precedence
                        ),
                        "variable_boundary_precedence": bool(
                            variable_boundary_precedence
                        ),
                        "parent_backend": parent_numbering_backend,
                        "candidate_backend": candidate_meta.get(
                            "numbering_backend"
                        ),
                    },
                    "candidate_change_summary": {
                        "parent_snapshot_id": parent_snapshot_id,
                        "parent_template_name": parent_template_name,
                        "numbering_backend": candidate_meta.get("numbering_backend"),
                        "selected_model": dict(candidate_meta.get("numbering_model") or {}).get("name"),
                        "residue_count": len(candidate_snapshot.sequence_row),
                    },
                    "work_state": work_state.summary(),
                }
            )

        best_snapshot = work_state.best_snapshot
        diagnostics = work_state.best_diagnostics
        difficulty = work_state.best_difficulty
        route = work_state.best_route

        work_state_summary = work_state.summary()
        work_state_summary["soft_risk_review"] = {
            "eligible": bool(observed_soft_risk_evidence),
            "reasons": list(observed_soft_risk_evidence),
            "decision": dict(soft_review_decision),
            "decisions": list(soft_review_decisions),
            "reviewed_reason_codes": sorted(reviewed_soft_risk_codes),
            "original_candidate_preserved": True,
        }
        best_guard = self.hard_guards.validate(request, best_snapshot)
        work_state_summary["disposition"] = self._guard_disposition(
            best_snapshot,
            best_guard,
        )
        return SearchResult(
            root_snapshot=root_snapshot,
            best_snapshot=best_snapshot,
            diagnostics=diagnostics,
            difficulty=difficulty,
            route=route,
            root_diagnostics=root_diagnostics,
            root_difficulty=root_difficulty,
            root_route=root_route,
            expanded=expanded,
            trace_events=trace_events,
            branch_summaries=branch_summaries,
            context_summary=context_summary,
            memory_hits=list(work_state.memory_hits),
            work_state_summary=work_state_summary,
        )

    def _execute_planned_action(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState | None,
    ) -> RootSnapshot:
        if action.action_type == "reconcile_variable_boundary_with_structure":
            return self._execute_variable_boundary_multi_evidence_search(
                request=request,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                work_state=work_state,
            )
        if action.action_type == "reconcile_acceptor_tail_with_template_consensus":
            return self._execute_acceptor_tail_template_consensus_search(
                request=request,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                work_state=work_state,
            )
        multi_template_reference_search = str(
            dict(action.params or {}).get("multi_template_reference_search", False)
        ).strip().lower() in {"1", "true", "yes", "on"}
        if (
            action.action_type == "align_legacy_reference"
            and multi_template_reference_search
        ):
            return self._execute_multi_template_reference_search(
                request=request,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                work_state=work_state,
            )
        return self.branch_executor.execute(
            request=request,
            parent_snapshot=parent_snapshot,
            action=action,
            branch_id=branch_id,
            work_state=work_state,
        )

    @staticmethod
    def _acceptor_tail_coordinate_signature(labels: List[str]) -> tuple[str, ...]:
        signature: List[str] = []
        for label in labels:
            match = re.match(r"^(?P<parent>6[6-9]|7[0-6])(?:i\d+)?$", label)
            if match:
                signature.append(str(label))
        return tuple(signature)

    @staticmethod
    def _acceptor_tail_changed_residue_indices(
        parent_labels: List[str],
        candidate_labels: List[str],
    ) -> List[int]:
        if len(parent_labels) != len(candidate_labels):
            return []
        return [
            index
            for index, (parent, candidate) in enumerate(
                zip(parent_labels, candidate_labels), start=1
            )
            if parent != candidate
        ]

    def _execute_acceptor_tail_template_consensus_search(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState,
    ) -> RootSnapshot:
        values = dict(request.runtime_values_override or {})
        try:
            top_k = int(
                values.get("AGENT_ACCEPTOR_TAIL_REFERENCE_TOP_K", 5)
            )
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(3, min(top_k, 10))
        try:
            minimum_support = int(
                values.get("AGENT_ACCEPTOR_TAIL_MIN_REFERENCE_SUPPORT", 3)
            )
        except (TypeError, ValueError):
            minimum_support = 3
        minimum_support = max(3, min(minimum_support, top_k))

        reference_action = CandidateAction(
            action_id=f"{action.action_id}:reference-consensus",
            action_type="align_legacy_reference",
            specialist="acceptor_tail_reference_reviewer",
            scope="snapshot",
            params={"template_top_k": top_k},
            reason=(
                "acceptor_tail_reference_reviewer:request_distinct_"
                "template_endpoint_consensus"
            ),
            allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
            success_condition=(
                "distinct_nonidentical_templates_agree_on_the_66_76_"
                "coordinate_partition_and_candidate_passes_shared_guards"
            ),
            rollback_condition=(
                "retain_parent_when_endpoint_consensus_is_not_established"
            ),
        )
        contract = {
            "policy": "distinct_template_acceptor_tail_consensus_v1",
            "top_k": top_k,
            "minimum_support": minimum_support,
            "exact_input_templates_excluded": True,
            "answer_free": True,
            "gold_coordinates_used": False,
        }
        try:
            baseline = self.branch_executor.execute(
                request=request,
                parent_snapshot=parent_snapshot,
                action=reference_action,
                branch_id=f"{branch_id}-reference-01",
                work_state=work_state,
            )
        except Exception as exc:
            retained = deepcopy(parent_snapshot)
            retained.raw_meta = dict(retained.raw_meta or {})
            retained.raw_meta["acceptor_tail_template_consensus"] = {
                **contract,
                "status": "failed",
                "established": False,
                "tool_error_type": type(exc).__name__,
                "candidates": [],
            }
            return self._retag_composite_candidate(
                retained,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                note="acceptor_tail_template_consensus_failed_parent_retained",
            )

        template_names: List[str] = []
        for raw_name in [
            baseline.template_name,
            *[
                str(item.get("seqname") or "")
                for item in list(baseline.template_candidates or [])
            ],
        ]:
            name = str(raw_name or "").strip()
            if name and name not in template_names:
                template_names.append(name)
            if len(template_names) >= top_k:
                break

        parent_labels, parent_bases = self._occupied_snapshot_rows(parent_snapshot)
        snapshot_records: List[tuple[RootSnapshot, Dict[str, Any]]] = []
        records: List[Dict[str, Any]] = []
        for index, template_name in enumerate(template_names, start=1):
            if template_name == baseline.template_name:
                snapshot = baseline
            else:
                probe_action = deepcopy(reference_action)
                probe_action.params = {
                    **dict(probe_action.params or {}),
                    "template_name": template_name,
                }
                try:
                    snapshot = self.branch_executor.execute(
                        request=request,
                        parent_snapshot=parent_snapshot,
                        action=probe_action,
                        branch_id=f"{branch_id}-reference-{index:02d}",
                        work_state=work_state,
                    )
                except Exception as exc:
                    records.append(
                        {
                            "template_name": template_name,
                            "tool_status": "failed",
                            "tool_error_type": type(exc).__name__,
                            "hard_valid": False,
                            "base_preserved": False,
                            "eligible_support": False,
                        }
                    )
                    continue

            candidate_labels, candidate_bases = self._occupied_snapshot_rows(
                snapshot
            )
            try:
                guard = self.hard_guards.validate(request, snapshot)
                hard_valid = bool(guard.valid)
                guard_reasons = list(guard.reasons)
            except Exception as exc:
                hard_valid = False
                guard_reasons = [f"guard_error:{type(exc).__name__}"]
            base_preserved = candidate_bases == parent_bases
            template_sequence = "".join(
                str(base or "").strip().upper().replace("T", "U")
                for base in list(snapshot.template_row or [])
                if str(base or "").strip().upper().replace("T", "U")
                in {"A", "C", "G", "U", "N"}
            )
            template_sha256 = (
                hashlib.sha256(template_sequence.encode("ascii")).hexdigest()
                if template_sequence
                else ""
            )
            exact_input_template = bool(
                template_sequence and template_sequence == "".join(parent_bases)
            )
            signature = self._acceptor_tail_coordinate_signature(candidate_labels)
            changed_indices = self._acceptor_tail_changed_residue_indices(
                parent_labels,
                candidate_labels,
            )
            eligible_support = bool(
                hard_valid
                and base_preserved
                and not exact_input_template
                and len(signature) >= 1
                and changed_indices
            )
            records.append(
                {
                    "template_name": str(snapshot.template_name or template_name),
                    "template_sequence_sha256": template_sha256,
                    "exact_input_template": exact_input_template,
                    "hard_valid": hard_valid,
                    "hard_guard_reasons": guard_reasons,
                    "base_preserved": base_preserved,
                    "tail_coordinate_signature": list(signature),
                    "changed_residue_indices": changed_indices,
                    "eligible_support": eligible_support,
                }
            )
            snapshot_records.append((snapshot, records[-1]))

        groups: Dict[tuple[str, ...], List[tuple[RootSnapshot, Dict[str, Any]]]] = {}
        for snapshot, record in snapshot_records:
            if not record.get("eligible_support"):
                continue
            signature = tuple(record.get("tail_coordinate_signature") or [])
            groups.setdefault(signature, []).append((snapshot, record))
        ranked_groups = sorted(
            groups.items(),
            key=lambda item: len(
                {
                    str(record.get("template_sequence_sha256") or "")
                    for _, record in item[1]
                    if record.get("template_sequence_sha256")
                }
            ),
            reverse=True,
        )
        consensus_signature = ranked_groups[0][0] if ranked_groups else ()
        consensus_group = ranked_groups[0][1] if ranked_groups else []
        support = len(
            {
                str(record.get("template_sequence_sha256") or "")
                for _, record in consensus_group
                if record.get("template_sequence_sha256")
            }
        )
        established = bool(
            support >= minimum_support and consensus_signature
        )
        audit = {
            **contract,
            "status": "resolved" if established else "unresolved",
            "established": established,
            "distinct_sequence_support": support,
            "candidate_count": len(records),
            "consensus_signature": list(consensus_signature),
            "candidates": records,
        }
        if not established:
            retained = deepcopy(parent_snapshot)
            retained.raw_meta = dict(retained.raw_meta or {})
            retained.raw_meta["acceptor_tail_template_consensus"] = audit
            return self._retag_composite_candidate(
                retained,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                note="acceptor_tail_template_consensus_not_established_parent_retained",
            )

        selected, selected_record = max(
            consensus_group,
            key=lambda item: (
                float(self._evaluate_snapshot(request, item[0])[4]),
                str(item[1].get("template_name") or ""),
            ),
        )
        audit["selected_template"] = str(selected.template_name or "")
        audit["selected_changed_residue_indices"] = list(
            selected_record.get("changed_residue_indices") or []
        )
        selected.raw_meta = dict(selected.raw_meta or {})
        selected.raw_meta["acceptor_tail_template_consensus"] = audit
        return self._retag_composite_candidate(
            selected,
            parent_snapshot=parent_snapshot,
            action=action,
            branch_id=branch_id,
            note="acceptor_tail_template_consensus_established",
        )

    def _execute_variable_boundary_multi_evidence_search(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState,
    ) -> RootSnapshot:
        structure_candidate = self.branch_executor.execute(
            request=request,
            parent_snapshot=parent_snapshot,
            action=action,
            branch_id=f"{branch_id}-structure",
            work_state=work_state,
        )
        structure_candidate.raw_meta = dict(structure_candidate.raw_meta or {})
        audit = dict(
            structure_candidate.raw_meta.get("variable_boundary_reconciliation")
            or {}
        )
        result = dict(audit.get("result") or {})
        if str(audit.get("status") or "") != (
            "structure_resolved_reference_pending"
        ):
            return self._retag_composite_candidate(
                structure_candidate,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                note="variable_boundary_structure_stage_completed",
            )

        values = dict(request.runtime_values_override or {})
        try:
            top_k = int(values.get("AGENT_VARIABLE_BOUNDARY_REFERENCE_TOP_K", 5))
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(2, min(top_k, 10))
        try:
            minimum_support = int(
                values.get("AGENT_VARIABLE_BOUNDARY_MIN_REFERENCE_SUPPORT", 2)
            )
        except (TypeError, ValueError):
            minimum_support = 2
        minimum_support = max(2, min(minimum_support, top_k))

        reference_action = CandidateAction(
            action_id=f"{action.action_id}:reference-consensus",
            action_type="align_legacy_reference",
            specialist="variable_boundary_reference_reviewer",
            scope="snapshot",
            params={"template_top_k": top_k},
            reason=(
                "variable_boundary_reference_reviewer:request_distinct_"
                "homologous_template_consensus"
            ),
            allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
            success_condition=(
                "distinct_nonidentical_reference_sequences_agree_with_the_"
                "independent_structure_projection"
            ),
            rollback_condition=(
                "retain_parent_when_reference_consensus_is_not_established"
            ),
        )
        consensus_contract = {
            "policy": "trnascan_plus_distinct_template_consensus_v1",
            "top_k": top_k,
            "minimum_support": minimum_support,
            "exact_input_templates_excluded": True,
            "answer_free": True,
            "gold_coordinates_used": False,
        }
        try:
            baseline = self.branch_executor.execute(
                request=request,
                parent_snapshot=parent_snapshot,
                action=reference_action,
                branch_id=f"{branch_id}-reference-01",
                work_state=work_state,
            )
        except Exception as exc:
            not_applicable = _reference_tool_not_applicable(exc)
            reason = (
                "variable_boundary_reference_tool_not_applicable"
                if not_applicable
                else "variable_boundary_reference_search_failed"
            )
            audit["reference_consensus"] = {
                **consensus_contract,
                "established": False,
                "distinct_sequence_support": 0,
                "candidate_count": 0,
                "candidates": [],
                "tool_status": "not_applicable" if not_applicable else "failed",
                "tool_error_type": type(exc).__name__,
            }
            return self._retain_variable_boundary_parent(
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                audit=audit,
                structure_proposal=result,
                reason=reason,
                note=f"{reason}_parent_retained",
            )
        template_names: List[str] = []
        for raw_name in [
            baseline.template_name,
            *[
                str(item.get("seqname") or "")
                for item in list(baseline.template_candidates or [])
            ],
        ]:
            template_name = str(raw_name or "").strip()
            if template_name and template_name not in template_names:
                template_names.append(template_name)
            if len(template_names) >= top_k:
                break

        structural_labels, structural_bases = self._occupied_snapshot_rows(
            structure_candidate
        )
        variable_span_indices = list(
            result.get("variable_span_residue_indices") or []
        )
        reference_records: List[Dict[str, Any]] = []
        supporting_signatures: set[str] = set()
        for index, template_name in enumerate(template_names, start=1):
            if template_name == baseline.template_name:
                reference = baseline
            else:
                probe_action = deepcopy(reference_action)
                probe_action.params = {
                    **dict(probe_action.params or {}),
                    "template_name": template_name,
                }
                try:
                    reference = self.branch_executor.execute(
                        request=request,
                        parent_snapshot=parent_snapshot,
                        action=probe_action,
                        branch_id=f"{branch_id}-reference-{index:02d}",
                        work_state=work_state,
                    )
                except Exception as exc:
                    not_applicable = _reference_tool_not_applicable(exc)
                    reference_records.append(
                        {
                            "template_name": template_name,
                            "tool_status": (
                                "not_applicable" if not_applicable else "failed"
                            ),
                            "tool_error_type": type(exc).__name__,
                            "hard_valid": False,
                            "base_preserved": False,
                            "boundary_partition_agreement": {
                                "agrees": False,
                                "reason": (
                                    "reference_tool_not_applicable"
                                    if not_applicable
                                    else "reference_tool_failed"
                                ),
                            },
                            "eligible_support": False,
                        }
                    )
                    continue

            reference_labels, reference_bases = self._occupied_snapshot_rows(reference)
            template_sequence = "".join(
                str(base or "").strip().upper().replace("T", "U")
                for base in list(reference.template_row or [])
                if str(base or "").strip().upper().replace("T", "U")
                in {"A", "C", "G", "U", "N"}
            )
            template_sha256 = (
                hashlib.sha256(template_sequence.encode("ascii")).hexdigest()
                if template_sequence
                else ""
            )
            exact_input_template = bool(
                template_sequence and template_sequence == "".join(structural_bases)
            )
            try:
                guard = self.hard_guards.validate(request, reference)
                hard_valid = bool(guard.valid)
                hard_guard_reasons = list(guard.reasons)
            except Exception as exc:
                hard_valid = False
                hard_guard_reasons = [f"guard_error:{type(exc).__name__}"]
            base_preserved = bool(reference_bases == structural_bases)
            boundary_agreement = assess_variable_boundary_reference_agreement(
                proposed_labels=structural_labels,
                reference_labels=reference_labels,
                variable_span_residue_indices=variable_span_indices,
            )
            eligible_support = bool(
                hard_valid
                and base_preserved
                and boundary_agreement.get("agrees")
                and template_sha256
                and not exact_input_template
            )
            if eligible_support:
                supporting_signatures.add(template_sha256)
            reference_records.append(
                {
                    "template_name": str(reference.template_name or ""),
                    "template_sequence_sha256": template_sha256,
                    "exact_input_template": exact_input_template,
                    "hard_valid": hard_valid,
                    "hard_guard_reasons": hard_guard_reasons,
                    "base_preserved": base_preserved,
                    "boundary_partition_agreement": boundary_agreement,
                    "eligible_support": eligible_support,
                }
            )

        consensus = {
            **consensus_contract,
            "established": len(supporting_signatures) >= minimum_support,
            "distinct_sequence_support": len(supporting_signatures),
            "candidate_count": len(reference_records),
            "candidates": reference_records,
        }
        audit["reference_consensus"] = consensus
        if consensus["established"]:
            audit["status"] = "resolved"
            result["reference_consensus_established"] = True
            result["reference_consensus_support"] = len(supporting_signatures)
            audit["result"] = result
            structure_candidate.raw_meta["variable_boundary_reconciliation"] = audit
            return self._retag_composite_candidate(
                structure_candidate,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                note="variable_boundary_multi_evidence_consensus_established",
            )

        return self._retain_variable_boundary_parent(
            parent_snapshot=parent_snapshot,
            action=action,
            branch_id=branch_id,
            audit=audit,
            structure_proposal=result,
            reason="long_variable_arm_requires_reference_consensus",
            note="variable_boundary_multi_evidence_consensus_not_established",
        )

    @classmethod
    def _retain_variable_boundary_parent(
        cls,
        *,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        audit: Dict[str, Any],
        structure_proposal: Dict[str, Any],
        reason: str,
        note: str,
    ) -> RootSnapshot:
        retained = deepcopy(parent_snapshot)
        audit["status"] = "unresolved"
        audit["structure_proposal"] = dict(structure_proposal)
        audit["result"] = {
            "resolved": False,
            "reason": reason,
            "answer_free": True,
            "gold_coordinates_used": False,
        }
        retained.raw_meta = dict(retained.raw_meta or {})
        retained.raw_meta["variable_boundary_reconciliation"] = audit
        return cls._retag_composite_candidate(
            retained,
            parent_snapshot=parent_snapshot,
            action=action,
            branch_id=branch_id,
            note=note,
        )

    @staticmethod
    def _occupied_snapshot_rows(snapshot: RootSnapshot) -> tuple[List[str], List[str]]:
        labels: List[str] = []
        bases: List[str] = []
        for label, raw_base in zip(snapshot.numbering_row, snapshot.sequence_row):
            base = str(raw_base or "").strip().upper().replace("T", "U")
            if base not in {"A", "C", "G", "U", "N"}:
                continue
            labels.append(str(label or "").strip())
            bases.append(base)
        return labels, bases

    @staticmethod
    def _retag_composite_candidate(
        snapshot: RootSnapshot,
        *,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        note: str,
    ) -> RootSnapshot:
        snapshot.snapshot_id = branch_id
        snapshot.parent_snapshot_id = parent_snapshot.snapshot_id
        snapshot.source_action = action.action_type
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        branch = dict(snapshot.raw_meta.get("agent_branch") or {})
        branch.update(
            {
                "branch_id": branch_id,
                "parent_snapshot_id": parent_snapshot.snapshot_id,
                "action_id": action.action_id,
                "action_type": action.action_type,
                "specialist": action.specialist,
                "scope": action.scope,
                "target_region": action.target_region,
                "params": dict(action.params or {}),
                "reason": action.reason,
                "allowed_regions": list(action.allowed_regions or []),
                "success_condition": str(action.success_condition or ""),
                "rollback_condition": str(action.rollback_condition or ""),
                "notes": [*list(branch.get("notes") or []), note],
            }
        )
        snapshot.raw_meta["agent_branch"] = branch
        return snapshot

    def _execute_multi_template_reference_search(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState,
    ) -> RootSnapshot:
        values = dict(request.runtime_values_override or {})
        try:
            top_k = int(values.get("AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K", 5))
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(2, min(top_k, 10))
        try:
            minimum_support = int(
                values.get("AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT", 2)
            )
        except (TypeError, ValueError):
            minimum_support = 2
        minimum_support = max(2, min(minimum_support, top_k))

        parent_evidence = assess_local_coordinate_ambiguity(parent_snapshot)
        search_action = deepcopy(action)
        search_action.params = {
            **dict(search_action.params or {}),
            "template_top_k": top_k,
        }
        baseline = self.branch_executor.execute(
            request=request,
            parent_snapshot=parent_snapshot,
            action=search_action,
            branch_id=f"{branch_id}-reference-01",
            work_state=work_state,
        )
        template_names: List[str] = []
        for raw_name in [
            baseline.template_name,
            *[
                str(item.get("seqname") or "")
                for item in list(baseline.template_candidates or [])
            ],
        ]:
            name = str(raw_name or "").strip()
            if name and name not in template_names:
                template_names.append(name)
            if len(template_names) >= top_k:
                break
        if not template_names:
            template_names.append(str(baseline.template_name or ""))

        snapshots: List[RootSnapshot] = []
        candidate_records: List[Dict[str, Any]] = []
        for index, template_name in enumerate(template_names, start=1):
            if template_name == baseline.template_name:
                snapshot = baseline
            else:
                probe_action = deepcopy(search_action)
                probe_action.params = {
                    **dict(probe_action.params or {}),
                    "template_name": template_name,
                }
                snapshot = self.branch_executor.execute(
                    request=request,
                    parent_snapshot=parent_snapshot,
                    action=probe_action,
                    branch_id=f"{branch_id}-reference-{index:02d}",
                    work_state=work_state,
                )

            (
                _diagnostics,
                _difficulty,
                _route,
                guard,
                score,
            ) = self._evaluate_snapshot(request, snapshot)
            resolution = candidate_local_resolution(
                parent_snapshot,
                snapshot,
                parent_evidence,
            )
            snapshots.append(snapshot)
            candidate_records.append(
                {
                    "template_name": str(snapshot.template_name or ""),
                    "hard_valid": bool(guard.valid),
                    "score": round(float(score), 4) if math.isfinite(score) else None,
                    "hard_guard_reasons": list(guard.reasons),
                    "local_resolution": resolution,
                }
            )

        resolving = [
            (snapshot, record)
            for snapshot, record in zip(snapshots, candidate_records)
            if bool(record["hard_valid"])
            and bool(dict(record["local_resolution"])["resolved"])
        ]
        resolution_groups: Dict[str, List[tuple[RootSnapshot, Dict[str, Any]]]] = {}
        for snapshot, record in resolving:
            resolution_key = str(
                dict(record.get("local_resolution") or {}).get("resolution_key")
                or ""
            )
            if not resolution_key:
                continue
            resolution_groups.setdefault(resolution_key, []).append((snapshot, record))
        ranked_resolution_groups = sorted(
            resolution_groups.items(),
            key=lambda item: (
                len(
                    {
                        str(record["template_name"])
                        for _, record in item[1]
                        if str(record["template_name"])
                    }
                ),
                max(
                    (
                        float(record["score"])
                        if record.get("score") is not None
                        else -math.inf
                    )
                    for _, record in item[1]
                ),
                item[0],
            ),
            reverse=True,
        )
        consensus_key = ranked_resolution_groups[0][0] if ranked_resolution_groups else ""
        consensus_group = (
            ranked_resolution_groups[0][1] if ranked_resolution_groups else []
        )
        resolution_support = len(
            {
                str(record["template_name"])
                for _, record in consensus_group
                if str(record["template_name"])
            }
        )
        consensus_established = resolution_support >= minimum_support
        valid = [
            (snapshot, record)
            for snapshot, record in zip(snapshots, candidate_records)
            if bool(record["hard_valid"])
        ]
        selection_pool = consensus_group if consensus_established else valid
        if selection_pool:
            selected, selected_record = max(
                selection_pool,
                key=lambda item: (
                    (
                        float(item[1]["score"])
                        if item[1].get("score") is not None
                        else -math.inf
                    ),
                    str(item[1]["template_name"]),
                ),
            )
        else:
            selected = baseline
            selected_record = next(
                (
                    record
                    for snapshot, record in zip(snapshots, candidate_records)
                    if snapshot is baseline
                ),
                candidate_records[0],
            )

        selected.raw_meta = dict(selected.raw_meta or {})
        selected.raw_meta["multi_template_reference_search"] = {
            "policy": "top_k_template_consensus_for_variable_bridge_v3",
            "answer_free": True,
            "gold_coordinates_used": False,
            "top_k": top_k,
            "minimum_distinct_template_support": minimum_support,
            "local_trigger": dict(parent_evidence or {}),
            "candidate_count": len(candidate_records),
            "candidates": candidate_records,
            "resolution_support": resolution_support,
            "resolution_group_support": {
                key: len(
                    {
                        str(record["template_name"])
                        for _, record in group
                        if str(record["template_name"])
                    }
                )
                for key, group in ranked_resolution_groups
            },
            "consensus_resolution_key": consensus_key,
            "consensus_established": consensus_established,
            "selected_template": str(selected.template_name or ""),
            "selected_local_resolution": dict(
                selected_record.get("local_resolution") or {}
            ),
            "selection_reason": (
                "distinct_templates_support_the_same_canonical_variable_bridge"
                if consensus_established
                else "best_valid_reference_candidate_without_local_consensus"
            ),
        }
        legacy_meta = dict(selected.raw_meta.get("legacy_reference_tool") or {})
        legacy_meta["search_mode"] = "top_k_template_consensus"
        selected.raw_meta["legacy_reference_tool"] = legacy_meta
        selected.snapshot_id = branch_id
        selected.parent_snapshot_id = parent_snapshot.snapshot_id
        selected.source_action = action.action_type
        branch_meta = dict(selected.raw_meta.get("agent_branch") or {})
        branch_meta.update(
            {
                "branch_id": branch_id,
                "parent_snapshot_id": parent_snapshot.snapshot_id,
                "action_id": action.action_id,
                "action_type": action.action_type,
                "specialist": action.specialist,
                "params": dict(action.params or {}),
                "notes": [
                    *list(branch_meta.get("notes") or []),
                    "multi_template_reference_search_completed",
                ],
            }
        )
        selected.raw_meta["agent_branch"] = branch_meta
        return selected

    def _initialize_root_from_agent(
        self,
        request: AlignmentRequest,
    ) -> tuple[RootSnapshot | None, List[Dict[str, Any]], List[Dict[str, Any]], int]:
        parent = RootSnapshot(
            template_name="input-contract",
            csv_content="",
            raw_meta={"input_contract": self._input_contract_observation(request)},
            snapshot_id="input-contract",
            root_source="input_contract",
            source_action="classify_input",
        )
        remaining = self._initial_root_actions(request)
        trace_events: List[Dict[str, Any]] = [
            {
                "stage": "scope_gate_evaluated",
                "strategy": self._canonical_root_strategy(request),
                "allowed_tools": [action.action_type for action in remaining],
            }
        ]
        emit_progress(request, trace_events[0])
        failures: List[Dict[str, Any]] = []
        planner_calls = 0

        while remaining:
            if self._use_llm_for_initial_selection(request, remaining, failures):
                emit_progress(request, {"stage": "evidence_review", "phase": "initial_tool_selection"})
                decision = self.action_planner.decide(
                    request=request,
                    phase="initial_tool_selection",
                    observation={
                        "input_contract": self._input_contract_observation(request),
                        "previous_tool_failures": list(failures),
                    },
                    actions=remaining,
                    allowed_outcomes=(),
                )
            else:
                decision = PlannerDecision(
                    decision="tool",
                    action_id=remaining[0].action_id,
                    reason_code="harness_evidence_first",
                    planner_source="harness_fast_path",
                    llm_called=False,
                )
            planner_calls += int(bool(decision.llm_called))
            record_trace(request, trace_events,
                {
                    "stage": "agent_decision",
                    "phase": "initial_tool_selection",
                    "allowed_action_ids": [action.action_id for action in remaining],
                    **decision.trace(),
                }
            )
            selected = next(
                (action for action in remaining if action.action_id == decision.action_id),
                remaining[0],
            )
            record_trace(request, trace_events,
                {
                    "stage": "tool_started",
                    "phase": "initial_tool_selection",
                    "tool": selected.action_type,
                    "action_id": selected.action_id,
                }
            )
            try:
                snapshot = self.branch_executor.execute(
                    request=request,
                    parent_snapshot=parent,
                    action=selected,
                    branch_id="root-candidate",
                    work_state=None,
                )
            except Exception as exc:
                failure = {
                    "tool": selected.action_type,
                    "action_id": selected.action_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                failures.append(failure)
                record_trace(request, trace_events,
                    {
                        "stage": "initial_tool_failed",
                        **failure,
                        "disposition": "rejected",
                    }
                )
                record_trace(request, trace_events,
                    {
                        "stage": (
                            "primary_tool_failed"
                            if len(failures) == 1
                            else "fallback_tool_failed"
                        ),
                        **failure,
                        "disposition": (
                            "next_agent_decision"
                            if len(remaining) > 1
                            else "abstained"
                        ),
                    }
                )
                remaining = [
                    action for action in remaining if action.action_id != selected.action_id
                ]
                continue

            snapshot.snapshot_id = "root"
            snapshot.parent_snapshot_id = None
            snapshot.raw_meta = dict(snapshot.raw_meta or {})
            snapshot.raw_meta["input_contract"] = self._input_contract_observation(request)
            snapshot.raw_meta["agent_strategy"] = {
                "strategy": self._canonical_root_strategy(request),
                "selected_initial_tool": selected.action_type,
                "planner_source": decision.planner_source,
                "llm_called": bool(decision.llm_called),
            }
            legacy_failure = next(
                (
                    item
                    for item in failures
                    if item.get("tool") == "align_legacy_reference"
                ),
                None,
            )
            if legacy_failure is not None:
                snapshot.raw_meta["legacy_root_failure"] = {
                    **dict(legacy_failure),
                    "stage": "legacy_root_failed",
                    "preserved": True,
                }
                snapshot.raw_meta["root_recovery"] = {
                    "enabled": True,
                    "status": "candidate_generated",
                    "selected_tool": selected.action_type,
                }
                record_trace(request, trace_events,
                    {
                        "stage": "root_recovery_tool_called",
                        "tool": selected.action_type,
                        "scope_reason": "explicit_mature_canonical_scope",
                    }
                )
            extend_trace(request, trace_events,
                [
                    {
                        "stage": "canonical_route_selected",
                        "strategy": self._canonical_root_strategy(request),
                        "tool": selected.action_type,
                        "planner_source": decision.planner_source,
                    },
                    *list(
                        dict(snapshot.raw_meta.get("cm_tool") or {}).get(
                            "trace_events"
                        )
                        or []
                    ),
                ]
            )
            if request.output_csv_path:
                with open(
                    request.output_csv_path,
                    "w",
                    encoding="utf-8",
                    newline="",
                ) as handle:
                    handle.write(snapshot.csv_content)
            return snapshot, trace_events, failures, planner_calls

        return None, trace_events, failures, planner_calls

    def _initial_root_actions(self, request: AlignmentRequest) -> List[CandidateAction]:
        cm_action = CandidateAction(
            action_id="initial:align_numbering_cm",
            action_type="align_numbering_cm",
            specialist="scientific_toolbox",
            params={"domain_hint": request.domain_hint},
            reason="generate_structure_homology_numbering_evidence",
            success_condition="candidate_passes_calibration_and_shared_hard_guards",
            rollback_condition="reject_candidate_or_tool_failure",
        )
        legacy_action = CandidateAction(
            action_id="initial:align_legacy_reference",
            action_type="align_legacy_reference",
            specialist="scientific_toolbox",
            reason="generate_template_segmentation_and_regional_dp_evidence",
            success_condition="candidate_passes_shared_hard_guards",
            rollback_condition="reject_candidate_or_tool_failure",
        )
        strategy = self._canonical_root_strategy(request)
        if strategy == "legacy_only":
            actions = [legacy_action]
            recovery_allowed, _ = self._canonical_root_recovery_allowed(request)
            if self._root_recovery_enabled(request) and recovery_allowed:
                cm_action.action_id = "recovery:align_numbering_cm"
                cm_action.reason = "explicit_root_recovery_after_legacy_tool_failure"
                actions.append(cm_action)
            return actions
        if strategy == "cm_only":
            return [cm_action]
        if strategy == "cm_first":
            return (
                [cm_action, legacy_action]
                if self._legacy_fallback_enabled(request)
                else [cm_action]
            )
        if not request.use_llm:
            return [legacy_action]
        if not self._canonical_cm_allowed(request):
            return [legacy_action]
        return [cm_action, legacy_action]

    @staticmethod
    def _canonical_cm_allowed(request: AlignmentRequest) -> bool:
        input_type = str(request.input_type or "unknown").strip().lower() or "unknown"
        # Unknown is eligible for evidence gathering, not treated as a mature-tRNA claim.
        # CM calibration and shared guards still decide whether its candidate can survive.
        if input_type not in {"mature", "unknown"}:
            return False
        if str(request.intron_status or "").strip().lower() == "present":
            return False
        if str(request.domain_hint or "").strip().lower() in {
            "mitochondrial",
            "virus",
        }:
            return False
        return str(request.trna_class or "").strip().lower() != "selenocysteine"

    @staticmethod
    def _input_contract_observation(request: AlignmentRequest) -> Dict[str, Any]:
        observation = assess_input_evidence(
            target_seq=request.target_seq,
            input_type=request.input_type,
            domain_hint=request.domain_hint,
            cca_status=request.cca_status,
            intron_status=request.intron_status,
            trna_class=request.trna_class,
        )
        observation.update(
            {
                "input_type": request.input_type,
                "domain_hint": request.domain_hint,
                "cca_status": request.cca_status,
                "intron_status": request.intron_status,
                "trna_class": request.trna_class,
                "mitochondrial_subtype": request.mitochondrial_subtype,
                "anticodon_hint": request.anticode,
                "template_constraint": request.template_name or "",
            }
        )
        return observation

    def _failed_initial_tools_result(
        self,
        request: AlignmentRequest,
        *,
        failures: List[Dict[str, Any]],
        trace_events: List[Dict[str, Any]],
    ) -> SearchResult:
        recovery_allowed, recovery_reason = self._canonical_root_recovery_allowed(
            request
        )
        recovery_requested = self._root_recovery_enabled(request)
        warning = (
            recovery_reason
            if recovery_requested and not recovery_allowed
            else "initial_toolbox_failed"
        )
        evidence = SpecialRouteEvidence(
            route="out_of_scope",
            status="error",
            disposition="initial_toolbox_unavailable",
            message="No Harness-allowed initial tool produced a candidate.",
            warnings=[warning],
            evidence={
                "tool_failures": list(failures),
                "root_recovery_requested": recovery_requested,
                "root_recovery_scope": recovery_reason,
            },
        )
        snapshot = self._evidence_only_snapshot(request, evidence)
        return self._single_snapshot_result(
            request=request,
            guard_request=request,
            snapshot=snapshot,
            trace_events=list(trace_events),
            stop_reason="initial_toolbox_failed",
        )

    @staticmethod
    def _canonical_root_strategy(request: AlignmentRequest) -> str:
        raw = str(
            (request.runtime_values_override or {}).get(
                "AGENT_CANONICAL_ROOT_STRATEGY",
                "agent_toolbox",
            )
            or "agent_toolbox"
        ).strip().lower()
        return (
            raw
            if raw in {"agent_toolbox", "cm_first", "cm_only", "legacy_only"}
            else "agent_toolbox"
        )

    @staticmethod
    def _legacy_fallback_enabled(request: AlignmentRequest) -> bool:
        raw = (request.runtime_values_override or {}).get(
            "AGENT_ENABLE_LEGACY_FALLBACK",
            True,
        )
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _guard_disposition(snapshot: RootSnapshot, guard) -> str:
        override = str(
            dict(snapshot.raw_meta.get("agent_specialized_status") or {}).get(
                "disposition_override"
            )
            or ""
        ).strip().lower()
        if override in {"investigate", "abstained"}:
            return override
        if guard.valid:
            return "accepted"
        if snapshot.numbering_row and snapshot.sequence_row:
            return "investigate"
        return "abstained"

    def _run_special_preflight(
        self,
        request: AlignmentRequest,
    ) -> SpecialRouteEvidence | None:
        try:
            return self.special_route_service.run_if_applicable(request)
        except Exception as exc:
            if str(request.domain_hint or "").lower() == "mitochondrial":
                route = "mitochondrial"
            elif str(request.trna_class or "").lower() == "selenocysteine":
                route = "selenocysteine"
            else:
                route = "archaeal_ncintron"
            return SpecialRouteEvidence(
                route=route,
                status="error",
                disposition="tool_error_abstain",
                message=f"Specialized evidence tool failed: {type(exc).__name__}: {exc}",
                warnings=["specialized_evidence_tool_failed"],
                trace_events=[
                    {
                        "stage": "special_tool_failed",
                        "route": route,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "disposition": "abstained",
                    }
                ],
            )

    def _result_from_special_evidence(
        self,
        request: AlignmentRequest,
        evidence: SpecialRouteEvidence,
    ) -> SearchResult:
        special_candidate_attempted = False
        if evidence.canonical_numbering_eligible and evidence.mature_sequence:
            snapshot = self._number_derived_mature_sequence(request, evidence)
            guard_request = request
        elif self._canonical_like_special_candidate_allowed(request, evidence):
            special_candidate_attempted = True
            try:
                snapshot = self._number_special_canonical_like_sequence(request, evidence)
            except Exception as exc:
                evidence.warnings.append("canonical_like_numbering_tool_failed")
                evidence.trace_events.append(
                    {
                        "stage": "special_canonical_like_tool_failed",
                        "route": evidence.route,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "disposition": "abstained",
                    }
                )
                snapshot = self._evidence_only_snapshot(request, evidence)
            guard_request = request
        else:
            snapshot = self._evidence_only_snapshot(request, evidence)
            guard_request = request
        trace = [
            {
                "stage": "input_classified",
                "input_type": request.input_type,
                "domain_hint": request.domain_hint,
                "trna_class": request.trna_class,
                "intron_status": request.intron_status,
                "input_contract": self._input_contract_observation(request),
            },
            {
                "stage": "special_route_selected",
                "route": evidence.route,
                "status": evidence.status,
                "disposition": evidence.disposition,
            },
            *list(evidence.trace_events),
            *list(
                dict(snapshot.raw_meta.get("cm_tool") or {}).get("trace_events") or []
            ),
        ]
        return self._single_snapshot_result(
            request=request,
            guard_request=guard_request,
            snapshot=snapshot,
            trace_events=trace,
            stop_reason=(
                "special_route_mature_sequence_numbered"
                if evidence.canonical_numbering_eligible and evidence.mature_sequence
                else (
                    "special_route_canonical_like_candidate_evaluated"
                    if special_candidate_attempted and snapshot.numbering_row
                    else "special_route_abstained"
                )
            ),
        )

    @staticmethod
    def _canonical_like_special_candidate_allowed(
        request: AlignmentRequest,
        evidence: SpecialRouteEvidence,
    ) -> bool:
        input_type = str(request.input_type or "unknown").strip().lower()
        intron_status = str(request.intron_status or "unknown").strip().lower()
        return bool(
            evidence.route in {"mitochondrial", "virus", "selenocysteine"}
            and input_type in {"mature", "unknown"}
            and intron_status != "present"
        )

    def _number_special_canonical_like_sequence(
        self,
        request: AlignmentRequest,
        evidence: SpecialRouteEvidence,
    ) -> RootSnapshot:
        parent = RootSnapshot(
            template_name="special-route-evidence",
            csv_content="",
            raw_meta={"specialized_route": evidence.metadata()},
            snapshot_id="special-evidence",
            root_source="special_route_preflight",
            source_action="run_special_model",
        )
        action = CandidateAction(
            action_id="special:align-numbering-cm",
            action_type="align_numbering_cm",
            specialist="scientific_toolbox",
            params={
                "model_group": "all",
                "domain_hint": request.domain_hint,
            },
            reason=(
                "use_cross_domain_numbering_model_for_special_source_outside_"
                "canonical_domain_selector_scope"
            ),
            success_condition="four_arm_topology_and_shared_hard_guards_pass",
            rollback_condition="investigate_or_abstain_if_evidence_is_insufficient",
        )
        snapshot = self.cm_numbering_tool.execute(
            request=request,
            parent_snapshot=parent,
            action=action,
        )
        assessment = assess_canonical_like_structure(
            numbering_row=snapshot.numbering_row,
            sequence_row=snapshot.sequence_row,
            structure_pairs=list(dict(snapshot.structure_view or {}).get("id_pairs") or []),
            numbering_backend=str(snapshot.raw_meta.get("numbering_backend") or ""),
            cm_bit_score=dict(snapshot.raw_meta.get("cm_score") or {}).get("bit_score"),
        )
        special_meta = evidence.metadata()
        special_meta.update(
            {
                "evidence_disposition": evidence.disposition,
                "disposition": "canonical_like_candidate_evaluated",
                "canonical_like_structure": assessment.metadata(),
            }
        )
        snapshot.snapshot_id = "special-canonical-like-root"
        snapshot.parent_snapshot_id = "special-evidence"
        snapshot.root_source = "special_route_canonical_like_numbering"
        snapshot.source_action = "align_special_source_with_numbering_cm"
        snapshot.raw_meta["specialized_route"] = special_meta
        snapshot.raw_meta["agent_specialized_status"] = {
            "forced_route": evidence.route,
            "in_canonical_benchmark": False,
            "out_of_scope": False,
            "confidence_tier": (
                "accepted_with_scope_warning"
                if assessment.qualified
                else "insufficient_canonical_like_evidence"
            ),
            "disposition_override": "" if assessment.qualified else "investigate",
        }
        snapshot.raw_meta["agent_strategy"] = {
            "strategy": "special_source_canonical_like_gate",
            "selected_initial_tool": "align_numbering_cm",
            "selected_model_group": "all",
            "domain_selector_used": False,
            "domain_selector_exclusion_reason": "special_source_outside_training_scope",
            "planner_source": "deterministic_harness_gate",
            "llm_called": False,
        }
        snapshot.warnings.extend(
            warning
            for warning in (
                "special_source_numbered_with_canonical_like_model",
                "route_specific_crosswalk_not_used",
                (
                    "accepted_with_scope_warning"
                    if assessment.qualified
                    else "canonical_like_structure_not_qualified"
                ),
            )
            if warning not in snapshot.warnings
        )
        trace_events = list(
            dict(snapshot.raw_meta.get("cm_tool") or {}).get("trace_events") or []
        )
        record_trace(request, trace_events,
            {
                "stage": "canonical_like_structure_assessed",
                "route": evidence.route,
                "qualified": assessment.qualified,
                "pair_counts": dict(assessment.pair_counts),
                "minimum_pair_counts": dict(assessment.minimum_pair_counts),
                "cm_bit_score": assessment.cm_bit_score,
                "minimum_cm_bit_score": assessment.minimum_cm_bit_score,
                "reasons": list(assessment.reasons),
                "numbering_model_group": "all",
                "domain_selector_used": False,
                "disposition": (
                    "accepted_with_scope_warning"
                    if assessment.qualified
                    else "investigate"
                ),
            }
        )
        snapshot.raw_meta.setdefault("cm_tool", {})["trace_events"] = trace_events
        return snapshot

    def _number_derived_mature_sequence(
        self,
        request: AlignmentRequest,
        evidence: SpecialRouteEvidence,
    ) -> RootSnapshot:
        mature_request = AlignmentRequest(
            target_seq=evidence.mature_sequence,
            output_csv_path=request.output_csv_path,
            anticode=request.anticode,
            use_llm=False,
            llm_reasoning=False,
            input_type="mature",
            domain_hint=request.domain_hint,
            cca_status=request.cca_status,
            intron_status=str(
                evidence.evidence.get("derived_intron_status") or "removed"
            ),
            trna_class=request.trna_class,
            mitochondrial_subtype=request.mitochondrial_subtype,
            runtime_values_override=dict(request.runtime_values_override or {}),
        )
        parent = RootSnapshot(
            template_name="special-route-evidence",
            csv_content="",
            raw_meta={"specialized_route": evidence.metadata()},
            snapshot_id="special-evidence",
            root_source="special_route_preflight",
            source_action="run_special_model",
        )
        action = CandidateAction(
            action_id="special-intron-number-mature",
            action_type="align_numbering_cm",
            specialist="special_route_recovery",
            params={"domain_hint": request.domain_hint},
            reason="number_only_the_derived_mature_sequence",
            success_condition="mature_sequence_base_preserved_and_numbering_legal",
            rollback_condition="abstain_if_cm_or_shared_guards_fail",
        )
        snapshot = self.cm_numbering_tool.execute(
            request=mature_request,
            parent_snapshot=parent,
            action=action,
        )
        snapshot.snapshot_id = "special-mature-root"
        snapshot.parent_snapshot_id = "special-evidence"
        snapshot.root_source = "special_route_mature_numbering"
        snapshot.source_action = (
            "number_mature_after_intron_splicing"
            if evidence.evidence.get("removed_intron_interval")
            else "number_mature_after_input_boundary_detection"
        )
        snapshot.raw_meta["specialized_route"] = evidence.metadata()
        snapshot.raw_meta["agent_specialized_status"] = {
            "forced_route": evidence.route,
            "in_canonical_benchmark": False,
            "out_of_scope": False,
        }
        transform_meta = dict(evidence.evidence.get("input_transform") or {})
        snapshot.raw_meta["input_transform"] = {
            "type": (
                "extract_tRNA_body_and_remove_intron"
                if evidence.evidence.get("removed_intron_interval")
                else "extract_tRNA_body"
            ),
            "raw_sequence": request.normalized_target_seq().replace("T", "U"),
            "mature_sequence": evidence.mature_sequence,
            "raw_to_mature": list(evidence.raw_to_mature),
            "removed_interval": dict(evidence.evidence.get("removed_intron_interval") or {}),
            "source_interval": dict(transform_meta.get("source_interval") or {}),
            "removed_flanks": dict(transform_meta.get("removed_flanks") or {}),
        }
        snapshot.raw_meta["input_contract"] = self._input_contract_observation(request)
        snapshot.warnings.extend(
            warning for warning in evidence.warnings if warning not in snapshot.warnings
        )
        if request.output_csv_path:
            with open(request.output_csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(snapshot.csv_content)
        return snapshot

    @staticmethod
    def _evidence_only_snapshot(
        request: AlignmentRequest,
        evidence: SpecialRouteEvidence,
    ) -> RootSnapshot:
        structures = list(evidence.evidence.get("structures") or [])
        structure = str((structures[0] if structures else {}).get("structure") or "")
        hits = list(evidence.evidence.get("hits") or [])
        best_hit = max(hits, key=lambda item: float(item.get("score") or -1e9)) if hits else {}
        annotation_meta = {
            "annotation_source": "specialized_evidence",
            "trnascan_hit": bool(hits),
            "trnascan_isotype": str(best_hit.get("isotype") or ""),
            "trnascan_anticodon": str(best_hit.get("anticodon") or ""),
            "segmentation_status": "not_projected_to_sprinzl",
        }
        return RootSnapshot(
            template_name=str(
                evidence.evidence.get("selected_model")
                or best_hit.get("isotype_model")
                or f"{evidence.route}-evidence"
            ),
            csv_content="",
            warnings=list(evidence.warnings),
            structure_view={
                "secondary_structure": structure,
                "annotation_source": "specialized_evidence",
            },
            raw_meta={
                "annotation_meta": annotation_meta,
                "specialized_route": evidence.metadata(),
                "agent_specialized_status": {
                    "forced_route": evidence.route,
                    "in_canonical_benchmark": False,
                    "out_of_scope": True,
                },
                "numbering_backend": None,
            },
            root_source="special_route_evidence",
            snapshot_id="special-evidence",
            source_action="run_special_model",
        )

    def _recover_failed_root(
        self,
        request: AlignmentRequest,
        exc: Exception,
    ) -> SearchResult:
        failure = {
            "stage": "legacy_root_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "preserved": True,
        }
        allowed, reason = self._canonical_root_recovery_allowed(request)
        if not allowed:
            evidence = SpecialRouteEvidence(
                route="out_of_scope",
                status="abstained",
                disposition="root_recovery_scope_unresolved",
                message="Canonical CM recovery requires an explicit mature canonical input scope.",
                warnings=[reason],
                evidence={"legacy_root_failure": failure},
                trace_events=[
                    {
                        "stage": "root_recovery_abstained",
                        "reason": reason,
                        "disposition": "abstained",
                    }
                ],
            )
            snapshot = self._evidence_only_snapshot(request, evidence)
            return self._single_snapshot_result(
                request=request,
                guard_request=request,
                snapshot=snapshot,
                trace_events=[
                    {
                        "stage": "input_classified",
                        "input_type": request.input_type,
                        "domain_hint": request.domain_hint,
                        "trna_class": request.trna_class,
                        "intron_status": request.intron_status,
                        "input_contract": self._input_contract_observation(request),
                    },
                    failure,
                    *evidence.trace_events,
                ],
                stop_reason="root_recovery_abstained",
            )

        parent = RootSnapshot(
            template_name="legacy-root-failed",
            csv_content="",
            raw_meta={"legacy_root_failure": failure},
            snapshot_id="failed-root",
            root_source="legacy_root_failure",
            source_action="root_snapshot",
        )
        action = CandidateAction(
            action_id="recover-root-with-canonical-cm",
            action_type="align_numbering_cm",
            specialist="root_recovery",
            params={"domain_hint": request.domain_hint},
            reason="legacy_root_failed_after_explicit_mature_input_contract",
            success_condition="calibrated_or_explicit_cm_candidate_passes_shared_guards",
            rollback_condition="abstain_if_calibration_or_shared_guards_fail",
        )
        snapshot = self.cm_numbering_tool.execute(
            request=request,
            parent_snapshot=parent,
            action=action,
        )
        snapshot.snapshot_id = "cm-recovery-root"
        snapshot.parent_snapshot_id = "failed-root"
        snapshot.root_source = "cm_root_recovery"
        snapshot.source_action = "recover_root_with_numbering_cm"
        snapshot.raw_meta["legacy_root_failure"] = failure
        snapshot.raw_meta["root_recovery"] = {
            "enabled": True,
            "status": "candidate_generated",
            "scope_reason": reason,
        }
        if request.output_csv_path:
            with open(request.output_csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(snapshot.csv_content)
        return self._single_snapshot_result(
            request=request,
            guard_request=request,
            snapshot=snapshot,
            trace_events=[
                {
                    "stage": "input_classified",
                    "input_type": request.input_type,
                    "domain_hint": request.domain_hint,
                    "trna_class": request.trna_class,
                    "intron_status": request.intron_status,
                    "input_contract": self._input_contract_observation(request),
                },
                failure,
                {
                    "stage": "root_recovery_tool_called",
                    "tool": "align_numbering_cm",
                    "scope_reason": reason,
                },
                *list(dict(snapshot.raw_meta.get("cm_tool") or {}).get("trace_events") or []),
            ],
            stop_reason="root_recovery_candidate_evaluated",
        )

    @staticmethod
    def _canonical_root_recovery_allowed(request: AlignmentRequest) -> tuple[bool, str]:
        if str(request.input_type or "").lower() != "mature":
            return False, "root_recovery_requires_input_type_mature"
        if str(request.intron_status or "").lower() == "present":
            return False, "canonical_root_recovery_blocked_for_present_intron"
        if str(request.domain_hint or "").lower() in {"mitochondrial", "virus"}:
            return False, "canonical_root_recovery_blocked_for_special_domain"
        if str(request.trna_class or "").lower() == "selenocysteine":
            return False, "canonical_root_recovery_blocked_for_selenocysteine"
        return True, "explicit_mature_canonical_scope"

    def _single_snapshot_result(
        self,
        *,
        request: AlignmentRequest,
        guard_request: AlignmentRequest,
        snapshot: RootSnapshot,
        trace_events: List[Dict[str, Any]],
        stop_reason: str,
    ) -> SearchResult:
        diagnostics = self.difficulty_assessor.build_diagnostics(snapshot)
        difficulty = self.difficulty_assessor.assess(diagnostics, snapshot)
        route = self.route_assessor.assess(snapshot, diagnostics)
        guard = self.hard_guards.validate(guard_request, snapshot)
        score = self.online_ranker.score(snapshot, diagnostics, route, guard)
        disposition = self._guard_disposition(snapshot, guard)
        specialized_route = dict(snapshot.raw_meta.get("specialized_route") or {})
        canonical_like = dict(specialized_route.get("canonical_like_structure") or {})
        if canonical_like:
            specialized_route["final_disposition"] = disposition
            if disposition == "accepted":
                specialized_route["disposition"] = "accepted_with_scope_warning"
            snapshot.raw_meta["specialized_route"] = specialized_route
        trace = list(trace_events)
        trace.extend(
            [
                {
                    "stage": "guard_evaluated",
                    "valid": bool(guard.valid),
                    "reasons": list(guard.reasons),
                    "disposition": disposition,
                },
                {
                    "stage": "search_stopped",
                    "reason": stop_reason,
                    "disposition": disposition,
                },
            ]
        )
        summary = {
            "stop_reason": stop_reason,
            "best_score": float(score),
            "best_valid": bool(guard.valid),
            "best_reasons": list(guard.reasons),
            "disposition": disposition,
        }
        branch = {
            "snapshot_id": snapshot.snapshot_id,
            "source_action": snapshot.source_action,
            "template_name": snapshot.template_name,
            "route": route.route,
            "route_confidence": route.confidence,
            "in_canonical_benchmark": route.in_canonical_benchmark,
            "out_of_scope": route.out_of_scope,
            "difficulty_tier": difficulty.tier,
            "difficulty_score": difficulty.difficulty_score,
            "hard_valid": guard.valid,
            "score": score,
            "reasons": list(guard.reasons),
            "disposition": disposition,
        }
        return SearchResult(
            root_snapshot=snapshot,
            best_snapshot=snapshot,
            diagnostics=diagnostics,
            difficulty=difficulty,
            route=route,
            root_diagnostics=diagnostics,
            root_difficulty=difficulty,
            root_route=route,
            expanded=True,
            trace_events=trace,
            branch_summaries=[branch],
            context_summary={
                "input_contract": self._input_contract_observation(request)
            },
            memory_hits=[],
            work_state_summary=summary,
        )

    def _evaluate_snapshot(
        self,
        request: AlignmentRequest,
        snapshot,
    ):
        diagnostics = self.difficulty_assessor.build_diagnostics(snapshot)
        difficulty = self.difficulty_assessor.assess(diagnostics, snapshot)
        route = self.route_assessor.assess(snapshot, diagnostics)
        guard = self.hard_guards.validate(request, snapshot)
        score = self.online_ranker.score(snapshot, diagnostics, route, guard)
        return diagnostics, difficulty, route, guard, score

    @staticmethod
    def _build_budget(request: AlignmentRequest) -> SearchBudget:
        values = dict(request.runtime_values_override or {})

        def _bounded(name: str, default: int, low: int, high: int) -> int:
            raw = values.get(name, default)
            try:
                return max(low, min(int(raw), high))
            except (TypeError, ValueError):
                return default

        return SearchBudget(
            max_action_steps=_bounded("AGENT_MAX_ACTION_STEPS", 8, 1, 32),
            max_planner_calls=_bounded("AGENT_MAX_PLANNER_CALLS", 6, 1, 32),
            max_template_switches=_bounded("AGENT_MAX_TEMPLATE_SWITCHES", 2, 0, 8),
            max_region_postchecks=_bounded("AGENT_MAX_REGION_POSTCHECKS", 2, 0, 8),
        )

    @staticmethod
    def _planning_enabled(request: AlignmentRequest) -> bool:
        values = dict(request.runtime_values_override or {})
        if "AGENT_ENABLE_PLANNING" not in values:
            return bool(request.use_llm)
        return str(values.get("AGENT_ENABLE_PLANNING") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _llm_triage_mode(request: AlignmentRequest) -> str:
        raw = str(
            (request.runtime_values_override or {}).get(
                "AGENT_LLM_TRIAGE_MODE",
                "uncertain_only",
            )
            or "uncertain_only"
        ).strip().lower()
        return (
            raw
            if raw in {"uncertain_only", "dual_trigger", "always"}
            else "uncertain_only"
        )

    @classmethod
    def _use_llm_for_initial_selection(
        cls,
        request: AlignmentRequest,
        actions: List[CandidateAction],
        failures: List[Dict[str, Any]],
    ) -> bool:
        if not request.use_llm:
            return True
        if cls._llm_triage_mode(request) == "always":
            return True
        # The evidence-first route is fixed and auditable. The model is reserved for
        # conflicts observed after a scientific tool has produced a candidate.
        return bool(failures and len(actions) > 1)

    @classmethod
    def _soft_risk_evidence(
        cls,
        request: AlignmentRequest,
        work_state: AgentWorkState,
    ) -> List[Dict[str, Any]]:
        if (
            not request.use_llm
            or cls._llm_triage_mode(request) != "dual_trigger"
            or work_state.current_route.out_of_scope
        ):
            return []

        meta = dict(work_state.current_snapshot.raw_meta or {})
        if str(meta.get("numbering_backend") or "") != "cmalign":
            return []

        values = dict(request.runtime_values_override or {})
        try:
            posterior_threshold = float(
                values.get("AGENT_SOFT_RISK_MIN_CM_POSTERIOR", 0.995)
            )
        except (TypeError, ValueError):
            posterior_threshold = 0.995
        posterior_threshold = max(0.0, min(1.0, posterior_threshold))

        evidence: List[Dict[str, Any]] = []
        review_acceptor_tail = str(
            values.get("AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL", True)
        ).strip().lower() in {"1", "true", "yes", "on"}
        acceptor_tail = (
            assess_acceptor_tail_ambiguity(work_state.current_snapshot)
            if review_acceptor_tail
            else None
        )
        # A hard conflict in another region must not hide an independently
        # supported acceptor-endpoint check. The candidate remains subject to
        # every shared hard guard before it can be accepted.
        if acceptor_tail:
            evidence.append(acceptor_tail)
        # Expose only this narrow action for an already-invalid candidate.
        if not work_state.current_valid or bool(work_state.issues):
            return evidence
        cm_score = dict(meta.get("cm_score") or {})
        try:
            posterior = float(cm_score.get("average_posterior"))
        except (TypeError, ValueError):
            posterior = None
        if posterior is not None and posterior < posterior_threshold:
            evidence.append(
                {
                    "code": "cm_average_posterior_below_threshold",
                    "observed": round(posterior, 6),
                    "threshold": round(posterior_threshold, 6),
                }
            )

        review_local_ambiguity = str(
            values.get(
                "AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY",
                True,
            )
        ).strip().lower() in {"1", "true", "yes", "on"}
        local_ambiguity = (
            assess_local_coordinate_ambiguity(work_state.current_snapshot)
            if review_local_ambiguity
            else None
        )
        if local_ambiguity:
            evidence.append(local_ambiguity)

        domain_hint = str(request.domain_hint or "").strip().lower()
        trna_class = str(request.trna_class or "").strip().lower()
        review_variable_boundary = str(
            values.get("AGENT_SOFT_RISK_REVIEW_VARIABLE_BOUNDARY", True)
        ).strip().lower() in {"1", "true", "yes", "on"}
        variable_boundary = (
            assess_variable_boundary_ambiguity(work_state.current_snapshot)
            if (
                review_variable_boundary
                and "mitochond" not in domain_hint
                and trna_class not in {"selenocysteine", "sec"}
            )
            else None
        )
        if variable_boundary:
            evidence.append(variable_boundary)

        insertion_count = sum(
            1
            for item in list(meta.get("position_provenance") or [])
            if str((item or {}).get("label_kind") or "") == "insertion"
        )
        review_insertions = str(
            values.get("AGENT_SOFT_RISK_REVIEW_INSERTIONS", True)
        ).strip().lower() in {"1", "true", "yes", "on"}
        if review_insertions and insertion_count > 0:
            evidence.append(
                {
                    "code": "cm_insertion_state_present",
                    "observed": insertion_count,
                    "threshold": 0,
                }
            )

        model_group = str(dict(meta.get("numbering_model") or {}).get("group") or "")
        review_broad_model = str(
            values.get("AGENT_SOFT_RISK_REVIEW_BROAD_CM", True)
        ).strip().lower() in {"1", "true", "yes", "on"}
        if review_broad_model and model_group == "all":
            evidence.append(
                {
                    "code": "cross_domain_cm_requires_agent_review",
                    "observed": model_group,
                }
            )
        return evidence

    @staticmethod
    def _merge_soft_risk_evidence(
        observed: List[Dict[str, Any]],
        current: List[Dict[str, Any]],
    ) -> None:
        known_codes = {
            str(item.get("code") or "")
            for item in observed
            if str(item.get("code") or "")
        }
        for item in current:
            code = str(item.get("code") or "")
            if not code or code in known_codes:
                continue
            observed.append(dict(item))
            known_codes.add(code)

    @staticmethod
    def _next_soft_risk_review_batch(
        evidence: List[Dict[str, Any]],
        *,
        reviewed_codes: set[str],
    ) -> List[Dict[str, Any]]:
        remaining = [
            dict(item)
            for item in evidence
            if str(item.get("code") or "") not in reviewed_codes
        ]
        acceptor_endpoint = [
            item
            for item in remaining
            if str(item.get("code") or "")
            == "cm_acceptor_tail_boundary_ambiguity"
        ]
        if acceptor_endpoint:
            return acceptor_endpoint
        variable_boundary = [
            item
            for item in remaining
            if str(item.get("code") or "")
            == "cm_variable_boundary_structure_ambiguity"
        ]
        if variable_boundary:
            return variable_boundary
        return remaining

    @staticmethod
    def _with_soft_risk_actions(
        actions: List[CandidateAction],
        *,
        work_state: AgentWorkState,
        evidence: List[Dict[str, Any]],
    ) -> List[CandidateAction]:
        if (
            str(
                dict(work_state.current_snapshot.raw_meta or {}).get(
                    "numbering_backend"
                )
                or ""
            )
            != "cmalign"
        ):
            return actions
        reason_codes = [str(item.get("code") or "") for item in evidence]
        local_ambiguity_reason_codes = {
            "cm_local_45_48_insertion_deletion_ambiguity",
            "cm_position_47_neighbor_insertion_ambiguity",
            "cm_variable_bridge_coordinate_ambiguity",
        }
        multi_template_reference_search = bool(
            local_ambiguity_reason_codes.intersection(reason_codes)
        )
        enriched = list(actions)
        acceptor_evidence = next(
            (
                item
                for item in evidence
                if str(item.get("code") or "")
                == "cm_acceptor_tail_boundary_ambiguity"
            ),
            {},
        )
        endpoint_consensus_needed = bool(
            acceptor_evidence.get("endpoint_is_ambiguous")
            or str(acceptor_evidence.get("structural_check_reason") or "")
            == "acceptor_pairs_do_not_define_one_mirrored_seven_residue_arm"
        )
        if (
            "cm_acceptor_tail_boundary_ambiguity" in reason_codes
            and endpoint_consensus_needed
            and not any(
                action.action_type
                == "reconcile_acceptor_tail_with_template_consensus"
                for action in enriched
            )
        ):
            enriched.insert(
                0,
                CandidateAction(
                    action_id=(
                        "soft-risk:reconcile_acceptor_tail_with_template_consensus"
                    ),
                    action_type="reconcile_acceptor_tail_with_template_consensus",
                    specialist="acceptor_tail_reference_reviewer",
                    scope="region",
                    target_region="Aminoacyl arm 3' end",
                    reason=(
                        "acceptor_tail_reference_reviewer:request_distinct_"
                        "template_endpoint_consensus"
                    ),
                    success_condition=(
                        "at_least_three_distinct_valid_templates_agree_on_"
                        "the_66_76_coordinate_partition"
                    ),
                    rollback_condition=(
                        "retain_original_candidate_when_template_endpoint_"
                        "consensus_is_not_established"
                    ),
                    allowed_regions=[
                        "Aminoacyl arm 5' end",
                        "Aminoacyl arm 3' end",
                    ],
                    params={"soft_risk_reason_codes": reason_codes},
                ),
            )
        if (
            "cm_acceptor_tail_boundary_ambiguity" in reason_codes
            and not endpoint_consensus_needed
            and not any(
                action.action_type == "repair_acceptor_tail_with_structure"
                for action in enriched
            )
        ):
            enriched.insert(
                0,
                CandidateAction(
                    action_id="soft-risk:repair_acceptor_tail_with_structure",
                    action_type="repair_acceptor_tail_with_structure",
                    specialist="acceptor_endpoint_reviewer",
                    scope="region",
                    target_region="Aminoacyl arm 3' end",
                    reason=(
                        "acceptor_endpoint_reviewer:request_trnascan_structure_"
                        "endpoint_reconstruction"
                    ),
                    success_condition=(
                        "secondary_structure_defines_mirrored_seven_residue_"
                        "acceptor_arms_and_the_discriminator_then_candidate_"
                        "passes_shared_hard_guards"
                    ),
                    rollback_condition=(
                        "retain_original_candidate_when_structure_is_ambiguous_"
                        "or_candidate_fails_shared_validation"
                    ),
                    allowed_regions=[
                        "Aminoacyl arm 5' end",
                        "T loop + T stem",
                        "Aminoacyl arm 3' end",
                    ],
                    params={"soft_risk_reason_codes": reason_codes},
                ),
            )
        if set(reason_codes) == {"cm_acceptor_tail_boundary_ambiguity"}:
            if not bool(getattr(work_state, "current_valid", True)):
                return [
                    action
                    for action in enriched
                    if action.action_type in {
                        "repair_acceptor_tail_with_structure",
                        "reconcile_acceptor_tail_with_template_consensus",
                    }
                ]
            return enriched
        if (
            "cm_variable_boundary_structure_ambiguity" in reason_codes
            and not any(
                action.action_type
                == "reconcile_variable_boundary_with_structure"
                for action in enriched
            )
        ):
            enriched.insert(
                0,
                CandidateAction(
                    action_id=(
                        "soft-risk:reconcile_variable_boundary_with_structure"
                    ),
                    action_type="reconcile_variable_boundary_with_structure",
                    specialist="variable_boundary_reviewer",
                    scope="region",
                    target_region="Variable loop",
                    reason=(
                        "variable_boundary_reviewer:request_independent_"
                        "trnascan_structure_projection"
                    ),
                    success_condition=(
                        "independent_structure_confirms_the_49_65_t_stem_and_"
                        "a_supported_variable_boundary_then_candidate_passes_"
                        "shared_hard_guards"
                    ),
                    rollback_condition=(
                        "retain_original_candidate_when_structure_or_v_dialect_"
                        "placement_is_ambiguous"
                    ),
                    allowed_regions=["Variable loop"],
                    params={"soft_risk_reason_codes": reason_codes},
                ),
            )
        if set(reason_codes) == {
            "cm_variable_boundary_structure_ambiguity"
        }:
            return enriched
        if any(
            action.action_type == "align_legacy_reference" for action in enriched
        ):
            return enriched
        enriched.append(
            CandidateAction(
                action_id="soft-risk:align_legacy_reference",
                action_type="align_legacy_reference",
                specialist="soft_risk_reviewer",
                scope="snapshot",
                reason=(
                    "soft_risk_reviewer:request_multi_template_reference_crosscheck"
                    if multi_template_reference_search
                    else "soft_risk_reviewer:request_reference_crosscheck"
                ),
                success_condition=(
                    "distinct_valid_templates_agree_and_candidate_passes_shared_"
                    "hard_guards_and_local_score_margin"
                    if multi_template_reference_search
                    else "candidate_passes_shared_hard_guards_and_exceeds_the_"
                    "frozen_cross_backend_evidence_margin"
                ),
                rollback_condition=(
                    "retain_original_legal_candidate_on_failure_or_no_improvement"
                ),
                params={
                    "soft_risk_reason_codes": reason_codes,
                    "multi_template_reference_search": multi_template_reference_search,
                },
            )
        )
        return enriched

    @staticmethod
    def _allowed_agent_outcomes(state: AgentWorkState) -> List[str]:
        if state.current_valid:
            return ["accept"]
        if state.current_snapshot.numbering_row and state.current_snapshot.sequence_row:
            return ["investigate"]
        return ["abstain"]

    @staticmethod
    def _literature_settings(request: AlignmentRequest) -> Tuple[bool, int]:
        values = dict(request.runtime_values_override or {})
        raw_public = values.get("AGENT_LITERATURE_PUBLIC_SEARCH")
        include_public = (
            True
            if raw_public is None
            else str(raw_public or "").strip().lower() in {"1", "true", "yes", "on"}
        )
        try:
            limit = int(values.get("AGENT_LITERATURE_MAX_NOTES", agent_lookup.DEFAULT_NOTES))
        except (TypeError, ValueError):
            limit = agent_lookup.DEFAULT_NOTES
        return include_public, max(1, min(limit, agent_lookup.MAX_NOTES))

    def _apply_literature_action(
        self,
        *,
        request: AlignmentRequest,
        action: CandidateAction,
        work_state: AgentWorkState,
        observation: Dict[str, Any],
        context_summary: Dict[str, Any],
        parent_snapshot_id: str,
        before_score: float,
        before_valid: bool,
        signature: str,
        action_context_signature: Dict[str, Any],
        trace_events: List[Dict[str, Any]],
        planning_round: int,
        branch_summaries: List[Dict[str, Any]],
    ) -> None:
        """Run the reference lookup and keep the candidate untouched."""
        include_public, limit = self._literature_settings(request)
        evidence = agent_lookup.evidence_from_observation(
            observation,
            extra_regions=[
                str(item)
                for item in list((action.params or {}).get("conflict_regions") or [])
                if str(item)
            ],
        )
        lookup: Dict[str, Any] = {}
        error = ""
        try:
            lookup = agent_lookup.gather_note(
                evidence,
                include_public=include_public,
                limit=limit,
            )
        except Exception as exc:  # a missing note must not lose the case
            error = f"literature_lookup_failed:{type(exc).__name__}:{exc}"
        citations = list(lookup.get("citations") or [])
        notes = agent_lookup.as_prompt_notes(citations, limit=limit)
        finding = {
            "round": int(planning_round),
            "action_id": str(action.action_id or ""),
            "evidence": evidence,
            "queries": list(lookup.get("queries") or []),
            "citations": citations,
            "citation_ids": agent_lookup.citation_ids(citations),
            "cache": str(lookup.get("cache") or ""),
            "error": error,
        }
        work_state.literature_findings.append(dict(finding))
        self._consume_budget(work_state, action)
        work_state.attempted_signatures.append(signature)
        work_state.action_history.append(
            ActionRecord(
                action_id=str(action.action_id or ""),
                action_type=str(action.action_type or ""),
                context_signature=action_context_signature,
                target_regions=self._action_regions(action),
                accepted=True,
                score_before=before_score,
                score_after=before_score,
                valid_before=before_valid,
                valid_after=before_valid,
                notes=[
                    f"literature:citations={len(citations)}",
                    f"literature:cache={finding['cache']}",
                ]
                + ([error] if error else []),
            )
        )
        branch_summaries.append(
            {
                "snapshot_id": parent_snapshot_id,
                "source_action": str(action.action_type or ""),
                "template_name": str(work_state.current_snapshot.template_name or ""),
                "hard_valid": bool(before_valid),
                "score": float(before_score),
                "reasons": list(work_state.current_reasons),
                "accepted_as_current": True,
                "evidence_only": True,
                "reference_lookup": {
                    "queries": finding["queries"],
                    "citation_ids": finding["citation_ids"],
                    "citation_count": len(citations),
                    "cache": finding["cache"],
                },
            }
        )
        record_trace(
            request,
            trace_events,
            {
                "stage": "action_executed",
                "round": planning_round,
                "action_id": action.action_id,
                "action_type": action.action_type,
                "tool_name": action.action_type,
                "execution_class": self._action_execution_class(action),
                "disposition": "evidence_only",
                "candidate_changed": False,
                "candidate_snapshot_id": parent_snapshot_id,
                "candidate_score": float(before_score),
                "candidate_valid": bool(before_valid),
                "reference_lookup": {
                    "queries": finding["queries"],
                    "citation_ids": finding["citation_ids"],
                    "citations": notes,
                    "cache": finding["cache"],
                    "error": error,
                },
                "work_state": work_state.summary(),
            },
        )

    @staticmethod
    def _planner_observation(
        *,
        request: AlignmentRequest,
        work_state: AgentWorkState,
        context_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        meta = dict(work_state.current_snapshot.raw_meta or {})
        cm_portfolio = dict(meta.get("cm_portfolio") or {})
        cm_score = dict(meta.get("cm_score") or {})
        calibration = dict(cm_portfolio.get("calibration") or {})
        structure = dict(work_state.current_snapshot.structure_view or {})
        latest_lookup = work_state.literature_findings[-1] if work_state.literature_findings else {}
        return {
            "input_contract": AgentAlignmentService._input_contract_observation(
                request
            ),
            "current_candidate": {
                "source_action": work_state.current_snapshot.source_action,
                "numbering_backend": meta.get("numbering_backend"),
                "template_name": work_state.current_snapshot.template_name,
                "residue_count": len(work_state.current_snapshot.sequence_row),
                "structure_pair_count": len(structure.get("id_pairs") or []),
                # The manual states the rules; the row is what has to satisfy
                # them, so the Agent is shown both instead of choosing tools
                # against a count.
                "numbering_row": compact_row_view(
                    work_state.current_snapshot.numbering_row,
                    work_state.current_snapshot.sequence_row,
                ),
            },
            "input_sequence": request.normalized_target_seq(),
            "scientific_evidence": {
                "cm_bit_score": cm_score.get("bit_score"),
                "cm_average_posterior": cm_score.get("average_posterior"),
                "cm_raw_bit_score_margin": cm_portfolio.get(
                    "raw_bit_score_margin"
                ),
                "cm_calibrated_probabilities": dict(
                    calibration.get("probabilities") or {}
                ),
                "cm_calibration_disposition": calibration.get("disposition"),
                "structural_regions": dict(
                    meta.get("structural_region_evidence") or {}
                ),
            },
            "reference_evidence": (
                {
                    "round": latest_lookup.get("round"),
                    "queries": list(latest_lookup.get("queries") or []),
                    "notes": agent_lookup.as_prompt_notes(
                        list(latest_lookup.get("citations") or [])
                    ),
                }
                if latest_lookup
                else {}
            ),
            "validation": {
                "valid": bool(work_state.current_valid),
                "reasons": list(work_state.current_reasons),
                "issues": list(work_state.issues),
                "score": float(work_state.current_score),
            },
            "context": dict(context_summary or {}),
            "history": [
                {
                    "action_id": item.action_id,
                    "action_type": item.action_type,
                    "accepted": item.accepted,
                    "valid_after": item.valid_after,
                    "score_before": item.score_before,
                    "score_after": item.score_after,
                    "notes": list(item.notes),
                }
                for item in work_state.action_history[-6:]
            ],
            "budget": {
                "action_steps_remaining": max(
                    0,
                    work_state.budget.max_action_steps
                    - work_state.budget.used_action_steps,
                ),
                "planner_calls_remaining": max(
                    0,
                    work_state.budget.max_planner_calls
                    - work_state.budget.used_planner_calls,
                ),
                "region_postchecks_remaining": max(
                    0,
                    work_state.budget.max_region_postchecks
                    - work_state.budget.used_region_postchecks,
                ),
            },
        }

    @staticmethod
    def _root_recovery_enabled(request: AlignmentRequest) -> bool:
        if bool(request.enable_root_recovery):
            return True
        values = dict(request.runtime_values_override or {})
        return str(values.get("AGENT_ENABLE_CM_ROOT_RECOVERY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _infer_issues(snapshot, diagnostics, reasons: List[str]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        for region in list(diagnostics.empty_regions or []):
            issues.append({"type": "empty_region", "region": str(region), "severity": "high"})
        for warning in list(diagnostics.warning_types or []):
            region = ""
            if warning == "boundary":
                region = "Anticodon loop + Anticodon stem"
            elif warning == "anticodon":
                region = "Anticodon loop + Anticodon stem"
            elif warning == "insertion":
                region = "Variable loop"
            issues.append({"type": f"warning:{warning}", "region": region, "severity": "medium"})
        for reason in list(reasons or []):
            text = str(reason or "")
            severity = "high"
            region = ""
            if "anticodon" in text or any(k in text for k in ("34", "35", "36")):
                region = "Anticodon loop + Anticodon stem"
            elif "variable" in text or text.startswith("V"):
                region = "Variable loop"
                issues.append({"type": text, "region": region, "severity": severity})
                for extra_region in AgentAlignmentService._extra_regions_for_variable_reason(text):
                    issues.append({"type": text, "region": extra_region, "severity": severity})
                continue
            elif "acceptor" in text or text.startswith("-1"):
                region = "Aminoacyl arm"
            elif "d_stem" in text or text.startswith("d_optional"):
                region = "D loop + D stem"
            elif "t_stem" in text:
                region = "T loop + T stem"
            elif text.startswith("cca") or "74" in text or "75" in text or "76" in text:
                region = "Aminoacyl arm 3' end"
            elif "minus1" in text:
                region = "Aminoacyl arm 5' end"
            issues.append({"type": text, "region": region, "severity": severity})
        if not issues and diagnostics.warning_count > 0:
            issues.append({"type": "generic_warning", "region": "", "severity": "low"})
        return issues

    @staticmethod
    def _extra_regions_for_variable_reason(text: str) -> List[str]:
        if not text.startswith("variable_topology_"):
            return []
        labels = [part for part in re.split(r"[:\-]", text) if part]
        extras: List[str] = []
        if any(re.match(r"^(46|47|47i\d+|48|49|50|51|52|53|54|55|56|57|58|59|60|61|62|63|64|65)$", label) for label in labels):
            extras.append("T loop + T stem")
        if any(re.match(r"^(40|41|42|43|44|45)$", label) for label in labels):
            extras.append("Anticodon loop + Anticodon stem")
        return extras

    @staticmethod
    def _action_regions(action) -> List[str]:
        regions = [str(x) for x in list(action.allowed_regions or []) if str(x)]
        if not regions:
            regions = [str(x) for x in list((action.params or {}).get("focus_regions") or []) if str(x)]
        if not regions and action.target_region:
            regions = [str(action.target_region)]
        return regions

    @staticmethod
    def _budget_regions(action) -> List[str]:
        if action.target_region:
            return [str(action.target_region)]
        return AgentAlignmentService._action_regions(action)

    def _action_signature(self, action) -> str:
        focus_regions = ",".join(sorted(self._action_regions(action)))
        template_name = str((action.params or {}).get("template_name") or "")
        return "|".join(
            [
                str(action.action_type or ""),
                str(action.target_region or ""),
                focus_regions,
                template_name,
            ]
        )

    @staticmethod
    def _component_action_enabled(context, action) -> bool:
        if action.action_type == "refine_boundary":
            return bool(context.deterministic_boundary_enabled)
        if action.action_type in {
            "repair_region_with_structure_first_policy",
            "repair_region_with_template_soft_prior",
            "switch_postcheck_mode",
        }:
            return bool(context.regional_llm_candidate_enabled)
        if action.action_type == "search_literature":
            return bool(context.literature_tool_enabled)
        if action.action_type == "propose_coordinate_revision":
            return bool(context.coordinate_proposal_enabled)
        return True

    @staticmethod
    def _action_execution_class(action) -> str:
        if action.action_type in {
            "repair_region_with_structure_first_policy",
            "repair_region_with_template_soft_prior",
            "switch_postcheck_mode",
        }:
            return "bounded_llm_candidate_generator"
        if action.action_type == "search_literature":
            return "reference_lookup_evidence_only"
        if action.action_type == "propose_coordinate_revision":
            return "bounded_llm_coordinate_editor"
        return "deterministic_scientific_tool"

    # An action worth forcing before a punt is one that can move the candidate.
    # A reference lookup only adds evidence, and a route or memory action does
    # not touch the row, so neither answers "did you actually try to fix it".
    _ATTEMPTABLE_CLASSES = frozenset(
        {
            "deterministic_scientific_tool",
            "bounded_llm_candidate_generator",
            "bounded_llm_coordinate_editor",
        }
    )

    def _untried_repair_actions(
        self,
        state: AgentWorkState,
        actions: List[CandidateAction],
    ) -> List[CandidateAction]:
        """Offered actions that could still change the numbering row."""
        if state.budget.used_action_steps >= state.budget.max_action_steps:
            return []
        return [
            action
            for action in actions
            if self._action_execution_class(action) in self._ATTEMPTABLE_CLASSES
            and str(action.scope or "") in {"snapshot", "region"}
        ]

    @staticmethod
    def _escalation_requires_attempt(request: AlignmentRequest) -> bool:
        values = dict(request.runtime_values_override or {})
        raw = values.get("AGENT_ESCALATION_REQUIRES_ATTEMPT", True)
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    def _can_execute_action(self, state: AgentWorkState, action) -> bool:
        if state.budget.used_action_steps >= state.budget.max_action_steps:
            return False
        signature = self._action_signature(action)
        if signature in state.attempted_signatures:
            return False
        if action.action_type in {
            "switch_template_topk",
            "switch_template_family_constrained",
            "switch_template_domain_or_clade_specific",
            "route_to_mitochondrial_model",
            "route_to_archaeal_ncintron_mode",
        } and state.budget.used_template_switches >= state.budget.max_template_switches:
            return False
        if action.action_type in {
            "repair_region_with_structure_first_policy",
            "repair_region_with_template_soft_prior",
            "switch_postcheck_mode",
        } and state.budget.used_region_postchecks >= state.budget.max_region_postchecks:
            return False
        region_edit_limit = 3 if not state.current_valid else 2
        for region in self._budget_regions(action):
            if state.budget.repeated_region_edits.get(region, 0) >= region_edit_limit:
                return False
        return True

    @staticmethod
    def _should_accept_candidate(
        *,
        current_valid: bool,
        current_score: float,
        candidate_valid: bool,
        candidate_score: float,
        minimum_score_gain: float = 0.1,
    ) -> bool:
        if not candidate_valid:
            return False
        if not current_valid:
            return True
        return float(candidate_score) > (
            float(current_score) + max(0.0, float(minimum_score_gain))
        )

    @staticmethod
    def _accept_acceptor_endpoint_candidate(
        *,
        action: CandidateAction,
        candidate_snapshot: RootSnapshot,
        candidate_valid: bool,
        candidate_reasons: List[str] | None = None,
        parent_reasons: List[str] | None = None,
    ) -> bool:
        if action.action_type != "repair_acceptor_tail_with_structure":
            return False
        candidate_reason_set = {
            str(reason or "") for reason in list(candidate_reasons or []) if str(reason or "")
        }
        parent_reason_set = {
            str(reason or "") for reason in list(parent_reasons or []) if str(reason or "")
        }
        # A scoped endpoint repair may be carried forward when an unrelated
        # parent conflict remains unchanged. It is then only an intermediate
        # state: the final result still requires a later globally valid guard.
        progressive_acceptance = bool(
            not candidate_valid
            and candidate_reason_set
            and candidate_reason_set.issubset(parent_reason_set)
        )
        if not candidate_valid and not progressive_acceptance:
            return False
        audit = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "acceptor_tail_reconciliation"
            )
            or {}
        )
        result = dict(audit.get("result") or {})
        try:
            canonical_pair_count = int(result.get("canonical_pair_count") or 0)
            pair_count = int(result.get("pair_count") or 0)
            tail_length = int(result.get("tail_length") or 0)
        except (TypeError, ValueError):
            return False
        return bool(
            str(audit.get("status") or "") == "resolved"
            and result.get("resolved")
            and result.get("answer_free")
            and not result.get("gold_coordinates_used")
            and result.get("left_arm_reconciled")
            and canonical_pair_count >= 5
            and pair_count >= 5
            and 1 <= tail_length <= 4
            and list(result.get("changed_residue_indices") or [])
        )

    @staticmethod
    def _accept_acceptor_template_consensus_candidate(
        *,
        action: CandidateAction,
        candidate_snapshot: RootSnapshot,
        candidate_valid: bool,
    ) -> bool:
        if (
            action.action_type
            != "reconcile_acceptor_tail_with_template_consensus"
            or not candidate_valid
        ):
            return False
        audit = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "acceptor_tail_template_consensus"
            )
            or {}
        )
        return bool(
            str(audit.get("status") or "") == "resolved"
            and audit.get("established")
            and audit.get("answer_free")
            and not audit.get("gold_coordinates_used")
            and int(audit.get("distinct_sequence_support") or 0)
            >= int(audit.get("minimum_support") or 3)
            and list(audit.get("consensus_signature") or [])
            and list(audit.get("selected_changed_residue_indices") or [])
        )

    @staticmethod
    def _accept_variable_boundary_candidate(
        *,
        action: CandidateAction,
        candidate_snapshot: RootSnapshot,
        candidate_valid: bool,
    ) -> bool:
        if (
            action.action_type != "reconcile_variable_boundary_with_structure"
            or not candidate_valid
        ):
            return False
        audit = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "variable_boundary_reconciliation"
            )
            or {}
        )
        result = dict(audit.get("result") or {})
        try:
            canonical_pair_count = int(result.get("canonical_pair_count") or 0)
            pair_count = int(result.get("pair_count") or 0)
        except (TypeError, ValueError):
            return False
        pair_evidence_valid = bool(
            pair_count == 0
            or (
                3 <= pair_count <= 7
                and canonical_pair_count >= max(1, pair_count - 1)
            )
        )
        reference_consensus = dict(audit.get("reference_consensus") or {})
        long_arm_evidence_valid = bool(
            pair_count == 0
            or (
                reference_consensus.get("established")
                and int(reference_consensus.get("distinct_sequence_support") or 0)
                >= int(reference_consensus.get("minimum_support") or 2)
            )
        )
        return bool(
            str(audit.get("status") or "") == "resolved"
            and result.get("resolved")
            and result.get("answer_free")
            and not result.get("gold_coordinates_used")
            and result.get("t_stem_49_65_anchor_confirmed")
            and pair_evidence_valid
            and long_arm_evidence_valid
            and list(result.get("changed_residue_indices") or [])
        )

    @staticmethod
    def _candidate_comparison_policy(candidate_snapshot: RootSnapshot) -> str:
        variable_boundary = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "variable_boundary_reconciliation"
            )
            or {}
        )
        if str(variable_boundary.get("status") or "") == "resolved":
            return "trnascan_variable_boundary_projection_v1"
        acceptor = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "acceptor_tail_reconciliation"
            )
            or {}
        )
        if str(acceptor.get("status") or "") == "resolved":
            return "trnascan_acceptor_stem_endpoint_v1"
        acceptor_template = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "acceptor_tail_template_consensus"
            )
            or {}
        )
        if str(acceptor_template.get("status") or "") == "resolved":
            return "distinct_template_acceptor_tail_consensus_v1"
        search = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "multi_template_reference_search"
            )
            or {}
        )
        local_resolution = dict(search.get("selected_local_resolution") or {})
        if bool(search.get("consensus_established")) and bool(
            local_resolution.get("resolved")
        ):
            return "multi_template_consensus_variable_bridge_v3"
        return "validated_candidate_score_margin_v1"

    @staticmethod
    def _candidate_minimum_score_gain(
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        candidate_snapshot: RootSnapshot,
        action: CandidateAction,
    ) -> float:
        parent_backend = str(
            dict(parent_snapshot.raw_meta or {}).get("numbering_backend") or ""
        )
        candidate_backend = str(
            dict(candidate_snapshot.raw_meta or {}).get("numbering_backend") or ""
        )
        if (
            action.action_type != "align_legacy_reference"
            or parent_backend != "cmalign"
            or candidate_backend == parent_backend
        ):
            return 0.1

        values = dict(request.runtime_values_override or {})
        search = dict(
            dict(candidate_snapshot.raw_meta or {}).get(
                "multi_template_reference_search"
            )
            or {}
        )
        local_resolution = dict(search.get("selected_local_resolution") or {})
        reason_codes = {
            str(item)
            for item in list(dict(action.params or {}).get("soft_risk_reason_codes") or [])
        }
        local_ambiguity_reason_codes = {
            "cm_local_45_48_insertion_deletion_ambiguity",
            "cm_position_47_neighbor_insertion_ambiguity",
            "cm_variable_bridge_coordinate_ambiguity",
        }
        if (
            bool(local_ambiguity_reason_codes.intersection(reason_codes))
            and bool(search.get("consensus_established"))
            and bool(local_resolution.get("resolved"))
            and bool(local_resolution.get("base_preserved"))
        ):
            try:
                threshold = float(
                    values.get(
                        "AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN",
                        0.1,
                    )
                )
            except (TypeError, ValueError):
                threshold = 0.1
            return max(0.1, min(threshold, 500.0))

        try:
            threshold = float(
                values.get(
                    "AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN",
                    51.0,
                )
            )
        except (TypeError, ValueError):
            threshold = 51.0
        return max(0.1, min(threshold, 500.0))

    def _consume_budget(self, state: AgentWorkState, action) -> None:
        state.budget.used_action_steps += 1
        if action.action_type in {
            "switch_template_topk",
            "switch_template_family_constrained",
            "switch_template_domain_or_clade_specific",
            "route_to_mitochondrial_model",
            "route_to_archaeal_ncintron_mode",
        }:
            state.budget.used_template_switches += 1
        if action.action_type in {
            "repair_region_with_structure_first_policy",
            "repair_region_with_template_soft_prior",
            "switch_postcheck_mode",
        }:
            state.budget.used_region_postchecks += 1

    def _update_region_edit_counts(self, state: AgentWorkState, action) -> None:
        for region in self._budget_regions(action):
            state.budget.repeated_region_edits[region] = int(state.budget.repeated_region_edits.get(region, 0)) + 1

    @staticmethod
    def _register_no_gain(state: AgentWorkState, *, before_score: float, after_score: float) -> None:
        if float(after_score) >= float(before_score) + 0.5:
            state.budget.consecutive_no_gain = 0
        else:
            state.budget.consecutive_no_gain += 1

    def _accept_current_state(
        self,
        state: AgentWorkState,
        *,
        snapshot,
        diagnostics,
        difficulty,
        route,
        valid: bool,
        reasons: List[str],
        score: float,
    ) -> None:
        if float(score) >= float(state.current_score) + 0.5:
            state.budget.consecutive_no_gain = 0
        else:
            state.budget.consecutive_no_gain += 1
        state.current_diagnostics = diagnostics
        state.current_difficulty = difficulty
        state.current_route = route
        state.current_valid = bool(valid)
        state.current_reasons = list(reasons or [])
        state.current_score = float(score)
        state.sync_current(
            snapshot=snapshot,
            diagnostics=diagnostics,
            valid=bool(valid),
            reasons=list(reasons or []),
            score=float(score),
        )
        state.issues = self._infer_issues(snapshot, diagnostics, list(reasons or []))

    @staticmethod
    def _accept_best_state(
        state: AgentWorkState,
        *,
        snapshot,
        diagnostics,
        difficulty,
        route,
        valid: bool,
        reasons: List[str],
        score: float,
    ) -> None:
        state.best_diagnostics = diagnostics
        state.best_difficulty = difficulty
        state.best_route = route
        state.best_valid = bool(valid)
        state.best_reasons = list(reasons or [])
        state.best_score = float(score)
        state.sync_best(
            snapshot=snapshot,
            diagnostics=diagnostics,
            valid=bool(valid),
            reasons=list(reasons or []),
            score=float(score),
        )

    @staticmethod
    def _stop_reason(
        state: AgentWorkState,
        *,
        allow_quality_stop: bool = True,
    ) -> str:
        if state.budget.used_action_steps >= state.budget.max_action_steps:
            return "budget_exhausted"
        if state.budget.used_planner_calls >= state.budget.max_planner_calls:
            return "planner_budget_exhausted"
        if not allow_quality_stop:
            return ""
        if state.current_valid and not state.issues:
            return "legal_and_no_open_issues"
        if state.current_valid and state.budget.consecutive_no_gain >= 2:
            return "consecutive_no_gain"
        if (
            state.current_valid
            and state.current_difficulty.tier == "easy"
            and state.current_diagnostics.warning_count == 0
            and state.current_score >= state.best_score
        ):
            return "easy_and_stable"
        return ""

    def _maybe_select_better_root(
        self,
        *,
        request: AlignmentRequest,
        root_snapshot,
        root_diagnostics,
        root_difficulty,
        root_route,
        root_guard,
        root_score: float,
    ):
        root_search_summaries: List[Dict[str, Any]] = []
        root_search_events: List[Dict[str, Any]] = []
        enable_flag = str((request.runtime_values_override or {}).get("AGENT_ENABLE_MULTI_ROOT", "")).strip().lower()
        enable_multi_root = str((request.runtime_values_override or {}).get("AGENT_ENABLE_MULTI_ROOT", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if root_route.out_of_scope or not enable_multi_root or not self._should_run_multi_root(root_diagnostics, root_difficulty):
            return (
                root_snapshot,
                root_diagnostics,
                root_difficulty,
                root_route,
                root_guard,
                root_score,
                root_search_summaries,
                root_search_events,
            )

        candidate_templates = self._candidate_root_templates(root_snapshot, root_diagnostics, request)
        if not candidate_templates:
            return (
                root_snapshot,
                root_diagnostics,
                root_difficulty,
                root_route,
                root_guard,
                root_score,
                root_search_summaries,
                root_search_events,
            )

        baseline_screen = self._screen_root_template(
            request=request,
            template_name=root_snapshot.template_name,
            snapshot_id="root-screen-0000",
            source_action="root_candidate_screen",
        )
        screens = [baseline_screen]
        for idx, template_name in enumerate(candidate_templates, start=1):
            screens.append(
                self._screen_root_template(
                    request=request,
                    template_name=template_name,
                    snapshot_id=f"root-screen-{idx:04d}",
                    source_action="root_candidate_screen",
                )
            )

        for item in screens:
            root_search_summaries.append(
                {
                    "snapshot_id": item["snapshot"].snapshot_id,
                    "source_action": item["snapshot"].source_action,
                    "template_name": item["snapshot"].template_name,
                    "route": item["route"].route,
                    "route_confidence": item["route"].confidence,
                    "in_canonical_benchmark": item["route"].in_canonical_benchmark,
                    "out_of_scope": item["route"].out_of_scope,
                    "difficulty_tier": item["difficulty"].tier,
                    "difficulty_score": item["difficulty"].difficulty_score,
                    "hard_valid": item["guard"].valid,
                    "score": item["score"],
                    "reasons": list(item["guard"].reasons),
                    "root_candidate_screen": True,
                    "screening_use_llm": False,
                }
            )

        baseline = screens[0]
        best_screen = max(screens, key=lambda item: float(item["score"]))
        root_search_events.append(
            {
                "stage": "root_candidate_search",
                "selected_template": root_snapshot.template_name,
                "candidate_templates": [str(x) for x in candidate_templates],
                "baseline_screen_score": float(baseline["score"]),
                "best_screen_template": best_screen["snapshot"].template_name,
                "best_screen_score": float(best_screen["score"]),
            }
        )

        if best_screen["snapshot"].template_name == root_snapshot.template_name:
            return (
                root_snapshot,
                root_diagnostics,
                root_difficulty,
                root_route,
                root_guard,
                root_score,
                root_search_summaries,
                root_search_events,
            )

        candidate_root = self.root_builder.build(
            self._clone_request(
                request,
                template_name=best_screen["snapshot"].template_name,
                use_llm=request.use_llm,
                llm_reasoning=request.llm_reasoning,
            )
        )
        candidate_diag = self.difficulty_assessor.build_diagnostics(candidate_root)
        candidate_diff = self.difficulty_assessor.assess(candidate_diag, candidate_root)
        candidate_route = self.route_assessor.assess(candidate_root, candidate_diag)
        candidate_guard = self.hard_guards.validate(request, candidate_root)
        candidate_score = self.online_ranker.score(candidate_root, candidate_diag, candidate_route, candidate_guard)

        accepted = self._accept_root_replacement(
            current_score=root_score,
            current_diagnostics=root_diagnostics,
            candidate_score=candidate_score,
            candidate_diagnostics=candidate_diag,
            baseline_screen_score=float(baseline["score"]),
            best_screen_score=float(best_screen["score"]),
        )
        root_search_events.append(
            {
                "stage": "root_candidate_confirmation",
                "candidate_template": candidate_root.template_name,
                "candidate_score": float(candidate_score),
                "candidate_family_rank": int(candidate_diag.raw_signals.get("selected_family_match_rank") or 0),
                "candidate_anticodon_match": int(candidate_diag.raw_signals.get("selected_anticodon_match") or 0),
                "accepted": bool(accepted),
            }
        )
        if not accepted:
            return (
                root_snapshot,
                root_diagnostics,
                root_difficulty,
                root_route,
                root_guard,
                root_score,
                root_search_summaries,
                root_search_events,
            )

        candidate_root.snapshot_id = "root"
        candidate_root.parent_snapshot_id = None
        candidate_root.source_action = "root_snapshot"
        candidate_root.root_source = "multi_root_search"
        return (
            candidate_root,
            candidate_diag,
            candidate_diff,
            candidate_route,
            candidate_guard,
            candidate_score,
            root_search_summaries,
            root_search_events,
        )

    @staticmethod
    def _clone_request(
        request: AlignmentRequest,
        *,
        template_name: str | None,
        use_llm: bool,
        llm_reasoning: bool,
    ) -> AlignmentRequest:
        return AlignmentRequest(
            target_seq=request.target_seq,
            output_csv_path=request.output_csv_path,
            anticode=request.anticode,
            use_llm=use_llm,
            llm_reasoning=llm_reasoning,
            template_name=template_name,
            input_type=request.input_type,
            domain_hint=request.domain_hint,
            cca_status=request.cca_status,
            intron_status=request.intron_status,
            trna_class=request.trna_class,
            mitochondrial_subtype=request.mitochondrial_subtype,
            enable_root_recovery=request.enable_root_recovery,
            return_meta=True,
            runtime_values_override=dict(request.runtime_values_override or {}),
            status_callback=None,
        )

    def _screen_root_template(
        self,
        *,
        request: AlignmentRequest,
        template_name: str,
        snapshot_id: str,
        source_action: str,
    ) -> Dict[str, Any]:
        screen_snapshot = self.root_builder.build(
            self._clone_request(
                request,
                template_name=template_name,
                use_llm=False,
                llm_reasoning=False,
            )
        )
        screen_snapshot.snapshot_id = snapshot_id
        screen_snapshot.parent_snapshot_id = None
        screen_snapshot.source_action = source_action
        screen_snapshot.root_source = "multi_root_screen"
        screen_diag = self.difficulty_assessor.build_diagnostics(screen_snapshot)
        screen_diff = self.difficulty_assessor.assess(screen_diag, screen_snapshot)
        screen_route = self.route_assessor.assess(screen_snapshot, screen_diag)
        screen_guard = self.hard_guards.validate(request, screen_snapshot)
        screen_score = self.online_ranker.score(screen_snapshot, screen_diag, screen_route, screen_guard)
        return {
            "snapshot": screen_snapshot,
            "diagnostics": screen_diag,
            "difficulty": screen_diff,
            "route": screen_route,
            "guard": screen_guard,
            "score": screen_score,
        }

    @staticmethod
    def _should_run_multi_root(root_diagnostics, root_difficulty) -> bool:
        selected_family_rank = int(root_diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        strong_family_candidates = int(root_diagnostics.raw_signals.get("strong_family_candidates") or 0)
        family_rank_gap = int(root_diagnostics.raw_signals.get("family_rank_gap") or 0)
        template_margin = root_diagnostics.template_margin
        return bool(
            root_difficulty.tier in {"moderate", "hard"}
            or family_rank_gap > 0
            or (selected_family_rank >= 2 and strong_family_candidates >= 2 and template_margin is not None and template_margin <= 2.0)
        )

    @staticmethod
    def _candidate_root_templates(root_snapshot, root_diagnostics, request: AlignmentRequest) -> List[str]:
        candidates = list(root_snapshot.template_candidates or [])
        selected_name = str(root_snapshot.template_name or "")
        selected_family_rank = int(root_diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        selected_isotype_match = int(root_diagnostics.raw_signals.get("selected_isotype_match") or 0)
        selected_anticodon_match = int(root_diagnostics.raw_signals.get("selected_anticodon_match") or 0)
        selected_rerank = 0.0
        for item in candidates:
            if str(item.get("seqname") or "") == selected_name:
                try:
                    selected_rerank = float(item.get("rerank_score") or item.get("score") or 0.0)
                except Exception:
                    selected_rerank = 0.0
                break
        ranked: List[Tuple[Tuple[int, int, int, float, int], str]] = []
        for item in candidates:
            name = str(item.get("seqname") or "")
            if not name or name == selected_name:
                continue
            family_rank = int(item.get("family_match_rank") or 0)
            isotype_match = int(item.get("isotype_match") or 0)
            anticodon_match = int(item.get("anticodon_match") or 0)
            candidate_rank = int(item.get("rank") or 999)
            try:
                rerank_score = float(item.get("rerank_score") or item.get("score") or 0.0)
            except Exception:
                rerank_score = 0.0
            family_improves = family_rank > selected_family_rank
            evidence_improves = (
                isotype_match > selected_isotype_match
                or anticodon_match > selected_anticodon_match
                or rerank_score >= (selected_rerank - 0.25)
            )
            if not family_improves and not evidence_improves:
                continue
            ranked.append(
                (
                    (
                        1 if family_improves else 0,
                        anticodon_match,
                        isotype_match,
                        rerank_score,
                        -candidate_rank,
                    ),
                    name,
                )
            )
        ranked.sort(reverse=True)
        max_candidates = 2
        try:
            max_candidates = max(1, min(int((request.runtime_values_override or {}).get("AGENT_MULTI_ROOT_MAX_CANDIDATES", 2)), 4))
        except Exception:
            max_candidates = 2
        out: List[str] = []
        for _, name in ranked:
            if name not in out:
                out.append(name)
            if len(out) >= max_candidates:
                break
        return out

    @staticmethod
    def _accept_root_replacement(
        *,
        current_score: float,
        current_diagnostics,
        candidate_score: float,
        candidate_diagnostics,
        baseline_screen_score: float,
        best_screen_score: float,
    ) -> bool:
        current_family_rank = int(current_diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        candidate_family_rank = int(candidate_diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        current_anticodon_match = int(current_diagnostics.raw_signals.get("selected_anticodon_match") or 0)
        candidate_anticodon_match = int(candidate_diagnostics.raw_signals.get("selected_anticodon_match") or 0)
        if candidate_score > current_score + 0.25:
            return True
        if candidate_family_rank > current_family_rank and candidate_score >= current_score - 0.5:
            return True
        if candidate_anticodon_match > current_anticodon_match and candidate_score >= current_score - 0.25:
            return True
        if best_screen_score > baseline_screen_score + 4.0 and candidate_score >= current_score - 0.1:
            return True
        return False

    def align(self, request: AlignmentRequest) -> Tuple[str, str, List[str]]:
        response = self.align_with_meta(request)
        return response.template_name, response.csv_content, response.warnings

    def align_with_meta(self, request: AlignmentRequest) -> AlignmentResponse:
        emit_progress(request, {"stage": "started"})
        result = self.run(request)
        emit_progress(request, {"stage": "guard_evaluated", "valid": result.work_state_summary.get("best_valid")})
        emit_progress(request, {"stage": "completed", "disposition": result.work_state_summary.get("disposition", "")})
        best = result.best_snapshot
        meta = dict(best.raw_meta or {})
        warnings = list(best.warnings)
        selected_route = result.route.route
        if selected_route != "canonical":
            route_warning = f"specialized_route:{selected_route}"
            if route_warning not in warnings:
                warnings.append(route_warning)
        if result.route.out_of_scope:
            out_warning = "excluded_from_canonical_sprinzl_benchmark"
            if out_warning not in warnings:
                warnings.append(out_warning)
        soft_review = dict(
            dict(result.work_state_summary or {}).get("soft_risk_review") or {}
        )
        local_ambiguity_reason_codes = {
            "cm_local_45_48_insertion_deletion_ambiguity",
            "cm_position_47_neighbor_insertion_ambiguity",
            "cm_variable_bridge_coordinate_ambiguity",
        }
        local_trigger = next(
            (
                dict(item)
                for item in list(soft_review.get("reasons") or [])
                if isinstance(item, dict)
                and str(item.get("code") or "") in local_ambiguity_reason_codes
            ),
            {},
        )
        local_resolution = (
            candidate_local_resolution(result.root_snapshot, best, local_trigger)
            if local_trigger
            else {}
        )
        local_resolved = bool(local_resolution.get("resolved"))
        unresolved_local = (
            assess_local_coordinate_ambiguity(best) or local_trigger
            if local_trigger and not local_resolved
            else {}
        )
        local_review = {
            "triggered": bool(local_trigger),
            "trigger": local_trigger,
            "resolved": local_resolved,
            "resolution": local_resolution,
            "unresolved_evidence": dict(unresolved_local or {}),
            "policy": "multi_template_consensus_variable_bridge_v3",
        }
        if local_trigger and not local_resolved:
            ambiguity_warning = "local_variable_bridge_ambiguity_unresolved"
            if ambiguity_warning not in warnings:
                warnings.append(ambiguity_warning)
        acceptor_trigger = next(
            (
                dict(item)
                for item in list(soft_review.get("reasons") or [])
                if isinstance(item, dict)
                and str(item.get("code") or "")
                == "cm_acceptor_tail_boundary_ambiguity"
            ),
            {},
        )
        acceptor_resolution = dict(
            dict(best.raw_meta or {}).get("acceptor_tail_reconciliation") or {}
        )
        acceptor_resolved = (
            str(acceptor_resolution.get("status") or "") == "resolved"
        )
        acceptor_review = {
            "triggered": bool(acceptor_trigger),
            "trigger": acceptor_trigger,
            "resolved": acceptor_resolved,
            "resolution": acceptor_resolution,
            "policy": "trnascan_acceptor_stem_endpoint_v1",
        }
        if acceptor_trigger and not acceptor_resolved:
            acceptor_warning = "acceptor_tail_boundary_unresolved"
            if acceptor_warning not in warnings:
                warnings.append(acceptor_warning)
        variable_boundary_trigger = next(
            (
                dict(item)
                for item in list(soft_review.get("reasons") or [])
                if isinstance(item, dict)
                and str(item.get("code") or "")
                == "cm_variable_boundary_structure_ambiguity"
            ),
            {},
        )
        variable_boundary_resolution = dict(
            dict(best.raw_meta or {}).get("variable_boundary_reconciliation")
            or {}
        )
        variable_boundary_resolved = (
            str(variable_boundary_resolution.get("status") or "") == "resolved"
        )
        variable_boundary_review = {
            "triggered": bool(
                variable_boundary_trigger or variable_boundary_resolution
            ),
            "trigger": variable_boundary_trigger,
            "resolved": variable_boundary_resolved,
            "resolution": variable_boundary_resolution,
            "policy": "trnascan_variable_boundary_projection_v1",
        }
        if (
            variable_boundary_trigger
            and not variable_boundary_resolved
        ):
            variable_boundary_warning = "variable_boundary_structure_unresolved"
            if variable_boundary_warning not in warnings:
                warnings.append(variable_boundary_warning)
        meta["agent_v2"] = {
            "expanded": result.expanded,
            "expanded_search": result.expanded,
            "best_snapshot_id": best.snapshot_id,
            "best_source_action": best.source_action,
            "selected_template_name": best.template_name,
            "best_template": best.template_name,
            "best_score": result.work_state_summary.get("best_score"),
            "hard_guard_passed": result.work_state_summary.get("best_valid"),
            "difficulty_score": result.difficulty.difficulty_score,
            "difficulty_tier": result.difficulty.tier,
            "difficulty": result.difficulty.tier,
            "hard_case": result.difficulty.hard_case,
            "reasons": list(result.difficulty.reasons),
            "route": result.route.route,
            "route_confidence": result.route.confidence,
            "route_reasons": list(result.route.reasons),
            "in_canonical_benchmark": result.route.in_canonical_benchmark,
            "out_of_scope": result.route.out_of_scope,
            "warning_types": list(result.diagnostics.warning_types),
            "root_difficulty_score": result.root_difficulty.difficulty_score,
            "root_difficulty_tier": result.root_difficulty.tier,
            "root_hard_case": result.root_difficulty.hard_case,
            "root_reasons": list(result.root_difficulty.reasons),
            "root_route": result.root_route.route,
            "root_route_confidence": result.root_route.confidence,
            "root_route_reasons": list(result.root_route.reasons),
            "root_in_canonical_benchmark": result.root_route.in_canonical_benchmark,
            "root_out_of_scope": result.root_route.out_of_scope,
            "root_warning_types": list(result.root_diagnostics.warning_types),
            "context_summary": dict(result.context_summary or {}),
            "memory_hits": list(result.memory_hits or []),
            "branch_summaries": list(result.branch_summaries),
            "trace_events": list(result.trace_events),
            "work_state_summary": dict(result.work_state_summary or {}),
            "local_coordinate_review": local_review,
            "acceptor_tail_review": acceptor_review,
            "variable_boundary_review": variable_boundary_review,
        }
        return AlignmentResponse(
            template_name=best.template_name,
            csv_content=best.csv_content,
            warnings=warnings,
            meta=meta,
        )


__all__ = ["AgentAlignmentService"]
