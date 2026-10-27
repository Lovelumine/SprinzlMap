# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, ".")

from backend.agent.contracts import (
    AlignmentRequest,
    CandidateAction,
    DiagnosticsReport,
    DifficultyReport,
    RootSnapshot,
    RouteReport,
)
from backend.agent.orchestrator.service import AgentAlignmentService
from backend.agent.orchestrator.llm_action_planner import PlannerDecision


class _FakeRootBuilder:
    def __init__(self, snapshot: RootSnapshot) -> None:
        self.snapshot = snapshot

    def build(self, request: AlignmentRequest) -> RootSnapshot:
        return self.snapshot


class _FakeDifficultyAssessor:
    def build_diagnostics(self, snapshot: RootSnapshot) -> DiagnosticsReport:
        return DiagnosticsReport(warning_count=0, warning_types=[], empty_regions=[], trnascan_hit=True)

    def assess(self, diagnostics: DiagnosticsReport, snapshot: RootSnapshot) -> DifficultyReport:
        return DifficultyReport(difficulty_score=0.53, tier="moderate", hard_case=False)


class _FakeRouteAssessor:
    def assess(self, snapshot: RootSnapshot, diagnostics: DiagnosticsReport) -> RouteReport:
        return RouteReport(route="canonical", confidence=0.9, in_canonical_benchmark=True, out_of_scope=False)


class _FakePlanner:
    class _Ctx:
        def summary(self):
            return {"visible_issue": True}

    def __init__(self) -> None:
        self.called = False

    def plan(self, request, root_snapshot, diagnostics, difficulty, route, guard_reasons=None):
        self.called = True
        return self._Ctx(), [], []


class _DeterministicActionPlanner:
    def decide(self, *, actions, allowed_outcomes, **kwargs):
        candidates = list(actions)
        outcomes = list(allowed_outcomes)
        if candidates:
            return PlannerDecision(
                decision="tool",
                action_id=candidates[0].action_id,
                reason_code="test_action",
                planner_source="test",
            )
        return PlannerDecision(
            decision=outcomes[0] if outcomes else "abstain",
            reason_code="test_outcome",
            planner_source="test",
        )

    def plan_from_work_state(self, request, work_state):
        self.called = True
        return self._Ctx(), [], []


def test_agent_planner_sees_structure_topology_violation_and_does_not_stop_early():
    snapshot = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "V11", "V12", "V13", "V23", "V22", "V21", "46", "47", "48"],
        template_row=["-"] * 11,
        sequence_row=["U", "G", "G", "G", "C", "U", "G", "C", "C", "U", "C"],
        structure_view={
            "id_pairs": [
                {"left_id": "V11", "right_id": "46", "left_index": 3, "right_index": 9},
            ]
        },
        raw_meta={"annotation_meta": {"trnascan_hit": False, "trnascan_anticodon": ""}},
    )
    planner = _FakePlanner()
    service = AgentAlignmentService(
        root_builder=_FakeRootBuilder(snapshot),
        difficulty_assessor=_FakeDifficultyAssessor(),
        route_assessor=_FakeRouteAssessor(),
        planner=planner,
        action_planner=_DeterministicActionPlanner(),
    )

    result = service.run(
        AlignmentRequest(
            target_seq="UGGGCUGCCUC",
            use_llm=True,
            runtime_values_override={"AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only"},
        )
    )

    assert planner.called is True
    assert result.work_state_summary["stop_reason"] == "agent_investigate"
    assert result.work_state_summary["issue_count"] > 0
    assert result.branch_summaries[0]["hard_valid"] is False
    assert result.branch_summaries[0]["reasons"] == ["variable_topology_pair_violation:V11-46"]


def test_variable_topology_into_t_region_is_visible_as_variable_and_t_regions():
    issues = AgentAlignmentService._infer_issues(
        snapshot=None,
        diagnostics=DiagnosticsReport(warning_count=0, warning_types=[], empty_regions=[], trnascan_hit=True),
        reasons=["variable_topology_pair_violation:V11-47i2"],
    )

    issue_regions = [item["region"] for item in issues]

    assert "Variable loop" in issue_regions
    assert "T loop + T stem" in issue_regions


class _FailingPlanner(_FakePlanner):
    def plan_from_work_state(self, request, work_state):
        self.called = True
        return self._Ctx(), [], [
            CandidateAction(
                action_type="align_numbering_cm",
                action_id="failing-cm",
                specialist="cm_numbering_specialist",
                allowed_regions=["Variable loop"],
                success_condition="generate_candidate",
                rollback_condition="reject_tool_failure",
            )
        ]


class _FailingBranchExecutor:
    def __init__(self, root_snapshot):
        self.root_snapshot = root_snapshot

    def execute(self, **kwargs):
        if kwargs["action"].action_type == "align_legacy_reference":
            return self.root_snapshot
        raise RuntimeError("simulated tool failure")


def test_tool_failure_is_recorded_and_does_not_abort_agent_run():
    snapshot = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "V11", "46", "47", "48"],
        template_row=["-"] * 6,
        sequence_row=["U", "G", "G", "-", "-", "-"],
        raw_meta={"annotation_meta": {"trnascan_hit": False}},
    )
    service = AgentAlignmentService(
        root_builder=_FakeRootBuilder(snapshot),
        difficulty_assessor=_FakeDifficultyAssessor(),
        route_assessor=_FakeRouteAssessor(),
        planner=_FailingPlanner(),
        action_planner=_DeterministicActionPlanner(),
        branch_executor=_FailingBranchExecutor(snapshot),
    )

    result = service.run(AlignmentRequest(target_seq="UGG", use_llm=True))

    failures = [event for event in result.trace_events if event.get("stage") == "action_failed"]
    assert len(failures) == 1
    assert failures[0]["action_type"] == "align_numbering_cm"
    assert failures[0]["error_type"] == "RuntimeError"
    assert failures[0]["disposition"] == "rejected"
    assert result.work_state_summary["stop_reason"] == "agent_investigate"
    assert result.work_state_summary["action_history"][0]["accepted"] is False
