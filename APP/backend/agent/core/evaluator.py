# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .state import AgentEvaluation, DeltaScore

logger = logging.getLogger(__name__)

class ObjectiveEvaluator:
    """
    Evaluator that validates a branch against hard constraints and computes
    deltas mapped to the `AgentEvaluation` contract.
    """

    def __init__(
        self,
        target_seq: str,
        baseline_eval: Optional[AgentEvaluation] = None,
        best_eval: Optional[AgentEvaluation] = None,
        gold_query_aln: str = "",
        gold_template_aln: str = "",
    ):
        self.target_seq = target_seq
        self.baseline_eval = baseline_eval
        self.best_eval = best_eval
        self.gold_query_aln = gold_query_aln
        self.gold_template_aln = gold_template_aln

    def evaluate_branch(
        self,
        branch_id: str,
        aligned_sequence: List[str],
        aligned_numbering: List[str],
        pair_f1: float = 0.0,
        map_acc: float = 0.0,
        gap_f1: float = 0.0,
        template_sequence: Optional[List[str]] = None,
    ) -> AgentEvaluation:
        # If gold standard is present, compute real metrics
        if self.gold_query_aln and self.gold_template_aln and template_sequence:
            from ..tools.eval_tools import score_prediction_against_gold
            pred_q, pred_t = self._build_alignment_pair(aligned_sequence, template_sequence)
            metrics = score_prediction_against_gold(
                query_seq=self.target_seq,
                template_seq="", # not used by score_prediction_against_gold
                gold_query_aln=self.gold_query_aln,
                gold_template_aln=self.gold_template_aln,
                pred_query_aln=pred_q,
                pred_template_aln=pred_t,
                pred_numbering=aligned_numbering,
            )
            pair_f1 = metrics.get("pair_f1", 0.0)
            map_acc = metrics.get("map_acc", 0.0)
            gap_f1 = metrics.get("gap_f1", 0.0)
        
        evaluation = AgentEvaluation(
            branch_id=branch_id,
            pair_f1=pair_f1,
            map_acc=map_acc,
            gap_f1=gap_f1,
            delta_vs_baseline=DeltaScore(),
            delta_vs_best=DeltaScore(),
            reject_reasons=[],
            accept_reasons=[]
        )
        
        # 1. Hard constraint evaluation
        is_hard_valid = self._check_base_preservation(aligned_sequence)
        if not is_hard_valid:
            evaluation.reject_reasons.append("Base preservation constraint violated.")
            evaluation.hard_valid = False
        else:
            evaluation.hard_valid = True
            
        # 2. Deltas vs Baseline
        if self.baseline_eval:
            evaluation.delta_vs_baseline = DeltaScore(
                pair_f1=round(pair_f1 - self.baseline_eval.pair_f1, 4),
                map_acc=round(map_acc - self.baseline_eval.map_acc, 4),
                gap_f1=round(gap_f1 - self.baseline_eval.gap_f1, 4)
            )
            if evaluation.delta_vs_baseline.pair_f1 > 0 and evaluation.hard_valid:
                evaluation.accept_reasons.append("Improved pair_f1 over baseline.")

        # 3. Deltas vs Current Best
        if self.best_eval:
            evaluation.delta_vs_best = DeltaScore(
                pair_f1=round(pair_f1 - self.best_eval.pair_f1, 4),
                map_acc=round(map_acc - self.best_eval.map_acc, 4),
                gap_f1=round(gap_f1 - self.best_eval.gap_f1, 4)
            )
            if evaluation.delta_vs_best.pair_f1 > 0 and evaluation.hard_valid:
                evaluation.accept_reasons.append("New overall best score achieved.")
                evaluation.selected_as_best = True

        # If no best eval exists yet and this is valid, it becomes best
        if not self.best_eval and evaluation.hard_valid:
            evaluation.selected_as_best = True

        return evaluation

    def _check_base_preservation(self, aligned_sequence: List[str]) -> bool:
        if not aligned_sequence:
            return False
            
        stripped = "".join([b for b in aligned_sequence if b != "-"]).upper().replace("T", "U")
        target = self.target_seq.upper().replace("T", "U")
        valid = stripped == target
        if not valid:
            logger.warning(f"Base preservation failed. Extracted: {stripped[:20]}... Length: {len(stripped)} vs Target: {target[:20]}... Length: {len(target)}")
        return valid

    def _build_alignment_pair(self, q_aln: List[str], t_aln: List[str]) -> Tuple[str, str]:
        return "".join(q_aln), "".join(t_aln)

__all__ = ["ObjectiveEvaluator"]
