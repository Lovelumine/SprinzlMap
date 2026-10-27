# -*- coding: utf-8 -*-
"""
Joint postcheck for aminoacyl arms:
- Aminoacyl arm 5' end (AA5)
- Aminoacyl arm 3' end (AA3)

Key idea:
- Evaluate and optimize these two blocks together using acceptor-stem pairing
  constraints (1-72, 2-71, ..., 7-66).
- Keep numbering/template fixed.
- Only rewrite target rows when objective strictly improves.
"""

from __future__ import annotations

import json
import time
from math import comb
from typing import Any, Dict, List, Optional, Tuple

from backend.infra.llm.provider_config import (
    apply_openai_provider_extras,
    openai_chat_completions_url,
)

from .variable_loop import (
    _http_post_json,
    _load_runtime_cfg,
    _log_runtime_cfg,
    _log_trunc,
)
from ..runtime_helpers import deepseek_fallback_model, runtime_prompt_value
from .common import (
    apply_joint_postcheck_result,
    build_joint_trace_preview,
    finalize_joint_trace_preview,
    invalid_trace_preview,
    parse_keep_current_status,
    provider_fallback_model,
)

AA5_REGION = "Aminoacyl arm 5' end"
AA3_REGION = "Aminoacyl arm 3' end"

_VALID_BASES = {"A", "U", "G", "C", "N", "-"}
_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_ACCEPTOR_PAIR_LABELS: List[Tuple[str, str]] = [
    ("1", "72"),
    ("2", "71"),
    ("3", "70"),
    ("4", "69"),
    ("5", "68"),
    ("6", "67"),
    ("7", "66"),
]
_AA5_STEM_LABELS = {str(i) for i in range(1, 8)}
_AA3_STEM_LABELS = {str(i) for i in range(66, 73)}
_STEM_BASE_LABELS = _AA5_STEM_LABELS | _AA3_STEM_LABELS

_TEMPLATE_RELIABILITY_LOW_OVERLAP = 4
_TEMPLATE_RELIABILITY_LOW_IDENTITY = 0.45
_TEMPLATE_RELIABILITY_MED_IDENTITY = 0.70
_AA_EXACT_BLOCK_MAX_STATES = 2500
_AA_EXACT_JOINT_MAX_EVALS = 300000

DEFAULT_AA_JOINT_SYSTEM_PROMPT = (
    "You are a strict RNA alignment post-editor for aminoacyl arms.\n"
    "Task: jointly rewrite TARGET tokens for two blocks only:\n"
    "- Aminoacyl arm 5' end\n"
    "- Aminoacyl arm 3' end\n"
    "Hard rules:\n"
    "1) Never change numbering/template columns.\n"
    "2) Return exactly same length for each block target token list.\n"
    "3) Preserve exact left-to-right order of all non-gap bases within each block.\n"
    "4) Canonical acceptor-stem target pairs are: "
    "1-72, 2-71, 3-70, 4-69, 5-68, 6-67, 7-66.\n"
    "5) Conditional hard rule: if a full 7-pair contiguous acceptor chain is achievable, "
    "it must be realized on these canonical labels (not shifted to insertion labels).\n"
    "6) Minimize unmatched acceptor-stem pairs (gap pairs + non-canonical pairs).\n"
    "7) Positions 74/75/76 are reserved for one complete CCA tail or are all unoccupied; partial or non-CCA terminal residues must not be placed there or in 74i*/75i*/76i*. A 73i* label may represent an unresolved terminal extension.\n"
    "8) If position -1 has a base, position 1 must not be '-'.\n"
    "9) Soft caution: AA5 pattern '-1 occupied + gaps in 1..9' may indicate left-shift artifact; weigh trade-offs before keeping it.\n"
    "10) Template bases are soft prior only; if template reliability is low, prioritize structure.\n"
    "11) Keep numbering clean: avoid insertion labels unless structural evidence strongly requires them.\n"
    "12) If adding insertion occupancy would increase canonical stem gaps or fail to reduce unmatched pairs, reject it.\n"
    "13) Coexisting stem insertion occupancy and canonical stem gaps is high-risk; keep insertion count near zero when gaps remain.\n"
    "14) If template reliability is low, do not introduce new insertion occupancy unless unmatched pairs strictly decrease.\n"
    "15) Use only A/U/G/C/N or '-'.\n"
    "16) If the current blocks already satisfy hard constraints and you cannot STRICTLY improve the objective, "
    "return a no-change decision instead of fabricating edits.\n"
    "Return ONLY JSON in one of these forms:\n"
    "{\"aa5_target_tokens\": [...], \"aa3_target_tokens\": [...]}.\n"
    "or {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}."
)

