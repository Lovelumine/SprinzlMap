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
from backend.agent.orchestrator.context_engineering import (
    ContextEngineeringLayer,
    SpecialistContext,
)
from backend.agent.orchestrator.service import AgentAlignmentService
from backend.agent.orchestrator.specialists import (
    BoundarySpecialist,
    RegionRepairSpecialist,
    VariableBoundarySpecialist,
)


def test_boundary_specialist_triggers_on_variable_failure_region():
    context = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=False,
        selected_template="tpl",
        difficulty_tier="moderate",
        failure_regions=["Variable loop"],
    )

    actions = BoundarySpecialist().propose(context)

    assert [action.action_type for action in actions] == ["refine_boundary"]
    assert actions[0].target_region == "Anticodon loop + Anticodon stem"
    assert all(action.budget_cost.llm_calls_reserved == 0 for action in actions)


def test_disabled_boundary_component_is_absent_from_agent_allowlist():
    context = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=False,
        selected_template="tpl",
        deterministic_boundary_enabled=False,
        difficulty_tier="moderate",
        failure_regions=["Variable loop"],
    )

    assert BoundarySpecialist().propose(context) == []


def test_regional_boundary_candidate_is_independent_from_deterministic_tool():
    context = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=True,
        selected_template="tpl",
        regional_llm_candidate_enabled=True,
        deterministic_boundary_enabled=False,
        difficulty_tier="moderate",
        failure_regions=["Anticodon loop + Anticodon stem"],
    )

    actions = BoundarySpecialist().propose(context)

    assert [action.action_type for action in actions] == ["switch_postcheck_mode"]
    assert actions[0].budget_cost.llm_calls_reserved == 1


def test_region_repair_specialist_expands_variable_focus_into_t_region():
    context = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=False,
        selected_template="tpl",
        regional_llm_candidate_enabled=True,
        difficulty_tier="moderate",
        failure_regions=["Variable loop", "T loop + T stem"],
    )

    actions = RegionRepairSpecialist().propose(context)

    assert actions[0].action_type == "repair_variable_topology_locally"
    assert actions[0].params["focus_regions"] == ["Variable loop", "T loop + T stem"]
    assert actions[1].params["focus_regions"] == ["Variable loop", "T loop + T stem"]
    assert actions[2].params["focus_regions"] == ["Variable loop", "T loop + T stem"]


def test_disabled_regional_llm_component_is_absent_from_agent_allowlist():
    context = SpecialistContext(
        target_length=90,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=False,
        selected_template="tpl",
        regional_llm_candidate_enabled=False,
        difficulty_tier="moderate",
        failure_regions=["Variable loop", "T loop + T stem"],
    )

    actions = RegionRepairSpecialist().propose(context)

    assert [action.action_type for action in actions] == [
        "repair_variable_topology_locally"
    ]


def test_variable_boundary_specialist_handles_cm_bridge_guard_failure():
    context = SpecialistContext(
        target_length=84,
        anticodon_hint="",
        use_llm=True,
        llm_reasoning=False,
        selected_template="arch-num",
        numbering_backend="cmalign",
        canonical_eligible=True,
        validation_reasons=[
            "variable_bridge_structure_review_required:observed=46,47,47i1,48"
        ],
    )

    actions = VariableBoundarySpecialist().propose(context)

    assert [action.action_type for action in actions] == [
        "reconcile_variable_boundary_with_structure"
    ]
    assert actions[0].allowed_regions == ["Variable loop"]
    assert actions[0].budget_cost.llm_calls_reserved == 0


def test_runtime_component_flags_enter_specialist_context_and_filter_memory_actions():
    request = AlignmentRequest(
        target_seq="AC",
        runtime_values_override={
            "AGENT_ENABLE_LLM_POSTCHECK": True,
            "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT": False,
        },
    )
    context = ContextEngineeringLayer().build(
        request,
        RootSnapshot(template_name="tpl", csv_content=""),
        DiagnosticsReport(),
        DifficultyReport(difficulty_score=0.0, tier="easy", hard_case=False),
        RouteReport(),
    )

    assert context.regional_llm_candidate_enabled is True
    assert context.deterministic_boundary_enabled is False
    assert AgentAlignmentService._component_action_enabled(
        context,
        CandidateAction(action_type="repair_region_with_structure_first_policy"),
    )
    assert not AgentAlignmentService._component_action_enabled(
        context,
        CandidateAction(action_type="refine_boundary"),
    )
