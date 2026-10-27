# -*- coding: utf-8 -*-
"""The Agent may ask for a reference note, and that may not touch the numbering."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from backend.agent.contracts import (
    ActionBudgetCost,
    AlignmentRequest,
    CandidateAction,
    DiagnosticsReport,
    DifficultyReport,
    RootSnapshot,
    RouteReport,
)
from backend.agent.orchestrator.context_engineering import SpecialistContext
from backend.agent.orchestrator.llm_action_planner import (
    ConstrainedLLMActionPlanner,
    FixedActionPlanner,
    PlannerDecision,
)
from backend.agent.orchestrator.planner import AgentPlannerV2
from backend.agent.orchestrator.service import AgentAlignmentService
from backend.agent.orchestrator.specialists import LiteratureSpecialist
from backend.domain.literature import agent_lookup


def _literature_action(action_id: str = "literature_specialist:1:search_literature"):
    return CandidateAction(
        action_id=action_id,
        action_type="search_literature",
        specialist="literature_specialist",
        scope="snapshot",
        params={"conflict_regions": ["Variable loop"]},
        reason="literature_specialist:regional_reference_evidence",
        budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=3),
        allowed_regions=["Variable loop"],
        success_condition="return_reference_notes_without_changing_the_candidate",
        rollback_condition="no_candidate_change_to_roll_back",
    )


def _context(**overrides) -> SpecialistContext:
    values = {
        "target_length": 72,
        "anticodon_hint": "UUC",
        "use_llm": True,
        "llm_reasoning": False,
        "selected_template": "tpl",
        "difficulty_tier": "moderate",
        "failure_regions": ["Variable loop"],
        "numbering_backend": "legacy",
        "literature_tool_enabled": True,
    }
    values.update(overrides)
    return SpecialistContext(**values)


def test_specialist_proposes_only_when_the_tool_is_enabled():
    specialist = LiteratureSpecialist()
    assert specialist.propose(_context(literature_tool_enabled=False)) == []
    assert specialist.propose(_context(use_llm=False)) == []
    assert specialist.propose(_context(failure_regions=[], difficulty_tier="easy")) == []
    actions = specialist.propose(_context())
    assert [action.action_type for action in actions] == ["search_literature"]
    assert actions[0].budget_cost.llm_calls_reserved == 0
    assert actions[0].success_condition.endswith("without_changing_the_candidate")


def test_planner_offers_the_reference_lookup_last_but_still_offers_it():
    planner = AgentPlannerV2()
    root = RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["44", "45", "46", "47", "48"],
        template_row=["-"] * 5,
        sequence_row=["U", "G", "A", "C", "G"],
        raw_meta={"annotation_meta": {"trnascan_hit": False}},
    )
    diagnostics = DiagnosticsReport(
        warning_count=2,
        warning_types=["insertion", "boundary"],
        empty_regions=["D loop + D stem"],
        trnascan_hit=False,
    )
    difficulty = DifficultyReport(difficulty_score=0.9, tier="hard", hard_case=True)
    route = RouteReport(
        route="canonical", confidence=0.9, in_canonical_benchmark=True, out_of_scope=False
    )
    _context_value, _hits, actions = planner.plan(
        AlignmentRequest(target_seq="UGACG", use_llm=True),
        root,
        diagnostics,
        difficulty,
        route,
        guard_reasons=["duplicate_numbering_labels:47"],
    )
    action_types = [action.action_type for action in actions]
    assert "search_literature" in action_types
    assert action_types[-1] == "search_literature"
    assert len(actions) >= 2


def test_fixed_policy_never_selects_the_reference_lookup_first():
    actions = [
        CandidateAction(action_id="a1", action_type="align_legacy_reference"),
        _literature_action(),
    ]
    decision = FixedActionPlanner().decide(
        request=AlignmentRequest(target_seq="UGACG", use_llm=True),
        phase="evidence_review",
        observation={},
        actions=actions,
        allowed_outcomes=["accept"],
    )
    assert decision.action_id == "a1"


class _RootBuilder:
    def __init__(self, snapshot: RootSnapshot) -> None:
        self.snapshot = snapshot

    def build(self, request: AlignmentRequest) -> RootSnapshot:
        return self.snapshot


class _Assessor:
    def build_diagnostics(self, snapshot: RootSnapshot) -> DiagnosticsReport:
        return DiagnosticsReport(
            warning_count=0, warning_types=[], empty_regions=[], trnascan_hit=False
        )

    def assess(self, diagnostics, snapshot) -> DifficultyReport:
        return DifficultyReport(difficulty_score=0.5, tier="moderate", hard_case=False)


class _RouteAssessor:
    def assess(self, snapshot, diagnostics) -> RouteReport:
        return RouteReport(
            route="canonical", confidence=0.9, in_canonical_benchmark=True, out_of_scope=False
        )


class _OneActionPlanner:
    def __init__(self, action: CandidateAction) -> None:
        self.action = action
        self.rounds = 0

    def plan_from_work_state(self, request, work_state):
        self.rounds += 1
        context = _context(validation_reasons=list(work_state.current_reasons))
        if self.rounds > 1:
            return context, [], []
        return context, [], [self.action]


class _ToolChoosingActionPlanner:
    def __init__(self) -> None:
        self.observations = []

    def decide(self, *, request, phase, observation, actions, allowed_outcomes=()):
        self.observations.append(dict(observation))
        candidates = list(actions)
        if candidates:
            return PlannerDecision(
                decision="tool",
                action_id=candidates[0].action_id,
                reason_code="test_literature",
                planner_source="test",
            )
        outcomes = list(allowed_outcomes) or ["abstain"]
        return PlannerDecision(
            decision=outcomes[0],
            reason_code=f"test_{outcomes[0]}",
            planner_source="test",
        )


def _root_snapshot() -> RootSnapshot:
    return RootSnapshot(
        template_name="tpl",
        csv_content="",
        numbering_row=["-1", "1", "2", "2", "4", "5", "6", "7", "34", "35", "36"],
        template_row=["-"] * 11,
        sequence_row=["G", "G", "C", "A", "U", "G", "G", "C", "A", "A", "G"],
        raw_meta={"annotation_meta": {"trnascan_hit": False}},
        snapshot_id="root",
    )


def test_reference_lookup_leaves_the_candidate_untouched(monkeypatch):
    calls = []

    def fake_gather_note(evidence, *, include_public, limit):
        calls.append({"evidence": dict(evidence), "include_public": include_public, "limit": limit})
        return {
            "queries": [("tRNA variable loop length variation positions 44 to 48", "variable_loop")],
            "citations": [
                {
                    "source": "local_pdf",
                    "doc_key": "sprinzl1978",
                    "title": "Compilation of tRNA genes",
                    "year": "1978",
                    "venue": "Nucleic Acids Res",
                    "doi": "",
                    "snippet": "The variable arm ranges from four to five residues in class I tRNAs.",
                    "score": 0.9,
                }
            ],
            "cache": "miss",
        }

    monkeypatch.setattr(agent_lookup, "gather_note", fake_gather_note)

    snapshot = _root_snapshot()
    action_planner = _ToolChoosingActionPlanner()
    service = AgentAlignmentService(
        root_builder=_RootBuilder(snapshot),
        difficulty_assessor=_Assessor(),
        route_assessor=_RouteAssessor(),
        planner=_OneActionPlanner(_literature_action()),
        action_planner=action_planner,
    )
    result = service.run(
        AlignmentRequest(
            target_seq="GGCAUGGCAAG",
            use_llm=True,
            runtime_values_override={"AGENT_CANONICAL_ROOT_STRATEGY": "legacy_only"},
        )
    )

    assert len(calls) == 1
    assert calls[0]["include_public"] is True
    executed = [
        event
        for event in result.trace_events
        if event.get("stage") == "action_executed"
        and event.get("action_type") == "search_literature"
    ]
    assert len(executed) == 1
    assert executed[0]["disposition"] == "evidence_only"
    assert executed[0]["candidate_changed"] is False
    assert executed[0]["reference_lookup"]["citation_ids"] == ["local:sprinzl1978"]

    # The numbering is what it was before the lookup.
    assert list(result.best_snapshot.numbering_row) == list(snapshot.numbering_row)
    assert result.work_state_summary["literature_lookup_count"] == 1
    assert result.work_state_summary["action_history"][0]["accepted"] is True

    # The next planning round reads the note back, phrased for the region at issue.
    second_round = action_planner.observations[1]
    notes = second_round["reference_evidence"]["notes"]
    assert notes[0]["title"] == "Compilation of tRNA genes"
    assert len(notes[0]["excerpt"]) <= agent_lookup.NOTE_EXCERPT_CHARS
    assert second_round["input_contract"]["input_type"]
    assert calls[0]["evidence"]["conflict_regions"] == ["variable_loop"]


def test_planner_prompt_carries_the_coordinate_digest():
    request = AlignmentRequest(target_seq="GGCAUGG", use_llm=True)
    default_round = ConstrainedLLMActionPlanner._messages(
        phase="evidence_review",
        observation={},
        actions=[],
        allowed_outcomes=["accept"],
    )
    full = ConstrainedLLMActionPlanner._messages(
        phase="evidence_review",
        observation={},
        actions=[],
        allowed_outcomes=["accept"],
        manual_mode="full",
    )
    without_manual = ConstrainedLLMActionPlanner._messages(
        phase="evidence_review",
        observation={},
        actions=[],
        allowed_outcomes=["accept"],
        manual_mode="off",
    )
    assert "REFERENCE -" in default_round[0]["content"]
    assert "REFERENCE - SPRINZL COORDINATE SYSTEM" not in default_round[0]["content"]
    assert "REFERENCE - SPRINZL COORDINATE SYSTEM" in full[0]["content"]
    assert "REFERENCE -" not in without_manual[0]["content"]
    assert (
        len(default_round[0]["content"])
        < len(full[0]["content"]) * 0.75
    )
    assert ConstrainedLLMActionPlanner._numbering_manual_mode(request) == "brief"
    disabled = AlignmentRequest(
        target_seq="GGCAUGG",
        use_llm=True,
        runtime_values_override={"AGENT_NUMBERING_MANUAL": "off"},
    )
    assert ConstrainedLLMActionPlanner._numbering_manual_mode(disabled) == "off"
    legacy_false = AlignmentRequest(
        target_seq="GGCAUGG",
        use_llm=True,
        runtime_values_override={"AGENT_NUMBERING_MANUAL": False},
    )
    legacy_true = AlignmentRequest(
        target_seq="GGCAUGG",
        use_llm=True,
        runtime_values_override={"AGENT_NUMBERING_MANUAL": True},
    )
    assert ConstrainedLLMActionPlanner._numbering_manual_mode(legacy_false) == "off"
    assert ConstrainedLLMActionPlanner._numbering_manual_mode(legacy_true) == "full"