def _load_cfg(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _load_runtime_cfg(runtime_values)
    cfg["aa5_system_prompt"] = runtime_prompt_value("LLM_AA5_SYSTEM_PROMPT", runtime_values)
    cfg["aa3_system_prompt"] = runtime_prompt_value("LLM_AA3_SYSTEM_PROMPT", runtime_values)
    return cfg


def _effective_system_prompt(cfg: Dict[str, Any]) -> str:
    p5 = str(cfg.get("aa5_system_prompt") or "").strip()
    p3 = str(cfg.get("aa3_system_prompt") or "").strip()
    if p5 and p3:
        if p5 == p3:
            return p5
        return f"{p5}\n\n---\n\n{p3}"
    if p5:
        return p5
    if p3:
        return p3
    return DEFAULT_AA_JOINT_SYSTEM_PROMPT


def _norm_base(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    if s in _VALID_BASES:
        return s
    return "-"


def _non_gap_tokens(tokens: List[str]) -> List[str]:
    return [t for t in (_norm_base(x) for x in tokens) if t != "-"]


def _order_preserved(before: List[str], after: List[str]) -> bool:
    return _non_gap_tokens(before) == _non_gap_tokens(after)


def _build_idx(nums: List[str]) -> Dict[str, int]:
    idx: Dict[str, int] = {}
    for i, n in enumerate(nums):
        key = str(n or "")
        if key and key not in idx:
            idx[key] = i
    return idx


def _build_struct_tokens_for_columns(targ: List[str], region_struct: str) -> List[str]:
    struct = [ch for ch in (region_struct or "") if ch in (">", "<", ".")]
    out: List[str] = []
    p = 0
    for base in targ:
        b = _norm_base(base)
        if b == "-":
            out.append("GAP")
            continue
        if p < len(struct):
            out.append(struct[p])
            p += 1
        else:
            out.append("UNK")
    return out


def _template_similarity_stats(templ: List[str], targ: List[str]) -> Dict[str, Any]:
    t = [_norm_base(x) for x in templ]
    q = [_norm_base(x) for x in targ]
    n = min(len(t), len(q))
    t = t[:n]
    q = q[:n]

    template_non_gap = sum(1 for x in t if x != "-")
    target_non_gap = sum(1 for x in q if x != "-")
    overlap_non_gap = 0
    overlap_match = 0
    for tb, qb in zip(t, q):
        if tb == "-" or qb == "-":
            continue
        overlap_non_gap += 1
        if tb == qb:
            overlap_match += 1

    overlap_identity = (overlap_match / overlap_non_gap) if overlap_non_gap else 0.0
    template_overlap_coverage = (overlap_non_gap / template_non_gap) if template_non_gap else 0.0
    target_overlap_coverage = (overlap_non_gap / target_non_gap) if target_non_gap else 0.0

    if overlap_non_gap < _TEMPLATE_RELIABILITY_LOW_OVERLAP or overlap_identity < _TEMPLATE_RELIABILITY_LOW_IDENTITY:
        reliability = "low"
        template_weight = 0.05
        policy = "ignore_template_mismatch_and_prioritize_structure"
    elif overlap_identity < _TEMPLATE_RELIABILITY_MED_IDENTITY:
        reliability = "medium"
        template_weight = 0.20
        policy = "use_template_as_tie_breaker_only"
    else:
        reliability = "high"
        template_weight = 0.35
        policy = "template_can_assist_after_structure_constraints"

    return {
        "template_non_gap": int(template_non_gap),
        "target_non_gap": int(target_non_gap),
        "overlap_non_gap": int(overlap_non_gap),
        "overlap_match": int(overlap_match),
        "overlap_identity": round(float(overlap_identity), 4),
        "template_overlap_coverage": round(float(template_overlap_coverage), 4),
        "target_overlap_coverage": round(float(target_overlap_coverage), 4),
        "reliability": reliability,
        "template_weight": template_weight,
        "policy": policy,
    }


def _is_insertion_label(label: str) -> bool:
    return "i" in str(label or "")


def _pair_stats(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Dict[str, int]:
    idx5 = _build_idx(nums5)
    idx3 = _build_idx(nums3)
    total = 0
    canonical = 0
    gap_pairs = 0
    noncanonical = 0
    pair_states: List[str] = []
    for left, right in _ACCEPTOR_PAIR_LABELS:
        if left not in idx5 or right not in idx3:
            pair_states.append("missing")
            continue
        total += 1
        a = _norm_base(targ5[idx5[left]])
        b = _norm_base(targ3[idx3[right]])
        if a == "-" or b == "-":
            gap_pairs += 1
            pair_states.append("gap")
            continue
        if a + b in _CANONICAL_PAIRS:
            canonical += 1
            pair_states.append("canonical")
        else:
            noncanonical += 1
            pair_states.append("noncanonical")

    run_max = 0
    run = 0
    for st in pair_states:
        if st == "canonical":
            run += 1
            if run > run_max:
                run_max = run
        else:
            run = 0
    all7_contiguous_canonical = (
        len(pair_states) == len(_ACCEPTOR_PAIR_LABELS)
        and all(st == "canonical" for st in pair_states)
    )

    return {
        "total_pairs": total,
        "canonical_pairs": canonical,
        "gap_pairs": gap_pairs,
        "noncanonical_pairs": noncanonical,
        "unmatched_pairs": gap_pairs + noncanonical,
        "canonical_run_max": run_max,
        "all7_contiguous_canonical": int(all7_contiguous_canonical),
    }


def _pair_state_detail(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> List[Dict[str, str]]:
    idx5 = _build_idx(nums5)
    idx3 = _build_idx(nums3)
    detail: List[Dict[str, str]] = []
    for left, right in _ACCEPTOR_PAIR_LABELS:
        if left not in idx5 or right not in idx3:
            detail.append({"pair": f"{left}-{right}", "state": "missing", "bases": "-/-"})
            continue
        a = _norm_base(targ5[idx5[left]])
        b = _norm_base(targ3[idx3[right]])
        if a == "-" or b == "-":
            st = "gap"
        elif a + b in _CANONICAL_PAIRS:
            st = "canonical"
        else:
            st = "noncanonical"
        detail.append({"pair": f"{left}-{right}", "state": st, "bases": f"{a}/{b}"})
    return detail


def _parent_indices(nums: List[str], parent: str) -> List[int]:
    p = str(parent or "")
    out: List[int] = []
    for i, n in enumerate(nums):
        sid = str(n or "")
        if sid == p or sid.startswith(p + "i"):
            out.append(i)
    return out


def _full_acceptor_chain_possible(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Tuple[bool, Dict[str, int]]:
    """
    Check whether a 7-step contiguous acceptor chain is achievable in the
    acceptor neighborhood when allowing insertion labels under each canonical
    parent (e.g., 7i1 with 66i1).
    """
    step_ok: Dict[str, int] = {}
    all_ok = True
    for left, right in _ACCEPTOR_PAIR_LABELS:
        li = _parent_indices(nums5, left)
        ri = _parent_indices(nums3, right)
        ok = False
        for i in li:
            a = _norm_base(targ5[i])
            if a == "-":
                continue
            for j in ri:
                b = _norm_base(targ3[j])
                if b == "-":
                    continue
                if a + b in _CANONICAL_PAIRS:
                    ok = True
                    break
            if ok:
                break
        step_ok[f"{left}-{right}"] = int(ok)
        if not ok:
            all_ok = False
    return all_ok, step_ok


def _hard_constraint_stats(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Dict[str, Any]:
    idx5 = _build_idx(nums5)
    idx3 = _build_idx(nums3)
    reasons: List[str] = []
    violations = 0

    tail_labels = [label for label in ("74", "75", "76") if label in idx3]
    occupied_tail_labels = [
        label for label in tail_labels if _norm_base(targ3[idx3[label]]) != "-"
    ]
    cca_present = all(k in idx3 for k in ("74", "75", "76"))
    cca_motif = ""
    if occupied_tail_labels and not cca_present:
        violations += 1
        reasons.append(
            "cca_coordinates_incomplete:" + ",".join(occupied_tail_labels)
        )
    if cca_present:
        i74 = idx3["74"]
        i75 = idx3["75"]
        i76 = idx3["76"]
        b74 = _norm_base(targ3[i74])
        b75 = _norm_base(targ3[i75])
        b76 = _norm_base(targ3[i76])
        cca_motif = f"{b74}{b75}{b76}"
        if not (i74 + 1 == i75 and i75 + 1 == i76):
            violations += 1
            reasons.append(f"cca_not_contiguous:{i74},{i75},{i76}")
        if cca_motif not in {"CCA", "---"}:
            violations += 1
            reasons.append(f"cca_motif_invalid:{cca_motif}")

    terminal_insertions = [
        str(label)
        for label, base in zip(nums3, targ3)
        if _norm_base(base) != "-"
        and any(
            str(label).startswith(f"{parent}i")
            for parent in ("74", "75", "76")
        )
    ]
    if terminal_insertions:
        violations += 1
        reasons.append(
            "terminal_insertion_not_allowed:"
            + ",".join(terminal_insertions)
        )

    minus1_present = ("-1" in idx5 and "1" in idx5)
    minus1_base = ""
    pos1_base = ""
    aa5_minus1_core_gap_pattern = False
    aa5_minus1_core_gap_labels: List[str] = []
    if minus1_present:
        minus1_base = _norm_base(targ5[idx5["-1"]])
        pos1_base = _norm_base(targ5[idx5["1"]])
        if minus1_base != "-" and pos1_base == "-":
            violations += 1
            reasons.append(f"minus1_without_pos1:{minus1_base}/-")
        # Soft pattern flag only (no hard rejection):
        # if -1 is occupied while canonical AA5 body 1..9 has gaps,
        # this may be a left-shift artifact and should trigger an LLM reconsideration once.
        if minus1_base != "-":
            core_labels = [str(i) for i in range(1, 10)]
            aa5_minus1_core_gap_labels = [
                lab for lab in core_labels
                if (lab in idx5 and _norm_base(targ5[idx5[lab]]) == "-")
            ]
            aa5_minus1_core_gap_pattern = bool(aa5_minus1_core_gap_labels)

    pair_stats = _pair_stats(nums5, targ5, nums3, targ3)
    full_chain_possible, chain_detail = _full_acceptor_chain_possible(nums5, targ5, nums3, targ3)
    if full_chain_possible and not bool(pair_stats.get("all7_contiguous_canonical", 0)):
        violations += 1
        reasons.append(
            "acceptor_chain_shifted_from_canonical_labels"
        )
    stem_insertions = _stem_insertion_count(nums5, targ5) + _stem_insertion_count(nums3, targ3)
    canonical_stem_gaps = _canonical_stem_gap_count(nums5, targ5, nums3, targ3)
    if stem_insertions > 0 and canonical_stem_gaps > 0 and int(pair_stats.get("unmatched_pairs", 0)) > 0:
        violations += 1
        reasons.append("stem_insertion_with_remaining_canonical_gaps")

    return {
        "violations": violations,
        "reasons": reasons,
        "cca_present": bool(cca_present),
        "cca_motif": cca_motif,
        "minus1_present": bool(minus1_present),
        "minus1_base": minus1_base,
        "pos1_base": pos1_base,
        "aa5_minus1_core_gap_pattern": bool(aa5_minus1_core_gap_pattern),
        "aa5_minus1_core_gap_labels": aa5_minus1_core_gap_labels,
        "acceptor_canonical_run_max": int(pair_stats.get("canonical_run_max", 0)),
        "acceptor_all7_contiguous_canonical": bool(pair_stats.get("all7_contiguous_canonical", 0)),
        "acceptor_full_chain_possible": bool(full_chain_possible),
        "acceptor_chain_step_ok": chain_detail,
        "stem_insertions": int(stem_insertions),
        "canonical_stem_gaps": int(canonical_stem_gaps),
    }


def _stem_insertion_count(nums: List[str], targ: List[str]) -> int:
    cnt = 0
    for n, b in zip(nums, targ):
        sid = str(n or "")
        if "i" not in sid:
            continue
        parent = sid.split("i", 1)[0]
        if parent in _STEM_BASE_LABELS and _norm_base(b) != "-":
            cnt += 1
    return cnt


def _canonical_stem_gap_count(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> int:
    idx5 = _build_idx(nums5)
    idx3 = _build_idx(nums3)
    gaps = 0
    for sid in _AA5_STEM_LABELS:
        pos = idx5.get(sid)
        if pos is None:
            continue
        if _norm_base(targ5[pos]) == "-":
            gaps += 1
    for sid in _AA3_STEM_LABELS:
        pos = idx3.get(sid)
        if pos is None:
            continue
        if _norm_base(targ3[pos]) == "-":
            gaps += 1
    return gaps


def _cleanliness_metrics(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Dict[str, int]:
    pairs = _pair_stats(nums5, targ5, nums3, targ3)
    stem_insertions = _stem_insertion_count(nums5, targ5) + _stem_insertion_count(nums3, targ3)
    canonical_stem_gaps = _canonical_stem_gap_count(nums5, targ5, nums3, targ3)
    coexist_penalty = stem_insertions if canonical_stem_gaps > 0 else 0
    return {
        "unmatched_pairs": int(pairs.get("unmatched_pairs", 0)),
        "stem_insertions": int(stem_insertions),
        "canonical_stem_gaps": int(canonical_stem_gaps),
        "insertion_gap_coexist_penalty": int(coexist_penalty),
    }


def _all_insertion_occupancy_count(nums: List[str], targ: List[str]) -> int:
    cnt = 0
    for n, b in zip(nums, targ):
        sid = str(n or "")
        if "i" in sid and _norm_base(b) != "-":
            cnt += 1
    return cnt


def _insertion_regression_guard(
    baseline: Dict[str, int],
    candidate: Dict[str, int],
    template_reliability_low: bool = False,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    cand_ins = int(candidate.get("stem_insertions", 0))
    base_ins = int(baseline.get("stem_insertions", 0))
    cand_unmatched = int(candidate.get("unmatched_pairs", 0))
    base_unmatched = int(baseline.get("unmatched_pairs", 0))
    cand_gaps = int(candidate.get("canonical_stem_gaps", 0))
    base_gaps = int(baseline.get("canonical_stem_gaps", 0))
    cand_coexist = int(candidate.get("insertion_gap_coexist_penalty", 0))
    base_coexist = int(baseline.get("insertion_gap_coexist_penalty", 0))

    if cand_ins > base_ins and cand_unmatched >= base_unmatched and cand_gaps > base_gaps:
        reasons.append("insertion_increases_canonical_gaps_without_unmatched_gain")
    if cand_ins > base_ins and cand_unmatched > base_unmatched:
        reasons.append("insertion_causes_unmatched_regression")
    if cand_coexist > base_coexist and cand_unmatched >= base_unmatched:
        reasons.append("insertion_gap_coexistence_increases_without_unmatched_gain")
    if cand_ins > 0 and cand_gaps > 0 and cand_unmatched >= 0:
        reasons.append("stem_insertion_with_remaining_canonical_gaps")
    if template_reliability_low and cand_ins > base_ins and cand_unmatched >= base_unmatched:
        reasons.append("low_template_reliability_disallow_new_insertions_without_unmatched_gain")

    return (len(reasons) > 0), reasons


def _shifted_bad_alignment_guard(
    baseline_pairs: Dict[str, int],
    candidate_pairs: Dict[str, int],
    candidate_clean: Dict[str, int],
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    total_pairs = int(candidate_pairs.get("total_pairs", 0))
    cand_unmatched = int(candidate_pairs.get("unmatched_pairs", 0))
    cand_canonical = int(candidate_pairs.get("canonical_pairs", 0))
    cand_run = int(candidate_pairs.get("canonical_run_max", 0))
    base_unmatched = int(baseline_pairs.get("unmatched_pairs", 0))

    # Strong signal for "global shifted but still bad":
    # almost no canonical chain and most canonical pairs are still broken.
    if total_pairs >= 6 and cand_unmatched >= max(5, total_pairs - 2) and cand_run <= 1:
        reasons.append("acceptor_chain_mostly_broken_after_shift_like_candidate")
    if cand_canonical <= 2 and cand_run <= 1 and cand_unmatched >= 5:
        reasons.append("no_stable_canonical_chain")
    if cand_unmatched >= base_unmatched and cand_run <= 1:
        reasons.append("no_pairing_progress_vs_baseline")

    cand_ins = int(candidate_clean.get("stem_insertions", 0))
    cand_gaps = int(candidate_clean.get("canonical_stem_gaps", 0))
    if cand_ins > 0 and cand_gaps > 0:
        reasons.append("stem_insertions_coexist_with_canonical_gaps")

    return (len(reasons) > 0), reasons


def _score_acceptor_joint(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Tuple[int, List[str]]:
    idx5 = _build_idx(nums5)
    idx3 = _build_idx(nums3)
    score = 0
    reasons: List[str] = []

    hard = _hard_constraint_stats(nums5, targ5, nums3, targ3)
    if int(hard.get("violations") or 0) > 0:
        score += 100 * int(hard["violations"])
        reasons.extend([f"hard:{r}" for r in hard.get("reasons", [])])

    for left, right in _ACCEPTOR_PAIR_LABELS:
        if left not in idx5 or right not in idx3:
            continue
        a = _norm_base(targ5[idx5[left]])
        b = _norm_base(targ3[idx3[right]])
        if a == "-" or b == "-":
            score += 4
            reasons.append(f"gap_pair:{left}-{right}={a}/{b}")
            continue
        if a + b not in _CANONICAL_PAIRS:
            score += 2
            reasons.append(f"noncanonical:{left}-{right}={a}{b}")

    for label, base in zip(nums5, targ5):
        sid = str(label or "")
        if "i" not in sid:
            continue
        parent = sid.split("i", 1)[0]
        if parent in _AA5_STEM_LABELS and _norm_base(base) != "-":
            score += 2
            reasons.append(f"stem_insertion:{sid}={_norm_base(base)}")

    for label, base in zip(nums3, targ3):
        sid = str(label or "")
        if "i" not in sid:
            continue
        parent = sid.split("i", 1)[0]
        if parent in _AA3_STEM_LABELS and _norm_base(base) != "-":
            score += 2
            reasons.append(f"stem_insertion:{sid}={_norm_base(base)}")

    return score, reasons


def _objective_tuple(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
) -> Tuple[int, int, int, int, int, int]:
    pairs = _pair_stats(nums5, targ5, nums3, targ3)
    hard = _hard_constraint_stats(nums5, targ5, nums3, targ3)
    score, _ = _score_acceptor_joint(nums5, targ5, nums3, targ3)
    ins_pen = _stem_insertion_count(nums5, targ5) + _stem_insertion_count(nums3, targ3)
    gap_pen = _canonical_stem_gap_count(nums5, targ5, nums3, targ3)
    coexist_pen = ins_pen if gap_pen > 0 else 0
    all_ins = _all_insertion_occupancy_count(nums5, targ5) + _all_insertion_occupancy_count(nums3, targ3)
    return (hard["violations"], pairs["unmatched_pairs"], coexist_pen, ins_pen, all_ins, score)


def _materialize_target_from_mask(mask: Tuple[bool, ...], targ_before: List[str]) -> Optional[List[str]]:
    seq = _non_gap_tokens(targ_before)
    if sum(1 for x in mask if x) != len(seq):
        return None
    out = ["-"] * len(mask)
    p = 0
    for i, use in enumerate(mask):
        if not use:
            continue
        if p >= len(seq):
            return None
        out[i] = seq[p]
        p += 1
    if p != len(seq):
        return None
    return out


def _search_block_candidate(
    nums: List[str],
    targ_before: List[str],
    important_labels: set[str],
    objective_func,
    max_steps: int = 4,
    beam_width: int = 24,
) -> Optional[List[str]]:
    base_targ = [_norm_base(x) for x in targ_before]
    base_obj = objective_func(base_targ)
    n = len(nums)
    if n == 0:
        return None

    mask0 = tuple(b != "-" for b in base_targ)
    best_mask = mask0
    best_targ = base_targ
    best_obj = base_obj
    visited = {mask0}
    beam: List[Tuple[Tuple[bool, ...], Tuple[int, ...], List[str]]] = [(mask0, base_obj, base_targ)]

    for _ in range(max_steps):
        cand_pool: List[Tuple[Tuple[int, ...], Tuple[bool, ...], List[str]]] = []
        for mask, _, _ in beam:
            donors_ins = [i for i in range(n) if mask[i] and _is_insertion_label(str(nums[i] or ""))]
            acceptors_can = [j for j in range(n) if (not mask[j]) and (not _is_insertion_label(str(nums[j] or "")))]
            moves: set[Tuple[int, int]] = set()

            for d in donors_ins:
                for a in acceptors_can:
                    if a > d:
                        moves.add((d, a))
            if not moves:
                for d in donors_ins:
                    for a in acceptors_can:
                        if a != d:
                            moves.add((d, a))

            important_gaps = [
                j for j in range(n)
                if (not mask[j]) and (str(nums[j] or "") in important_labels)
            ]
            donor_soft = [
                i for i in range(n)
                if mask[i]
                and (str(nums[i] or "") not in important_labels)
                and (not _is_insertion_label(str(nums[i] or "")))
            ]
            for d in donor_soft:
                for a in important_gaps:
                    if a != d:
                        moves.add((d, a))

            for d, a in moves:
                nm = list(mask)
                nm[d] = False
                nm[a] = True
                nmt = tuple(nm)
                if nmt in visited:
                    continue
                visited.add(nmt)
                cand_targ = _materialize_target_from_mask(nmt, base_targ)
                if cand_targ is None:
                    continue
                cand_obj = objective_func(cand_targ)
                cand_pool.append((cand_obj, nmt, cand_targ))

        if not cand_pool:
            break

        cand_pool.sort(key=lambda x: x[0])
        beam = [(m, obj, t) for obj, m, t in cand_pool[:beam_width]]
        top_obj, top_m, top_t = cand_pool[0]
        if top_obj < best_obj:
            best_obj = top_obj
            best_mask = top_m
            best_targ = top_t

    if best_obj < base_obj and best_mask != mask0:
        return best_targ
    return None


def _deterministic_joint_candidate(
    nums5: List[str],
    targ5: List[str],
    nums3: List[str],
    targ3: List[str],
    max_rounds: int = 2,
) -> Optional[Tuple[List[str], List[str]]]:
    def _enumerate_block_candidates(
        targ_before: List[str],
        max_states: int,
    ) -> Optional[List[List[str]]]:
        base_targ = [_norm_base(x) for x in targ_before]
        n = len(base_targ)
        k = sum(1 for x in base_targ if x != "-")
        if k < 0 or k > n:
            return None
        try:
            total_states = comb(n, k)
        except Exception:
            return None
        if total_states > int(max_states):
            return None
        out: List[List[str]] = []
        if k == 0:
            return [["-"] * n]
        from itertools import combinations

        for idxs in combinations(range(n), k):
            mask = [False] * n
            for i in idxs:
                mask[i] = True
            cand = _materialize_target_from_mask(tuple(mask), base_targ)
            if cand is not None:
                out.append(cand)
        return out

    # Exact global search first (small AA state-space), then fallback to local search.
    cand5_all = _enumerate_block_candidates(targ5, _AA_EXACT_BLOCK_MAX_STATES)
    cand3_all = _enumerate_block_candidates(targ3, _AA_EXACT_BLOCK_MAX_STATES)
    if cand5_all is not None and cand3_all is not None:
        max_evals = int(_AA_EXACT_JOINT_MAX_EVALS)
        if len(cand5_all) * len(cand3_all) <= max_evals:
            base5 = [_norm_base(x) for x in targ5]
            base3 = [_norm_base(x) for x in targ3]
            base_obj = _objective_tuple(nums5, base5, nums3, base3)
            best_obj = base_obj
            best_pair: Optional[Tuple[List[str], List[str]]] = None
            for c5 in cand5_all:
                for c3 in cand3_all:
                    obj = _objective_tuple(nums5, c5, nums3, c3)
                    if obj < best_obj:
                        best_obj = obj
                        best_pair = (c5, c3)
                        if obj == (0, 0, 0, 0, 0, 0):
                            return best_pair
            if best_pair is not None:
                return best_pair

    cur5 = [_norm_base(x) for x in targ5]
    cur3 = [_norm_base(x) for x in targ3]
    base_obj = _objective_tuple(nums5, cur5, nums3, cur3)
    cur_obj = base_obj

    for _ in range(max_rounds):
        changed = False

        cand5 = _search_block_candidate(
            nums=nums5,
            targ_before=cur5,
            important_labels=set(_AA5_STEM_LABELS),
            objective_func=lambda c5: _objective_tuple(nums5, c5, nums3, cur3),
        )
        if cand5 is not None:
            obj5 = _objective_tuple(nums5, cand5, nums3, cur3)
            if obj5 < cur_obj:
                cur5 = cand5
                cur_obj = obj5
                changed = True

        cand3 = _search_block_candidate(
            nums=nums3,
            targ_before=cur3,
            important_labels=set(_AA3_STEM_LABELS),
            objective_func=lambda c3: _objective_tuple(nums5, cur5, nums3, c3),
        )
        if cand3 is not None:
            obj3 = _objective_tuple(nums5, cur5, nums3, cand3)
            if obj3 < cur_obj:
                cur3 = cand3
                cur_obj = obj3
                changed = True

        if not changed:
            break

    if cur_obj < base_obj:
        return cur5, cur3
    return None


def _extract_model_content(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        return text
    if not isinstance(data, dict):
        return text
    if "aa5_target_tokens" in data or "aa3_target_tokens" in data:
        return text
    msg_obj = data.get("message") or {}
    msg = msg_obj.get("content")
    if not msg:
        msg = msg_obj.get("reasoning_content")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg2_obj = choices[0].get("message") or {}
        msg2 = msg2_obj.get("content")
        if not msg2:
            msg2 = msg2_obj.get("reasoning_content")
        if isinstance(msg2, str) and msg2.strip():
            return msg2.strip()
    return text


def _find_json_object_with_keys(content: str, keys: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    text = str(content or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and any(k in obj for k in keys):
            return obj
    except Exception:
        pass

    dec = json.JSONDecoder()
    pos = 0
    found: Optional[Dict[str, Any]] = None
    while pos < len(text):
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = dec.raw_decode(text, start)
        except Exception:
            pos = start + 1
            continue
        if isinstance(obj, dict) and any(k in obj for k in keys):
            found = obj
        pos = max(end, start + 1)
    return found


def _parse_target_list(raw_list: Any, expected_len: int) -> Tuple[Optional[List[str]], str]:
    if not isinstance(raw_list, list):
        return None, "missing_tokens"
    if len(raw_list) != expected_len:
        return None, f"length_mismatch:{len(raw_list)}!={expected_len}"
    out: List[str] = []
    for tok in raw_list:
        t = _norm_base(tok)
        if t not in _VALID_BASES:
            return None, f"invalid_token:{tok}"
        out.append(t)
    return out, "ok"


def _parse_joint_targets(raw: str, len5: int, len3: int) -> Tuple[Optional[Tuple[List[str], List[str]]], str]:
    content = _extract_model_content(raw)
    obj = _find_json_object_with_keys(
        content,
        (
            "aa5_target_tokens",
            "aa3_target_tokens",
            "aminoacyl5_target_tokens",
            "aminoacyl3_target_tokens",
            "aa5",
            "aa3",
            "decision",
            "action",
            "no_change",
            "keep_current",
        ),
    )
    if obj is None:
        return None, "json_not_found"

    decision = str(obj.get("decision") or obj.get("action") or "").strip().lower()
    if decision in {"keep_current", "no_change", "keep", "pass_through"} or bool(obj.get("no_change")) or bool(obj.get("keep_current")):
        reason = str(obj.get("reason") or obj.get("message") or "").strip()
        return None, f"keep_current:{reason}" if reason else "keep_current"

    arr5 = (
        obj.get("aa5_target_tokens")
        if obj.get("aa5_target_tokens") is not None
        else obj.get("aminoacyl5_target_tokens")
    )
    if arr5 is None:
        arr5 = obj.get("aa5")

    arr3 = (
        obj.get("aa3_target_tokens")
        if obj.get("aa3_target_tokens") is not None
        else obj.get("aminoacyl3_target_tokens")
    )
    if arr3 is None:
        arr3 = obj.get("aa3")

    out5, status5 = _parse_target_list(arr5, len5)
    if out5 is None:
        return None, f"aa5_{status5}"
    out3, status3 = _parse_target_list(arr3, len3)
    if out3 is None:
        return None, f"aa3_{status3}"
    return (out5, out3), "ok"


def _format_global_ctx(meta_any: Dict[str, Any]) -> str:
    g = meta_any.get("global_struct_summary")
    if not isinstance(g, dict):
        return "(unavailable)"
    keep_keys = [
        "annotation_source",
        "segmentation_status",
        "segmentation_errors",
        "trnascan_hit",
        "trnascan_isotype",
        "empty_regions",
        "non_variable_pair_count",
    ]
    slim = {k: g.get(k) for k in keep_keys}
    return json.dumps(slim, ensure_ascii=False)


def _rows_for_prompt(nums: List[str], templ: List[str], targ: List[str], struct: str) -> List[Dict[str, Any]]:
    struct_tokens = _build_struct_tokens_for_columns(targ, struct)
    rows: List[Dict[str, Any]] = []
    for i, (n, tb, qb, st) in enumerate(zip(nums, templ, targ, struct_tokens)):
        rows.append(
            {
                "col_idx": i,
                "id": str(n or ""),
                "template": _norm_base(tb),
                "target": _norm_base(qb),
                "struct": st,
            }
        )
    return rows


def _build_messages(
    nums5: List[str],
    templ5: List[str],
    targ5: List[str],
    meta5: Dict[str, Any],
    nums3: List[str],
    templ3: List[str],
    targ3: List[str],
    meta3: Dict[str, Any],
    cfg: Dict[str, Any],
) -> List[Dict[str, str]]:
    pair_stats = _pair_stats(nums5, targ5, nums3, targ3)
    hard_stats = _hard_constraint_stats(nums5, targ5, nums3, targ3)
    baseline_clean = _cleanliness_metrics(nums5, targ5, nums3, targ3)
    tmpl5_stats = _template_similarity_stats(templ5, targ5)
    tmpl3_stats = _template_similarity_stats(templ3, targ3)
    rows5 = _rows_for_prompt(nums5, templ5, targ5, str(meta5.get("input_struct") or ""))
    rows3 = _rows_for_prompt(nums3, templ3, targ3, str(meta3.get("input_struct") or ""))

    sys_prompt = _effective_system_prompt(cfg).strip()
    user_prompt = (
        "PROJECT BACKGROUND:\n"
        "- This project aligns tRNA sequences to Sprinzl numbering with structure awareness.\n"
        "- We are editing ONLY two blocks jointly: Aminoacyl arm 5' end + Aminoacyl arm 3' end.\n"
        "- Never edit outside these two blocks.\n\n"
        "HARD ACCEPTANCE RULES:\n"
        "1) Return EXACT lengths for both block target token lists.\n"
        "2) Allowed tokens: A/U/G/C/N/- only.\n"
        "3) Preserve exact non-gap order within each block.\n"
        "4) Canonical acceptor pairs are 1-72,2-71,3-70,4-69,5-68,6-67,7-66.\n"
        "5) Conditional hard rule: if a full 7-step acceptor chain is achievable, it must use these canonical labels, not insertion-shifted labels.\n"
        "6) Minimize unmatched acceptor pairs first (gap pairs + non-canonical pairs).\n"
        "7) Positions 74/75/76 are reserved for one complete CCA tail or are all unoccupied; partial or non-CCA terminal residues must not be placed there or in 74i*/75i*/76i*. A 73i* label may represent an unresolved terminal extension.\n"
        "8) If position -1 has a base, position 1 must not be '-'.\n"
        "9) Soft caution: if -1 has a base while any of 1..9 is '-', this may be AA5 left-shift artifact; re-evaluate trade-offs.\n"
        "10) Keep numbering clean: avoid insertion labels unless strictly necessary.\n"
        "11) If adding insertion occupancy increases canonical stem '-' or does not reduce unmatched pairs, it is unacceptable.\n"
        "12) In AA region, i and canonical '-' coexisting is high-risk; keep i count near 0 while canonical gaps remain.\n"
        "13) Treat templates as soft prior only; low template reliability means structure-first.\n"
        "14) Under low template reliability, do not introduce extra insertion occupancy unless unmatched pairs strictly decrease.\n\n"
        "NO-CHANGE PROTOCOL:\n"
        "- If the current AA5+AA3 blocks already satisfy hard constraints and you cannot STRICTLY improve the objective,\n"
        "  return {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}.\n"
        "- Do not invent edits only to make the output look different.\n\n"
        "STRUCTURE CONTEXT:\n"
        f"- global_context(aa5)={_format_global_ctx(meta5)}\n"
        f"- global_context(aa3)={_format_global_ctx(meta3)}\n"
        f"- current_pair_stats={json.dumps(pair_stats, ensure_ascii=False)}\n\n"
        f"- current_hard_constraints={json.dumps(hard_stats, ensure_ascii=False)}\n\n"
        f"- current_cleanliness={json.dumps(baseline_clean, ensure_ascii=False)}\n\n"
        "TEMPLATE RELIABILITY:\n"
        f"- aa5_template_similarity={json.dumps(tmpl5_stats, ensure_ascii=False)}\n"
        f"- aa3_template_similarity={json.dumps(tmpl3_stats, ensure_ascii=False)}\n\n"
        "AA5 BLOCK COLUMNS:\n"
        f"{json.dumps(rows5, ensure_ascii=False)}\n\n"
        "AA3 BLOCK COLUMNS:\n"
        f"{json.dumps(rows3, ensure_ascii=False)}\n\n"
        "Return ONLY JSON:\n"
        "{\"aa5_target_tokens\": [\"A\", \"-\", ...], \"aa3_target_tokens\": [\"A\", \"-\", ...]}\n"
        "or {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}"
    )
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _is_improved_candidate(
    baseline_obj: Tuple[int, ...],
    cand_obj: Tuple[int, ...],
) -> bool:
    return cand_obj < baseline_obj


def _llm_propose_joint(
    nums5: List[str],
    templ5: List[str],
    targ5: List[str],
    meta5: Dict[str, Any],
    nums3: List[str],
    templ3: List[str],
    targ3: List[str],
    meta3: Dict[str, Any],
    cfg: Dict[str, Any],
    attempt_logs: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Tuple[List[str], List[str]]]:
    messages = _build_messages(nums5, templ5, targ5, meta5, nums3, templ3, targ3, meta3, cfg)
    baseline_pairs = _pair_stats(nums5, targ5, nums3, targ3)
    baseline_hard = _hard_constraint_stats(nums5, targ5, nums3, targ3)
    baseline_score, _ = _score_acceptor_joint(nums5, targ5, nums3, targ3)
    baseline_obj = _objective_tuple(nums5, targ5, nums3, targ3)
    baseline_clean = _cleanliness_metrics(nums5, targ5, nums3, targ3)
    tmpl5_stats = _template_similarity_stats(templ5, targ5)
    tmpl3_stats = _template_similarity_stats(templ3, targ3)
    template_reliability_low = (
        str(tmpl5_stats.get("reliability") or "").lower() == "low"
        or str(tmpl3_stats.get("reliability") or "").lower() == "low"
    )
    expected_len5 = len(targ5)
    expected_len3 = len(targ3)

    det_candidate: Optional[Tuple[List[str], List[str]]] = None
    try:
        det_try = _deterministic_joint_candidate(nums5, targ5, nums3, targ3)
    except Exception as exc:
        if attempt_logs is not None:
            attempt_logs.append(
                {
                    "attempt": "deterministic_search",
                    "status": "error",
                    "error": _log_trunc(str(exc), 240),
                }
            )
        det_try = None

    if det_try is not None:
        det5, det3 = det_try
        if _order_preserved(targ5, det5) and _order_preserved(targ3, det3):
            det_pairs = _pair_stats(nums5, det5, nums3, det3)
            det_hard = _hard_constraint_stats(nums5, det5, nums3, det3)
            det_score, _ = _score_acceptor_joint(nums5, det5, nums3, det3)
            det_obj = _objective_tuple(nums5, det5, nums3, det3)
            det_clean = _cleanliness_metrics(nums5, det5, nums3, det3)
            det_regress, det_regress_reasons = _insertion_regression_guard(
                baseline_clean,
                det_clean,
                template_reliability_low=template_reliability_low,
            )
            det_improved = _is_improved_candidate(
                baseline_obj=baseline_obj,
                cand_obj=det_obj,
            )
            det_hard_ok = int(det_hard.get("violations") or 0) == 0
            if attempt_logs is not None:
                attempt_logs.append(
                    {
                        "attempt": "deterministic_search",
                        "status": (
                            "candidate_ready"
                            if (det_improved and det_hard_ok and (not det_regress))
                            else (
                                "cleanliness_regression"
                                if (det_improved and det_regress)
                                else ("hard_constraint_failed" if det_improved else "not_improved")
                            )
                        ),
                        "baseline_objective": list(baseline_obj),
                        "candidate_objective": list(det_obj),
                        "baseline_score": baseline_score,
                        "candidate_score": det_score,
                        "baseline_hard_violations": baseline_hard["violations"],
                        "candidate_hard_violations": det_hard["violations"],
                        "baseline_unmatched_pairs": baseline_pairs["unmatched_pairs"],
                        "candidate_unmatched_pairs": det_pairs["unmatched_pairs"],
                        "baseline_cleanliness": baseline_clean,
                        "candidate_cleanliness": det_clean,
                        "candidate_regress_reasons": det_regress_reasons,
                        "candidate_hard_reasons": list(det_hard.get("reasons") or [])[:8],
                    }
                )
            if det_improved and det_hard_ok and (not det_regress):
                det_candidate = (det5, det3)
                changed5 = [
                    {"id": str(nums5[i] or ""), "before": _norm_base(targ5[i]), "suggested": _norm_base(det5[i])}
                    for i in range(len(nums5))
                    if _norm_base(targ5[i]) != _norm_base(det5[i])
                ]
                changed3 = [
                    {"id": str(nums3[i] or ""), "before": _norm_base(targ3[i]), "suggested": _norm_base(det3[i])}
                    for i in range(len(nums3))
                    if _norm_base(targ3[i]) != _norm_base(det3[i])
                ]
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "DETERMINISTIC SEARCH HINT (candidate only, not final):\n"
                            "- Candidate built by insertion-gap relocation under joint acceptor objective.\n"
                            "- Use it if helpful, or return a better one.\n"
                            f"- baseline_pair_stats={json.dumps(baseline_pairs, ensure_ascii=False)}\n"
                            f"- candidate_pair_stats={json.dumps(det_pairs, ensure_ascii=False)}\n"
                            f"- baseline_hard_constraints={json.dumps(baseline_hard, ensure_ascii=False)}\n"
                            f"- candidate_hard_constraints={json.dumps(det_hard, ensure_ascii=False)}\n"
                            f"- baseline_cleanliness={json.dumps(baseline_clean, ensure_ascii=False)}\n"
                            f"- candidate_cleanliness={json.dumps(det_clean, ensure_ascii=False)}\n"
                            f"- baseline_score={baseline_score}, candidate_score={det_score}\n"
                            f"- aa5_candidate={json.dumps(det5, ensure_ascii=False)}\n"
                            f"- aa3_candidate={json.dumps(det3, ensure_ascii=False)}\n"
                            f"- aa5_changed={json.dumps(changed5, ensure_ascii=False)}\n"
                            f"- aa3_changed={json.dumps(changed3, ensure_ascii=False)}"
                        ),
                    }
                )
    elif attempt_logs is not None:
        attempt_logs.append({"attempt": "deterministic_search", "status": "no_candidate"})

    start = time.monotonic()
    attempts = 0
    aa5_shift_warned_once = False
    while attempts <= int(cfg["max_retries"]):
        attempts += 1
        log: Dict[str, Any] = {"attempt": attempts}
        if cfg["mode"] == "ollama":
            url = f"{cfg['base_url']}/api/chat"
            payload = {
                "model": cfg["model_name"],
                "messages": messages,
                "options": {"temperature": 0},
                "format": "json",
                "stream": bool(cfg["streaming"]),
            }
        else:
            url = openai_chat_completions_url(str(cfg["base_url"]))
            payload = {
                "model": cfg["model_name"],
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": int(cfg["max_tokens"]),
                "stream": bool(cfg["streaming"]),
                "response_format": {"type": "json_object"},
            }
            if "reasoner" in str(cfg.get("model_name") or "").lower():
                payload["reasoning_effort"] = "low"
            apply_openai_provider_extras(payload, cfg, model_name=str(cfg.get("model_name") or ""))

        ok, raw = _http_post_json(url, payload, cfg)
        if not ok:
            log["status"] = "http_failed"
            if attempt_logs is not None:
                attempt_logs.append(log)
            if (time.monotonic() - start) >= float(cfg["hard_budget_s"]):
                return det_candidate
            delay = min(float(cfg["backoff_base_s"]) * (2 ** (attempts - 1)), 2.0)
            time.sleep(delay)
            continue

        log["raw_response"] = _log_trunc(raw or "", 1200)
        parsed, parse_status = _parse_joint_targets(raw, expected_len5, expected_len3)
        if parsed is None:
            keep_current_reason = parse_keep_current_status(parse_status)
            if keep_current_reason is not None:
                log["status"] = "keep_current"
                if keep_current_reason:
                    log["message"] = keep_current_reason
                if attempt_logs is not None:
                    attempt_logs.append(log)
                return None
            log["status"] = parse_status
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"FEEDBACK: invalid JSON response ({parse_status}). "
                        "Return only JSON with keys aa5_target_tokens and aa3_target_tokens, "
                        "both exact required lengths."
                    ),
                }
            )
            continue

        cand5, cand3 = parsed
        if not _order_preserved(targ5, cand5) or not _order_preserved(targ3, cand3):
            log["status"] = "order_changed"
            if attempt_logs is not None:
                attempt_logs.append(log)
            before5 = "".join(_non_gap_tokens(targ5))
            after5 = "".join(_non_gap_tokens(cand5))
            before3 = "".join(_non_gap_tokens(targ3))
            after3 = "".join(_non_gap_tokens(cand3))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: non-gap order changed. "
                        f"aa5_before={before5} aa5_after={after5}; "
                        f"aa3_before={before3} aa3_after={after3}. "
                        "Regenerate while preserving exact base order in each block."
                    ),
                }
            )
            continue

        cand_hard = _hard_constraint_stats(nums5, cand5, nums3, cand3)
        log["candidate_hard_violations"] = cand_hard["violations"]
        if int(cand_hard["violations"]) > 0:
            log["status"] = "hard_constraint_failed"
            log["hard_reasons"] = list(cand_hard.get("reasons") or [])[:8]
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: hard constraints failed. "
                        f"candidate_hard={json.dumps(cand_hard, ensure_ascii=False)}. "
                        "You must satisfy both: "
                        "if a full 7-step acceptor chain is achievable then it must be on canonical labels 1-72..7-66 (not insertion-shifted), "
                        "74/75/76 reserved for one complete CCA tail or all gaps; partial or non-CCA terminal residues cannot use these columns or 74i*/75i*/76i*, while 73i* may represent an unresolved extension, "
                        "and avoid stem insertion occupancy when canonical stem gaps remain, "
                        "and no '-1 base with position 1 gap'."
                    ),
                }
            )
            continue

        cand_pairs = _pair_stats(nums5, cand5, nums3, cand3)
        cand_score, reasons = _score_acceptor_joint(nums5, cand5, nums3, cand3)
        cand_obj = _objective_tuple(nums5, cand5, nums3, cand3)
        cand_clean = _cleanliness_metrics(nums5, cand5, nums3, cand3)
        clean_regress, clean_regress_reasons = _insertion_regression_guard(
            baseline_clean,
            cand_clean,
            template_reliability_low=template_reliability_low,
        )
        log["candidate_objective"] = list(cand_obj)
        log["baseline_objective"] = list(baseline_obj)
        log["candidate_score"] = cand_score
        log["baseline_score"] = baseline_score
        log["candidate_unmatched_pairs"] = cand_pairs["unmatched_pairs"]
        log["baseline_unmatched_pairs"] = baseline_pairs["unmatched_pairs"]
        log["baseline_hard_violations"] = baseline_hard["violations"]
        log["baseline_cleanliness"] = baseline_clean
        log["candidate_cleanliness"] = cand_clean
        log["score_reasons"] = reasons[:8]
        log["clean_regress_reasons"] = clean_regress_reasons
        pair_detail = _pair_state_detail(nums5, cand5, nums3, cand3)
        log["candidate_pair_detail"] = pair_detail
        improved = _is_improved_candidate(
            baseline_obj=baseline_obj,
            cand_obj=cand_obj,
        )

        aa5_shift_pattern = bool(cand_hard.get("aa5_minus1_core_gap_pattern", False))
        aa5_shift_labels = list(cand_hard.get("aa5_minus1_core_gap_labels") or [])
        log["aa5_shift_pattern"] = aa5_shift_pattern
        log["aa5_shift_labels"] = aa5_shift_labels

        if clean_regress:
            log["status"] = "cleanliness_regression"
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: insertion-cleanliness regression. "
                        f"baseline_clean={json.dumps(baseline_clean, ensure_ascii=False)}, "
                        f"candidate_clean={json.dumps(cand_clean, ensure_ascii=False)}, "
                        f"reasons={json.dumps(clean_regress_reasons, ensure_ascii=False)}. "
                        "Keep alignment clean: avoid insertion labels unless they clearly reduce unmatched pairs "
                        "and do not increase canonical stem gaps."
                    ),
                }
            )
            continue

        if improved and aa5_shift_pattern and (not aa5_shift_warned_once):
            aa5_shift_warned_once = True
            log["status"] = "aa5_shift_pattern_warn_retry"
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: AA5 shows possible left-shift pattern "
                        f"(-1 occupied while core labels have gaps: {json.dumps(aa5_shift_labels, ensure_ascii=False)}). "
                        "Please weigh trade-offs: if this placement is not clearly beneficial, prefer moving the gap toward -1 "
                        "and keep core 1..9 cleaner while preserving base order and overall objective."
                    ),
                }
            )
            continue

        shifted_bad, shifted_bad_reasons = _shifted_bad_alignment_guard(
            baseline_pairs=baseline_pairs,
            candidate_pairs=cand_pairs,
            candidate_clean=cand_clean,
        )
        log["shifted_bad_reasons"] = shifted_bad_reasons
        if improved and shifted_bad:
            log["status"] = "shift_like_rejected_retry"
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: candidate still looks globally shifted / pairing-collapsed. "
                        f"candidate_pair_stats={json.dumps(cand_pairs, ensure_ascii=False)}, "
                        f"candidate_pair_detail={json.dumps(pair_detail, ensure_ascii=False)}, "
                        f"candidate_clean={json.dumps(cand_clean, ensure_ascii=False)}, "
                        f"reasons={json.dumps(shifted_bad_reasons, ensure_ascii=False)}. "
                        "Re-optimize and prioritize canonical acceptor chain (1-72..7-66), "
                        "minimize canonical pair breaks, and keep insertion occupancy near zero unless it strictly reduces unmatched pairs."
                    ),
                }
            )
            continue

        if not improved:
            log["status"] = "not_improved"
            if attempt_logs is not None:
                attempt_logs.append(log)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "FEEDBACK: your result did not improve acceptor-stem objective. "
                        f"baseline_obj={list(baseline_obj)}, candidate_obj={list(cand_obj)}, "
                        f"baseline_score={baseline_score}, candidate_score={cand_score}, "
                        f"baseline_unmatched_pairs={baseline_pairs['unmatched_pairs']}, "
                        f"candidate_unmatched_pairs={cand_pairs['unmatched_pairs']}. "
                        "Priority order is: hard constraints -> unmatched pairs -> insertions -> score."
                    ),
                }
            )
            continue

        log["status"] = "accepted"
        if attempt_logs is not None:
            attempt_logs.append(log)
        return cand5, cand3

    if cfg["mode"] == "openai" and "reasoner" in str(cfg.get("model_name") or "").lower():
        fb_log: Dict[str, Any] = {"attempt": "fallback_chat"}
        fb_url = openai_chat_completions_url(str(cfg["base_url"]))
        fb_payload = {
            "model": provider_fallback_model(
                cfg,
                deepseek_fallback_model(runtime_values),
            ),
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": int(cfg["max_tokens"]),
            "stream": bool(cfg["streaming"]),
            "response_format": {"type": "json_object"},
        }
        apply_openai_provider_extras(fb_payload, cfg, model_name=str(fb_payload.get("model") or ""))
        ok, raw = _http_post_json(fb_url, fb_payload, cfg)
        if not ok:
            fb_log["status"] = "http_failed"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate

        fb_log["raw_response"] = _log_trunc(raw or "", 1200)
        parsed, parse_status = _parse_joint_targets(raw, expected_len5, expected_len3)
        if parsed is None:
            keep_current_reason = parse_keep_current_status(parse_status)
            if keep_current_reason is not None:
                fb_log["status"] = "keep_current"
                if keep_current_reason:
                    fb_log["message"] = keep_current_reason
                if attempt_logs is not None:
                    attempt_logs.append(fb_log)
                return None
            fb_log["status"] = parse_status
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate
        cand5, cand3 = parsed
        if not _order_preserved(targ5, cand5) or not _order_preserved(targ3, cand3):
            fb_log["status"] = "order_changed"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate

        cand_hard = _hard_constraint_stats(nums5, cand5, nums3, cand3)
        fb_log["candidate_hard_violations"] = cand_hard["violations"]
        if int(cand_hard["violations"]) > 0:
            fb_log["status"] = "hard_constraint_failed"
            fb_log["hard_reasons"] = list(cand_hard.get("reasons") or [])[:8]
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate

        cand_pairs = _pair_stats(nums5, cand5, nums3, cand3)
        cand_score, reasons = _score_acceptor_joint(nums5, cand5, nums3, cand3)
        cand_obj = _objective_tuple(nums5, cand5, nums3, cand3)
        cand_clean = _cleanliness_metrics(nums5, cand5, nums3, cand3)
        clean_regress, clean_regress_reasons = _insertion_regression_guard(
            baseline_clean,
            cand_clean,
            template_reliability_low=template_reliability_low,
        )
        fb_log["candidate_objective"] = list(cand_obj)
        fb_log["baseline_objective"] = list(baseline_obj)
        fb_log["candidate_score"] = cand_score
        fb_log["baseline_score"] = baseline_score
        fb_log["candidate_unmatched_pairs"] = cand_pairs["unmatched_pairs"]
        fb_log["baseline_unmatched_pairs"] = baseline_pairs["unmatched_pairs"]
        fb_log["baseline_hard_violations"] = baseline_hard["violations"]
        fb_log["baseline_cleanliness"] = baseline_clean
        fb_log["candidate_cleanliness"] = cand_clean
        fb_log["score_reasons"] = reasons[:8]
        fb_log["clean_regress_reasons"] = clean_regress_reasons
        pair_detail = _pair_state_detail(nums5, cand5, nums3, cand3)
        fb_log["candidate_pair_detail"] = pair_detail
        if clean_regress:
            fb_log["status"] = "cleanliness_regression"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate
        shifted_bad, shifted_bad_reasons = _shifted_bad_alignment_guard(
            baseline_pairs=baseline_pairs,
            candidate_pairs=cand_pairs,
            candidate_clean=cand_clean,
        )
        fb_log["shifted_bad_reasons"] = shifted_bad_reasons
        if shifted_bad:
            fb_log["status"] = "shift_like_rejected"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate
        if not _is_improved_candidate(
            baseline_obj=baseline_obj,
            cand_obj=cand_obj,
        ):
            fb_log["status"] = "not_improved"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            return det_candidate

        fb_log["status"] = "accepted"
        if attempt_logs is not None:
            attempt_logs.append(fb_log)
        return cand5, cand3

    return det_candidate


