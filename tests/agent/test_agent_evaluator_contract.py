# -*- coding: utf-8 -*-
"""
Section 10.3 Required Test: Agent Evaluator Contract
Validates hard gate enforcement, delta scoring, and accept/reject logic.
"""
import sys
sys.path.insert(0, ".")

try:
    import pytest
except ImportError:
    pass
from backend.agent.core.evaluator import ObjectiveEvaluator
from backend.agent.core.state import AgentEvaluation, DeltaScore


class TestHardGate:
    """The base-preservation hard gate must NEVER be bypassed."""

    def test_accept_correct_sequence(self):
        ev = ObjectiveEvaluator(target_seq="GGGCCCAUAGCUCAGUU")
        r = ev.evaluate_branch("b1", list("GGGCCCAUAGCUCAGUU"), list(range(17)), pair_f1=0.9)
        assert r.hard_valid is True
        assert r.reject_reasons == []

    def test_reject_wrong_sequence(self):
        ev = ObjectiveEvaluator(target_seq="GGGCCCAUAGCUCAGUU")
        r = ev.evaluate_branch("b1", list("AAAA"), list(range(4)), pair_f1=0.99)
        assert r.hard_valid is False
        assert len(r.reject_reasons) >= 1
        assert "Base preservation" in r.reject_reasons[0]

    def test_accept_with_gaps(self):
        """Gaps should be stripped before comparison."""
        ev = ObjectiveEvaluator(target_seq="ACGU")
        aligned = list("A-C-G-U")  # dashes are gaps
        r = ev.evaluate_branch("b1", aligned, list(range(7)), pair_f1=0.8)
        assert r.hard_valid is True

    def test_reject_empty_sequence(self):
        ev = ObjectiveEvaluator(target_seq="ACGU")
        r = ev.evaluate_branch("b1", [], [], pair_f1=0.5)
        assert r.hard_valid is False

    def test_reject_mutated_base(self):
        """Even a single mutated base must trigger rejection."""
        ev = ObjectiveEvaluator(target_seq="ACGU")
        # Change A→U at position 0
        r = ev.evaluate_branch("b1", list("UCGU"), list(range(4)), pair_f1=0.99)
        assert r.hard_valid is False


class TestDeltaScoring:
    """Delta metrics must be computed correctly vs baseline."""

    def test_positive_delta(self):
        baseline = AgentEvaluation(branch_id="root", hard_valid=True, pair_f1=0.80, map_acc=0.75, gap_f1=0.30)
        ev = ObjectiveEvaluator(target_seq="ACGU", baseline_eval=baseline)
        r = ev.evaluate_branch("b1", list("ACGU"), list(range(4)), pair_f1=0.85, map_acc=0.80, gap_f1=0.40)
        assert r.delta_vs_baseline.pair_f1 > 0
        assert r.delta_vs_baseline.map_acc > 0
        assert r.delta_vs_baseline.gap_f1 > 0
        assert "Improved" in str(r.accept_reasons)

    def test_negative_delta(self):
        baseline = AgentEvaluation(branch_id="root", hard_valid=True, pair_f1=0.90)
        ev = ObjectiveEvaluator(target_seq="ACGU", baseline_eval=baseline)
        r = ev.evaluate_branch("b1", list("ACGU"), list(range(4)), pair_f1=0.70)
        assert r.delta_vs_baseline.pair_f1 < 0

    def test_no_baseline(self):
        """When no baseline exists, deltas should be zero."""
        ev = ObjectiveEvaluator(target_seq="ACGU")
        r = ev.evaluate_branch("b1", list("ACGU"), list(range(4)), pair_f1=0.75)
        assert r.delta_vs_baseline.pair_f1 == 0.0
        assert r.delta_vs_baseline.map_acc == 0.0


class TestSelectedAsBest:
    def test_auto_selected_when_no_prior_best(self):
        """When no prior best exists, the first valid branch should be auto-selected."""
        ev = ObjectiveEvaluator(target_seq="ACGU")
        r = ev.evaluate_branch("b1", list("ACGU"), list(range(4)), pair_f1=0.5)
        assert r.selected_as_best is True  # auto-selected as first valid

    def test_not_selected_when_invalid(self):
        """Invalid branches must never be selected as best."""
        ev = ObjectiveEvaluator(target_seq="ACGU")
        r = ev.evaluate_branch("b1", list("XXXX"), list(range(4)), pair_f1=0.99)
        assert r.selected_as_best is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
