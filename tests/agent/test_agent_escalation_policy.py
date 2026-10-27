# -*- coding: utf-8 -*-
"""A punt has to be earned: one offered repair must be tried first."""
from __future__ import annotations

from backend.agent.contracts import (
    AlignmentRequest,
    CandidateAction,
    DiagnosticsReport,
    DifficultyReport,
    RootSnapshot,
    RouteReport,
)
from backend.agent.orchestrator.llm_action_planner import PlannerDecision
from backend.agent.orchestrator.service import AgentAlignmentService


def _proposal_action() -> CandidateAction:
    return CandidateAction(
        action_id="coordinate_proposal_specialist:1:propose_coordinate_revision",
        action_type="propose_coordinate_revision",
        specialist="coordinate_proposal_specialist",
        scope="snapshot",
        reason="test:model_rewrites_flagged_regions",
        allowed_regions=["Variable loop"],
        success_condition="proposed_row_keeps_every_input_base_and_scores_higher",
        rollback_condition="retain_parent_when_guards_or_score_reject",
    )


class _RootBuilder:
    def __init__(self, snapshot: RootSnapshot) -> None:
        self.snapshot = snapshot

    def build(self, request: AlignmentRequest) -> RootSnapshot:
        return self.snapshot


class _Assessor:
    def build_diagnostics(self, snapshot: RootSnapshot) -> DiagnosticsReport:
        return DiagnosticsReport(
            warning_count=1, warning_types=["generic"], empty_regions=[], trnascan_hit=False
        )

    def assess(self, diagnostics, snapshot) -> DifficultyReport:
        return DifficultyReport(difficulty_score=0.7, tier="hard", hard_case=True)


class _RouteAssessor:
    def assess(self, snapshot, diagnostics) -> RouteReport:
        return RouteReport(
            route="canonical", confidence=0.9, in_canonical_benchmark=True, out_of_scope=False
        )


class _OneActionPlanner:
    """The toolbox always offers exactly the proposal, and never more."""

    def __init__(self) -> None:
        self.rounds = 0

    def plan_from_work_state(self, request, work_state):
        self.rounds += 1
        if self.rounds > 1:
            # A proposal needs the disputed region, and the row already carries
            # it, so the second round keeps offering the same single action.
            return _context(), [], [_proposal_action()]
        return _context(), [], [_proposal_action()]


def _context():
    from backend.agent.orchestrator.context_engineering import SpecialistContext

    return SpecialistContext(
        target_length=11,
        anticodon_hint="CAU",
        use_llm=True,
        llm_reasoning=False,
        selected_template="tpl",
        validation_reasons=["variable_singleton_occupied:V11"],
        failure_regions=["Variable loop"],
        coordinate_proposal_enabled=True,
        cm_tool_enabled=True,
        numbering_backend="cmalign",
    )


def _refusing_planner(punt_once: bool = True):
    calls: list[dict] = []

    class _Planner:
        def decide(self, *, request, phase, observation, actions, allowed_outcomes=()):
            calls.append(
                {
                    "phase": phase,
                    "allowed_outcomes": list(allowed_outcomes),
                    "actions": [action.action_type for action in actions],
                }
            )
            outcomes = list(allowed_outcomes)
            if outcomes and (punt_once or len(calls) < 3):
                return PlannerDecision(
                    decision=outcomes[0],
                    reason_code=f"test_{outcomes[0]}",
                    planner_source="test",
                )
            candidates = list(actions)
            if candidates:
                return PlannerDecision(
                    decision="tool",
                    action_id=candidates[0].action_id,
                    reason_code="test_tool_after_deferral",
                    planner_source="test",
                )
            return PlannerDecision(
                decision="abstain", reason_code="test_no_actions", planner_source="test"
            )

    return _Planner(), calls


