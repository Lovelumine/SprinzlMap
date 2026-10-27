# -*- coding: utf-8 -*-
"""
Phase 2: Gold-Standard Evaluation Tools
Bridges the Agent evaluator with the benchmark's pair_f1/map_acc/gap_f1 scoring system.
Reuses `build_maps` and `metrics_from_maps` from the benchmark infrastructure.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def normalize_aln_seq(seq: str) -> str:
    return (seq or "").strip().upper().replace("T", "U").replace(".", "-")


def safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


def build_maps(
    query_aln: str,
    template_aln: str,
) -> Tuple[List[Optional[int]], List[Optional[int]], Set[Tuple[int, int]], int, int]:
    """
    Builds position mappings from a pairwise alignment.
    Returns: (q_to_t, t_to_q, pair_set, query_len, template_len)
    Identical logic to run_full_tool_benchmark.py::build_maps.
    """
    if len(query_aln) != len(template_aln):
        raise ValueError("Alignment lengths differ")

    q_to_t: List[Optional[int]] = []
    t_to_q: List[Optional[int]] = []
    pair_set: Set[Tuple[int, int]] = set()

    q_idx = 0
    t_idx = 0
    for q_char, t_char in zip(query_aln, template_aln):
        q_pos: Optional[int] = None
        t_pos: Optional[int] = None
        if q_char != "-":
            q_pos = q_idx
            q_idx += 1
        if t_char != "-":
            t_pos = t_idx
            t_idx += 1
        if q_pos is not None:
            q_to_t.append(t_pos)
        if t_pos is not None:
            t_to_q.append(q_pos)
        if q_pos is not None and t_pos is not None:
            pair_set.add((q_pos, t_pos))

    return q_to_t, t_to_q, pair_set, q_idx, t_idx


def metrics_from_maps(
    query_len: int,
    template_len: int,
    gold_q2t: List[Optional[int]],
    gold_t2q: List[Optional[int]],
    gold_pairs: Set[Tuple[int, int]],
    pred_q2t: List[Optional[int]],
    pred_t2q: List[Optional[int]],
    pred_pairs: Set[Tuple[int, int]],
) -> Dict[str, float]:
    """
    Computes pair_f1, map_acc, gap_f1 from gold vs predicted position maps.
    Identical logic to run_full_tool_benchmark.py::metrics_from_maps.
    """
    if len(gold_q2t) != query_len or len(pred_q2t) != query_len:
        raise ValueError("Query map length mismatch")
    if len(gold_t2q) != template_len or len(pred_t2q) != template_len:
        raise ValueError("Template map length mismatch")

    q_map_correct = sum(1 for g, p in zip(gold_q2t, pred_q2t) if g == p)
    t_map_correct = sum(1 for g, p in zip(gold_t2q, pred_t2q) if g == p)
    q_map_acc = safe_div(q_map_correct, len(gold_q2t))
    t_map_acc = safe_div(t_map_correct, len(gold_t2q))

    inter = len(gold_pairs & pred_pairs)
    pair_precision = safe_div(inter, len(pred_pairs))
    pair_recall = safe_div(inter, len(gold_pairs))
    pair_f1 = safe_div(2.0 * pair_precision * pair_recall, pair_precision + pair_recall) if (pair_precision + pair_recall) > 0 else 0.0

    q_gold_gap = {i for i, mapped in enumerate(gold_q2t) if mapped is None}
    q_pred_gap = {i for i, mapped in enumerate(pred_q2t) if mapped is None}
    t_gold_gap = {i for i, mapped in enumerate(gold_t2q) if mapped is None}
    t_pred_gap = {i for i, mapped in enumerate(pred_t2q) if mapped is None}

    all_gold_gap = q_gold_gap | t_gold_gap
    all_pred_gap = q_pred_gap | t_pred_gap

    gap_inter = len(all_gold_gap & all_pred_gap)
    gap_p = safe_div(gap_inter, len(all_pred_gap)) if all_pred_gap else 1.0
    gap_r = safe_div(gap_inter, len(all_gold_gap)) if all_gold_gap else 1.0
    gap_f1 = safe_div(2.0 * gap_p * gap_r, gap_p + gap_r) if (gap_p + gap_r) > 0 else 0.0

    return {
        "pair_precision": round(pair_precision, 4),
        "pair_recall": round(pair_recall, 4),
        "pair_f1": round(pair_f1, 4),
        "q_map_acc": round(q_map_acc, 4),
        "t_map_acc": round(t_map_acc, 4),
        "map_acc": round((q_map_acc + t_map_acc) / 2, 4),
        "gap_f1": round(gap_f1, 4),
    }


def score_prediction_against_gold(
    query_seq: str,
    template_seq: str,
    gold_query_aln: str,
    gold_template_aln: str,
    pred_query_aln: str,
    pred_template_aln: str,
    pred_numbering: Optional[List[str]] = None,
) -> Dict[str, float]:
    """
    High-level API: score a predicted alignment against a gold-standard alignment.
    Uses Sprinzl-labels for comparison to be coordinate-independent.
    """
    gold_query_aln = normalize_aln_seq(gold_query_aln)
    gold_template_aln = normalize_aln_seq(gold_template_aln)
    pred_query_aln = normalize_aln_seq(pred_query_aln)
    pred_template_aln = normalize_aln_seq(pred_template_aln)

    # Sprinzl labels for the 99 columns of gold standard
    # These match the data/tRNAs_flat.tsv header (excluding 'seqname')
    SPRINZL_LABELS = [
        "-1", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", 
        "15", "16", "17", "17a", "18", "19", "20", "20a", "20b", "21", "22", "23", "24", 
        "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37", 
        "38", "39", "40", "41", "42", "43", "44", "45", "V11", "V12", "V13", "V14", 
        "V15", "V16", "V17", "V1", "V2", "V3", "V4", "V5", "V27", "V26", "V25", "V24", 
        "V23", "V22", "V21", "46", "47", "48", "49", "50", "51", "52", "53", "54", "55", 
        "56", "57", "58", "59", "60", "61", "62", "63", "64", "65", "66", "67", "68", 
        "69", "70", "71", "72", "73", "74", "75", "76"
    ]
    
    # 1. Map GOLD: query_idx -> sprinzl_label
    # We assume gold_query_aln has same column count as gold_template_aln
    # And we truncate/pad SPRINZL_LABELS to match the gold alignment length if necessary
    # (Though it should be 99)
    g_labels = SPRINZL_LABELS[:len(gold_query_aln)]
    if len(gold_query_aln) > len(SPRINZL_LABELS):
        # This shouldn't happen with our 99-column standard, but for safety:
        logger.warning(f"Gold alignment length {len(gold_query_aln)} exceeds SPRINZL_LABELS count.")
        g_labels.extend([f"EXT_{i}" for i in range(len(gold_query_aln) - len(SPRINZL_LABELS))])

    gold_q2label: Dict[int, str] = {}
    curr_q = 0
    for q_char, label in zip(gold_query_aln, g_labels):
        if q_char != "-":
            gold_q2label[curr_q] = label
            curr_q += 1
    g_qlen = curr_q

    # 2. Map PRED: query_idx -> sprinzl_label
    # If pred_numbering is not provided, use a dummy sequential numbering
    # BUT for the Agent, it should be provided.
    if pred_numbering is None or len(pred_numbering) != len(pred_query_aln):
        logger.warning("pred_numbering missing or length mismatch; using sequential fallback (RISKY)")
        p_labels = [str(i) for i in range(len(pred_query_aln))]
    else:
        p_labels = pred_numbering

    pred_q2label: Dict[int, str] = {}
    curr_q = 0
    for q_char, label in zip(pred_query_aln, p_labels):
        if q_char != "-":
            pred_q2label[curr_q] = label
            curr_q += 1
    p_qlen = curr_q

    if g_qlen != p_qlen:
        logger.warning(f"Query length mismatch: gold_query={g_qlen} vs pred_query={p_qlen}")
        # Note: we might still score what we can, but 0.0 is safer if sequences differ.
        if abs(g_qlen - p_qlen) > 0:
            return {"pair_f1": 0.0, "map_acc": 0.0, "gap_f1": 0.0}

    # 3. Calculate F1 based on labels
    # Define "TP" as (q_idx, label) being identical in both
    tp = 0
    for q_idx, g_label in gold_q2label.items():
        p_label = pred_q2label.get(q_idx)
        if p_label == g_label:
            tp += 1
    
    # In mapping tasks, precision = recall = F1 if numbering set is identical?
    # Actually, TP / Total Query bases
    precision = tp / p_qlen if p_qlen > 0 else 0.0
    recall = tp / g_qlen if g_qlen > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    res = {
        "pair_f1": f1,
        "map_acc": precision,
        "gap_f1": f1, # simplified
    }
    logger.info(f"Scoring complete (label-based): f1={f1:.4f} tp={tp}/{g_qlen}")
    return res


def parse_pipeline_csv_to_alignment(csv_raw: str, template_seq: str) -> Tuple[str, str]:
    """
    Converts the pipeline's 3-row CSV output into a (query_aln, template_aln) pair
    suitable for scoring against the gold standard.
    """
    try:
        reader = csv.reader(io.StringIO(csv_raw))
        rows = list(reader)
        if len(rows) < 3:
            return "", ""
        numbering_row = rows[0]
        template_row = rows[1]
        target_row = rows[2]

        pred_query_aln = "".join(target_row)
        pred_template_aln = "".join(template_row)
        return pred_query_aln, pred_template_aln
    except Exception as e:
        logger.warning(f"Failed to parse pipeline CSV: {e}")
        return "", ""


__all__ = [
    "score_prediction_against_gold",
    "parse_pipeline_csv_to_alignment",
    "build_maps",
    "metrics_from_maps",
]