def _find_region_indices(blocks: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    idx5: Optional[int] = None
    idx3: Optional[int] = None
    for i, blk in enumerate(blocks):
        region = str((blk or {}).get("region") or "")
        if region == AA5_REGION and idx5 is None:
            idx5 = i
        elif region == AA3_REGION and idx3 is None:
            idx3 = i
    return idx5, idx3


def _extract_lines(block: Dict[str, Any]) -> Optional[Tuple[List[str], List[str], List[str], Dict[str, Any]]]:
    lines = (block or {}).get("lines")
    if not (isinstance(lines, list) and len(lines) == 3):
        return None
    nums = list(lines[0])
    templ = list(lines[1])
    targ = [_norm_base(x) for x in list(lines[2])]
    meta = (block or {}).get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return nums, templ, targ, meta


def postcheck_aminoacyl_joint(
    blocks: List[Dict[str, Any]],
    runtime_values: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    idx5, idx3 = _find_region_indices(blocks)
    if idx5 is None or idx3 is None:
        return list(blocks)

    blk5 = blocks[idx5]
    blk3 = blocks[idx3]
    ext5 = _extract_lines(blk5)
    ext3 = _extract_lines(blk3)
    if ext5 is None or ext3 is None:
        return list(blocks)

    nums5, templ5, targ5, meta5 = ext5
    nums3, templ3, targ3, meta3 = ext3

    cfg = _load_cfg(runtime_values)
    _log_runtime_cfg(cfg)
    if not bool(cfg.get("postcheck_on")):
        return list(blocks)

    configured_prompt_5 = str(cfg.get("aa5_system_prompt") or "").strip()
    configured_prompt_3 = str(cfg.get("aa3_system_prompt") or "").strip()
    if not str(cfg.get("aa5_system_prompt") or "").strip() and not str(cfg.get("aa3_system_prompt") or "").strip():
        cfg = dict(cfg)
        cfg["aa5_system_prompt"] = DEFAULT_AA_JOINT_SYSTEM_PROMPT
        cfg["aa3_system_prompt"] = ""

    messages = _build_messages(
        nums5, templ5, targ5, meta5,
        nums3, templ3, targ3, meta3,
        cfg,
    )
    attempt_logs: List[Dict[str, Any]] = []
    candidate = _llm_propose_joint(
        nums5, templ5, targ5, meta5,
        nums3, templ3, targ3, meta3,
        cfg,
        attempt_logs=attempt_logs,
    )
    if candidate is None:
        print("[POSTCHECK][Aminoacyl joint] no improved candidate; pass-through.")
        return apply_joint_postcheck_result(
            blocks=list(blocks),
            idx5=idx5,
            idx3=idx3,
            nums5=nums5,
            templ5=templ5,
            targ5=targ5,
            meta5=meta5,
            nums3=nums3,
            templ3=templ3,
            targ3=targ3,
            meta3=meta3,
            configured_prompt=(configured_prompt_5 or configured_prompt_3),
            effective_prompt=str(messages[0].get("content") or "") if messages else "",
            user_prompt=str(messages[1].get("content") or "") if len(messages) > 1 else "",
            attempt_logs=attempt_logs,
            aa5_candidate=None,
            aa3_candidate=None,
            no_candidate_message="No valid improved AA5/AA3 candidate returned.",
            success_message="Applied improved TARGET for AA5+AA3.",
        )

    cand5, cand3 = candidate

    cand_score, _ = _score_acceptor_joint(nums5, cand5, nums3, cand3)
    cand_pairs = _pair_stats(nums5, cand5, nums3, cand3)
    print("[POSTCHECK][Aminoacyl joint] applied improved TARGET for AA5+AA3.")
    return apply_joint_postcheck_result(
        blocks=list(blocks),
        idx5=idx5,
        idx3=idx3,
        nums5=nums5,
        templ5=templ5,
        targ5=targ5,
        meta5=meta5,
        nums3=nums3,
        templ3=templ3,
        targ3=targ3,
        meta3=meta3,
        configured_prompt=(configured_prompt_5 or configured_prompt_3),
        effective_prompt=str(messages[0].get("content") or "") if messages else "",
        user_prompt=str(messages[1].get("content") or "") if len(messages) > 1 else "",
        attempt_logs=attempt_logs,
        aa5_candidate=cand5,
        aa3_candidate=cand3,
        no_candidate_message="No valid improved AA5/AA3 candidate returned.",
        success_message="Applied improved TARGET for AA5+AA3.",
        success_payload={
            "candidate_score": cand_score,
            "candidate_pair_stats": cand_pairs,
        },
    )


def trace_aminoacyl_joint_blocks(
    blocks: List[Dict[str, Any]],
    runtime_values: Optional[Dict[str, Any]] = None,
    run_llm: bool = True,
) -> Dict[str, Any]:
    idx5, idx3 = _find_region_indices(blocks)
    if idx5 is None or idx3 is None:
        return invalid_trace_preview(
            "AA5 or AA3 block is missing.",
            extra={
                "status": "missing_blocks",
                "aa5_target_tokens": None,
                "aa3_target_tokens": None,
            },
        )

    ext5 = _extract_lines(blocks[idx5])
    ext3 = _extract_lines(blocks[idx3])
    if ext5 is None or ext3 is None:
        return invalid_trace_preview(
            "Invalid AA block: expected 3 lines.",
            extra={
                "aa5_target_tokens": None,
                "aa3_target_tokens": None,
            },
        )

    nums5, templ5, targ5, meta5 = ext5
    nums3, templ3, targ3, meta3 = ext3
    cfg = _load_cfg(runtime_values)

    configured5 = str(cfg.get("aa5_system_prompt") or "").strip()
    configured3 = str(cfg.get("aa3_system_prompt") or "").strip()
    effective_prompt = _effective_system_prompt(cfg)
    cfg_eff = dict(cfg)
    cfg_eff["aa5_system_prompt"] = effective_prompt
    cfg_eff["aa3_system_prompt"] = ""

    messages = _build_messages(nums5, templ5, targ5, meta5, nums3, templ3, targ3, meta3, cfg_eff)
    baseline_pairs = _pair_stats(nums5, targ5, nums3, targ3)
    baseline_hard = _hard_constraint_stats(nums5, targ5, nums3, targ3)
    baseline_obj = _objective_tuple(nums5, targ5, nums3, targ3)
    baseline_score, baseline_reasons = _score_acceptor_joint(nums5, targ5, nums3, targ3)

    out: Dict[str, Any] = build_joint_trace_preview(
        configured_prompt=(configured5 or configured3),
        effective_prompt=effective_prompt,
        user_prompt=str(messages[1].get("content") or ""),
        postcheck_on=bool(cfg.get("postcheck_on")),
        run_llm=bool(run_llm),
        baseline_payload={
            "baseline_score": baseline_score,
            "baseline_objective": list(baseline_obj),
            "baseline_reasons": baseline_reasons[:12],
            "baseline_pair_stats": baseline_pairs,
            "baseline_hard_constraints": baseline_hard,
            "aa5_template_similarity": _template_similarity_stats(templ5, targ5),
            "aa3_template_similarity": _template_similarity_stats(templ3, targ3),
        },
    )

    if not bool(cfg.get("postcheck_on")):
        out["status"] = "postcheck_disabled"
        out["message"] = "LLM_POSTCHECK is disabled."
        return out
    if not run_llm:
        out["status"] = "preview_only"
        out["message"] = "Prompt preview only (LLM call skipped)."
        return out

    attempt_logs: List[Dict[str, Any]] = []
    candidate = _llm_propose_joint(
        nums5, templ5, targ5, meta5,
        nums3, templ3, targ3, meta3,
        cfg_eff,
        attempt_logs=attempt_logs,
    )
    success_payload = None
    aa5_candidate = None
    aa3_candidate = None
    if candidate is not None:
        aa5_candidate, aa3_candidate = candidate
        cand_pairs = _pair_stats(nums5, aa5_candidate, nums3, aa3_candidate)
        cand_hard = _hard_constraint_stats(nums5, aa5_candidate, nums3, aa3_candidate)
        cand_obj = _objective_tuple(nums5, aa5_candidate, nums3, aa3_candidate)
        cand_score, _ = _score_acceptor_joint(nums5, aa5_candidate, nums3, aa3_candidate)
        success_payload = {
            "candidate_score": cand_score,
            "candidate_objective": list(cand_obj),
            "candidate_pair_stats": cand_pairs,
            "candidate_hard_constraints": cand_hard,
        }
    return finalize_joint_trace_preview(
        out,
        aa5_candidate=aa5_candidate,
        aa3_candidate=aa3_candidate,
        attempt_logs=attempt_logs,
        success_payload=success_payload,
        no_candidate_message="No valid improved targets returned.",
        success_message="Aminoacyl joint prompt replay succeeded.",
    )
