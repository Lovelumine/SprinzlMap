# -*- coding: utf-8 -*-
"""
Section 10.3 Required Test: Agent Executor Isolation
Validates that the executor correctly wraps the pipeline with branch-local configs
and does NOT contaminate shared state.
"""
import sys
sys.path.insert(0, ".")

try:
    import pytest
except ImportError:
    pass
from backend.agent.core.executor import BranchExecutionResult, _parse_csv_alignment
from backend.agent.core.state import (
    ActionBudgetCost,
    AgentAction,
    AgentBudget,
    AgentEvaluation,
    AlignmentAgentState,
    AnnotationContext,
    BranchConfig,
    TemplateCandidate,
)

_DEFAULT_COST = ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=3)
from backend.agent.core.planner import AlignmentPlanner


class TestCSVParsing:
    """Validates internal CSV parsing for pipeline output."""

    def test_parse_three_row_csv(self):
        csv_raw = "1,2,3\nA,C,G\nA,-,G\n"
        numbering, template, sequence = _parse_csv_alignment(csv_raw)
        assert numbering == ["1", "2", "3"]
        assert template == ["A", "C", "G"]
        assert sequence == ["A", "-", "G"]

    def test_parse_empty_csv(self):
        numbering, template, sequence = _parse_csv_alignment("")
        assert numbering == []
        assert template == []
        assert sequence == []

    def test_parse_malformed_csv(self):
        numbering, template, sequence = _parse_csv_alignment("only one row\n")
        assert numbering == []
        assert template == []
        assert sequence == []


class TestBranchExecutionResult:
    def test_default_values(self):
        r = BranchExecutionResult(
            branch_id="b1",
            template_name_used="tpl",
            csv_raw="",
            warnings=[],
            wall_time_sec=0.0,
        )
        assert r.error is None
        assert r.aligned_numbering == []
        assert r.aligned_sequence == []


class TestActionToConfig:
    """Validates that the Planner correctly translates actions to branch configs."""

    def _make_planner(self):
        state = AlignmentAgentState(
            case_id="test",
            target_seq="ACGU",
            anticodon_hint="",
            template_candidates=[TemplateCandidate("A", "r", 1, 0.9)],
            baseline_result=AgentEvaluation(branch_id="root", hard_valid=True, pair_f1=0.5),
            annotation_context=AnnotationContext("test", 0, [], False),
            current_branch_id="root",
            current_config=BranchConfig("A", True, False, {}),
            budget=AgentBudget(max_branches=5),
            trace_id="test",
        )
        return AlignmentPlanner(state)

    def test_select_template_config(self):
        pl = self._make_planner()
        action = AgentAction(
            action_id="a1", branch_id="b1", parent_branch_id="root",
            action_type="select_template",
            params={"template_name": "new_tpl", "rank": 2, "similarity": 0.8},
            reason="test",
            budget_cost=_DEFAULT_COST,
        )
        config = pl._action_to_config(action)
        assert config.template_name == "new_tpl"
        assert config.use_llm is True  # inherited from base

    def test_dp_only_config(self):
        pl = self._make_planner()
        action = AgentAction(
            action_id="a2", branch_id="b2", parent_branch_id="root",
            action_type="force_dp_only_for_region",
            params={"region": "all"},
            reason="test",
            budget_cost=_DEFAULT_COST,
        )
        config = pl._action_to_config(action)
        assert config.use_llm is False
        assert config.llm_reasoning is False

    def test_reasoning_config(self):
        pl = self._make_planner()
        action = AgentAction(
            action_id="a3", branch_id="b3", parent_branch_id="root",
            action_type="switch_postcheck_mode",
            params={"region": "all", "preset_name": "llm_reasoning_on"},
            reason="test",
            budget_cost=_DEFAULT_COST,
        )
        config = pl._action_to_config(action)
        assert config.use_llm is True
        assert config.llm_reasoning is True

    def test_base_config_unchanged(self):
        """Verify that creating new configs doesn't mutate the original state."""
        pl = self._make_planner()
        original_template = pl.state.current_config.template_name
        action = AgentAction(
            action_id="a4", branch_id="b4", parent_branch_id="root",
            action_type="select_template",
            params={"template_name": "completely_different"},
            reason="test",
            budget_cost=_DEFAULT_COST,
        )
        _ = pl._action_to_config(action)
        # Original state must be unchanged
        assert pl.state.current_config.template_name == original_template


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
