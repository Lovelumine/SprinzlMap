# -*- coding: utf-8 -*-
"""
Section 10.3 Required Test: Agent State Schema
Validates that all dataclasses in state.py match the Run Contract JSON schema
from AGENT_REFACTOR_GOALS.md Section 8.1.1.
"""
import sys
sys.path.insert(0, ".")

try:
    import pytest
except ImportError:
    pass
from dataclasses import fields
from backend.agent.core.state import (
    TemplateCandidate,
    AnnotationContext,
    BranchConfig,
    AgentBudget,
    ActionBudgetCost,
    AgentAction,
    DeltaScore,
    AgentEvaluation,
    ObservationContext,
    TraceEvent,
    AlignmentAgentState,
)


class TestTemplateCandidate:
    def test_fields(self):
        f = {x.name for x in fields(TemplateCandidate)}
        assert f == {"template_name", "source", "rank", "similarity"}

    def test_instantiation(self):
        tc = TemplateCandidate("homoSapiens_chr6.trna12-GluTTC", "retrieval_topk", 1, 0.8125)
        assert tc.template_name == "homoSapiens_chr6.trna12-GluTTC"
        assert tc.similarity == 0.8125


class TestAnnotationContext:
    def test_fields(self):
        f = {x.name for x in fields(AnnotationContext)}
        assert "annotation_source" in f
        assert "empty_count" in f
        assert "boundary_resolved" in f


class TestBranchConfig:
    def test_fields(self):
        f = {x.name for x in fields(BranchConfig)}
        assert f == {"template_name", "use_llm", "llm_reasoning", "runtime_overrides"}

    def test_defaults(self):
        bc = BranchConfig("tpl", True, False, {})
        assert bc.runtime_overrides == {}


class TestAgentBudget:
    def test_fields(self):
        f = {x.name for x in fields(AgentBudget)}
        assert "max_branches" in f
        assert "max_llm_calls" in f
        assert "max_wall_time_sec" in f

    def test_defaults(self):
        b = AgentBudget(max_branches=5)
        assert b.max_branches == 5
        assert b.max_llm_calls >= 1
        assert b.max_wall_time_sec > 0


class TestAgentAction:
    def test_fields(self):
        f = {x.name for x in fields(AgentAction)}
        required = {"action_id", "branch_id", "parent_branch_id", "action_type", "params", "reason"}
        assert required.issubset(f)

    def test_instantiation(self):
        a = AgentAction(
            action_id="act-0001",
            branch_id="branch-0001",
            parent_branch_id="root",
            action_type="select_template",
            params={"template_name": "tpl-B"},
            reason="Try alternative template",
            budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=3),
        )
        assert a.action_type == "select_template"


class TestDeltaScore:
    def test_defaults_to_zero(self):
        d = DeltaScore()
        assert d.pair_f1 == 0.0
        assert d.map_acc == 0.0
        assert d.gap_f1 == 0.0


class TestAgentEvaluation:
    def test_fields(self):
        f = {x.name for x in fields(AgentEvaluation)}
        required = {"branch_id", "hard_valid", "pair_f1", "delta_vs_baseline"}
        assert required.issubset(f)

    def test_defaults(self):
        e = AgentEvaluation(branch_id="b1", hard_valid=True, pair_f1=0.85)
        assert e.selected_as_best is False
        assert e.reject_reasons == []
        assert e.accept_reasons == []


class TestAlignmentAgentState:
    def test_full_instantiation(self):
        """Test against the exact JSON example from Section 8.1.1."""
        seq = "GGGCCCAUAGCUCAGUUGGUAGAGCGCCAGACUGAAGAUCUGGAGGUCCUGUGUUCGAUCCACAGGGUCCCA"
        state = AlignmentAgentState(
            case_id="pair_000321",
            target_seq=seq,
            anticodon_hint="TTC",
            template_candidates=[
                TemplateCandidate("homoSapiens_chr6.trna12-GluTTC", "retrieval_topk", 1, 0.8125),
                TemplateCandidate("panTroglodytes_chr3.trna8-GluTTC", "retrieval_topk", 2, 0.8043),
            ],
            baseline_result=AgentEvaluation(branch_id="root", hard_valid=True, pair_f1=0.8421, map_acc=0.8215, gap_f1=0.3111),
            annotation_context=AnnotationContext("tRNAscan-SE", 0, [], False),
            current_branch_id="root",
            current_config=BranchConfig("homoSapiens_chr6.trna12-GluTTC", True, False, {}),
            budget=AgentBudget(max_branches=8, max_llm_calls=16, max_evaluations=16, max_wall_time_sec=120),
            trace_id="agent-run-20260325-0001",
        )
        assert state.case_id == "pair_000321"
        assert len(state.target_seq) == len(seq)
        assert len(state.template_candidates) == 2
        assert state.baseline_result.pair_f1 == 0.8421
        assert state.budget.max_branches == 8
        assert state.action_history == []
        assert state.memory_hits == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