def _snapshot() -> RootSnapshot:
    return RootSnapshot(
        template_name="tpl",
        csv_content="",
        # A repeated label fails the shared guards, so the round is offered
        # "investigate" as its only terminal outcome.
        numbering_row=["-1", "1", "2", "2", "4", "5", "6", "7", "34", "35", "36"],
        template_row=["-"] * 11,
        sequence_row=["G", "G", "C", "A", "U", "G", "G", "C", "A", "A", "G"],
        raw_meta={"annotation_meta": {"trnascan_hit": False}},
        snapshot_id="root",
    )


def _service(planner, action_planner) -> AgentAlignmentService:
    return AgentAlignmentService(
        root_builder=_RootBuilder(_snapshot()),
        difficulty_assessor=_Assessor(),
        route_assessor=_RouteAssessor(),
        planner=planner,
        action_planner=action_planner,
    )


def test_a_punt_with_an_untried_repair_is_asked_again_with_only_tools():
    action_planner, calls = _refusing_planner()
    service = _service(_OneActionPlanner(), action_planner)

    result = service.run(
        AlignmentRequest(
            target_seq="GGCAUGGCAAG",
            use_llm=True,
            runtime_values_override={
                "AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only",
                "AGENT_ENABLE_COORDINATE_PROPOSAL": True,
            },
        )
    )

    assert calls[0]["allowed_outcomes"] == ["investigate"]
    assert calls[1]["allowed_outcomes"] == []
    assert calls[1]["phase"] == "repair_required"
    assert any(
        event.get("stage") == "escalation_deferred_pending_attempt"
        for event in result.trace_events
    )
    assert result.work_state_summary["escalation_deferrals"] == 1


def test_a_second_refusal_keeps_the_disposition_the_model_first_asked_for():
    # The deferral buys one attempt, not a verdict the model never gave: after
    # the forced round the original investigate still stands.
    action_planner, calls = _refusing_planner(punt_once=False)
    service = _service(_OneActionPlanner(), action_planner)

    result = service.run(
        AlignmentRequest(
            target_seq="GGCAUGGCAAG",
            use_llm=True,
            runtime_values_override={
                "AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only",
                "AGENT_ENABLE_COORDINATE_PROPOSAL": True,
            },
        )
    )

    assert result.work_state_summary["disposition"] == "investigate"
    assert result.work_state_summary["escalation_deferrals"] == 1


def test_the_policy_can_be_turned_off_for_an_ablation():
    action_planner, calls = _refusing_planner()
    service = _service(_OneActionPlanner(), action_planner)

    result = service.run(
        AlignmentRequest(
            target_seq="GGCAUGGCAAG",
            use_llm=True,
            runtime_values_override={
                "AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only",
                "AGENT_ENABLE_COORDINATE_PROPOSAL": True,
                "AGENT_ESCALATION_REQUIRES_ATTEMPT": False,
            },
        )
    )

    assert len(calls) == 1
    assert calls[0]["allowed_outcomes"] == ["investigate"]
    assert result.work_state_summary["escalation_deferrals"] == 0


def test_an_evidence_only_action_does_not_earn_an_attempt():
    # A reference lookup adds evidence but cannot move a coordinate, so a punt
    # with only that on the table is not held back.
    action_planner, calls = _refusing_planner()
    planner = _OneActionPlanner()

    def plan_only_lookup(request, work_state):
        planner.rounds += 1
        return (
            _context(),
            [],
            [
                CandidateAction(
                    action_id="literature_specialist:1:search_literature",
                    action_type="search_literature",
                    specialist="literature_specialist",
                    scope="snapshot",
                    reason="test:regional_reference_evidence",
                )
            ],
        )

    planner.plan_from_work_state = plan_only_lookup
    service = _service(planner, action_planner)

    result = service.run(
        AlignmentRequest(
            target_seq="GGCAUGGCAAG",
            use_llm=True,
            runtime_values_override={"AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only"},
        )
    )

    assert len(calls) == 1
    assert result.work_state_summary["escalation_deferrals"] == 0
