# -*- coding: utf-8 -*-
"""
Anticodon loop + Anticodon stem postcheck.

Goal:
- Keep numbering/template lines unchanged.
- Let LLM propose a new TARGET token list for this block only.
- Enforce hard constraints (length, token set, base-preservation order).
- Accept only if region score improves (structure-aware, anticodon-aware).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .variable_loop import (
    _http_post_json,
    _load_runtime_cfg,
    _log_runtime_cfg,
    _log_trunc,
)
from ..runtime_helpers import deepseek_fallback_model, runtime_prompt_value
from .common import (
    provider_fallback_model,
    run_target_candidate_attempt,
    run_single_block_postcheck,
    trace_single_block_postcheck,
)

DEFAULT_ANTICODON_SYSTEM_PROMPT = (
    "You are a strict RNA alignment post-editor for Anticodon loop + Anticodon stem.\n"
    "Task: only rewrite TARGET tokens for this block.\n"
    "Hard rules:\n"
    "1) Never change numbering/template columns.\n"
    "2) Return exactly the same number of TARGET tokens.\n"
    "3) Preserve exact left-to-right order of all non-gap bases (no base loss, no reordering).\n"
    "4) Prioritize canonical anticodon-stem pairing by Sprinzl pairs: "
    "27-43, 28-42, 29-41, 30-40, 31-39.\n"
    "5) Minimize unmatched stem pairs (gap pairs + non-canonical pairs).\n"
    "6) Never place non-gap bases into insertion labels under 34 or 35 (e.g., 34i1, 35i1).\n"
    "7) If tRNAscan anticodon exists, Sprinzl 34/35/36 must match it exactly.\n"
    "8) Template bases are a soft prior only; when conflict exists, prioritize structure + anticodon evidence.\n"
    "9) If template similarity/reliability is low, do not optimize for template matching.\n"
    "10) Use only A/U/G/C/N or '-'.\n"
    "11) If the current block already satisfies hard constraints and you cannot STRICTLY improve the objective, "
    "return a no-change decision instead of fabricating edits.\n"
    "Return ONLY JSON in one of these forms:\n"
    "{\"target_tokens\": [\"A\", \"-\", ...]}\n"
    "or {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}."
)

_VALID_BASES = {"A", "U", "G", "C", "N", "-"}
_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_STEM_PAIR_LABELS: List[Tuple[str, str]] = [
    ("27", "43"),
    ("28", "42"),
    ("29", "41"),
    ("30", "40"),
    ("31", "39"),
]
_STEM_BASE_LABELS = {x for pair in _STEM_PAIR_LABELS for x in pair}
_TEMPLATE_RELIABILITY_LOW_OVERLAP = 4
_TEMPLATE_RELIABILITY_LOW_IDENTITY = 0.45
_TEMPLATE_RELIABILITY_MED_IDENTITY = 0.70
_ANTICODON_WINDOW_START = 31
_ANTICODON_WINDOW_END = 41
_TEMPLATE_INVALID_RATIO_LOW = 0.50
_TEMPLATE_INVALID_RATIO_HIGH = 0.80
_LEADING_INT_RE = re.compile(r"^-?\d+")
_SYSTEM_PROMPT_HARD_CONSTRAINT_APPENDIX = (
    "\n\nNON-NEGOTIABLE ANTICODON CONSTRAINTS:\n"
    "- Never place non-gap bases into insertion labels under 34 or 35 (34i*, 35i* must stay '-').\n"
    "- If tRNAscan anticodon exists, Sprinzl 34/35/36 must match it exactly.\n"
    "- Return ONLY JSON: {\"target_tokens\": [\"A\", \"-\", ...]}."
)

def _harden_anticodon_system_prompt(raw_prompt: str) -> str:
    prompt = str(raw_prompt or "").strip()
    if not prompt:
        return ""
    if _SYSTEM_PROMPT_HARD_CONSTRAINT_APPENDIX in prompt:
        return prompt
    return prompt + _SYSTEM_PROMPT_HARD_CONSTRAINT_APPENDIX


def _load_cfg(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _load_runtime_cfg(runtime_values)
    raw_prompt = runtime_prompt_value("LLM_ANTICODON_SYSTEM_PROMPT", runtime_values)
    cfg["anticodon_system_prompt"] = _harden_anticodon_system_prompt(raw_prompt)
    return cfg


def _norm_base(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    if s in _VALID_BASES:
        return s
    return "-"


def _norm_rna_triplet(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    s = "".join(ch for ch in s if ch in ("A", "U", "G", "C", "N"))
    return s


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


def _stem_pairs_present(nums: List[str]) -> List[Tuple[str, str]]:
    idx = _build_idx(nums)
    return [(l, r) for l, r in _STEM_PAIR_LABELS if l in idx and r in idx]


def _expected_anticodon(meta: Dict[str, Any]) -> str:
    g = meta.get("global_struct_summary")
    if not isinstance(g, dict):
        return ""
    return _norm_rna_triplet(g.get("trnascan_anticodon", ""))


def _pair_stats(nums: List[str], targ: List[str]) -> Dict[str, int]:
    idx = _build_idx(nums)
    total = 0
    canonical = 0
    gap_pairs = 0
    noncanonical = 0
    for left, right in _stem_pairs_present(nums):
        total += 1
        a = _norm_base(targ[idx[left]])
        b = _norm_base(targ[idx[right]])
        if a == "-" or b == "-":
            gap_pairs += 1
            continue
        if a + b in _CANONICAL_PAIRS:
            canonical += 1
        else:
            noncanonical += 1
    return {
        "total_pairs": total,
        "canonical_pairs": canonical,
        "gap_pairs": gap_pairs,
        "noncanonical_pairs": noncanonical,
        "unmatched_pairs": gap_pairs + noncanonical,
    }


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


def _label_parent(label: str) -> str:
    sid = str(label or "")
    if "i" not in sid:
        return sid
    return sid.split("i", 1)[0]


def _label_to_int(label: str) -> Optional[int]:
    sid = _label_parent(label)
    m = _LEADING_INT_RE.match(sid)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _trnascan_anticodon_slots(meta: Dict[str, Any]) -> Dict[str, str]:
    anti = _expected_anticodon(meta)
    if len(anti) != 3:
        return {}
    return {"34": anti[0], "35": anti[1], "36": anti[2]}


def _anticodon_window_template_stats(nums: List[str], templ: List[str], targ: List[str]) -> Dict[str, Any]:
    window_idx: List[int] = []
    for i, label in enumerate(nums):
        iv = _label_to_int(str(label or ""))
        if iv is None:
            continue
        if _ANTICODON_WINDOW_START <= iv <= _ANTICODON_WINDOW_END:
            window_idx.append(i)

    if not window_idx:
        return {
            "window_start": _ANTICODON_WINDOW_START,
            "window_end": _ANTICODON_WINDOW_END,
            "window_columns": 0,
            "template_non_gap": 0,
            "invalid_against_template": 0,
            "invalid_ratio": 1.0,
            "reliability": "low",
            "reason": "window_not_found",
            "template_similarity": _template_similarity_stats([], []),
        }

    t_sub = [_norm_base(templ[i]) for i in window_idx]
    q_sub = [_norm_base(targ[i]) for i in window_idx]
    base_stats = _template_similarity_stats(t_sub, q_sub)

    template_non_gap = sum(1 for b in t_sub if b != "-")
    invalid = sum(
        1
        for tb, qb in zip(t_sub, q_sub)
        if tb != "-" and (qb == "-" or qb != tb)
    )
    invalid_ratio = (invalid / template_non_gap) if template_non_gap else 1.0
    if invalid_ratio >= _TEMPLATE_INVALID_RATIO_HIGH:
        reliability = "low"
    elif invalid_ratio >= _TEMPLATE_INVALID_RATIO_LOW:
        reliability = "medium"
    else:
        reliability = "high"

    return {
        "window_start": _ANTICODON_WINDOW_START,
        "window_end": _ANTICODON_WINDOW_END,
        "window_columns": len(window_idx),
        "template_non_gap": int(template_non_gap),
        "invalid_against_template": int(invalid),
        "invalid_ratio": round(float(invalid_ratio), 4),
        "reliability": reliability,
        "reason": (
            "template_weak_for_anticodon_window"
            if reliability == "low"
            else "template_partially_trustworthy"
            if reliability == "medium"
            else "template_reasonably_consistent"
        ),
        "template_similarity": base_stats,
    }


def _validate_anticodon_hard_constraints(
    nums: List[str],
    candidate: List[str],
    meta: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    idx = _build_idx(nums)

    for required in ("34", "35", "36"):
        if required not in idx:
            violations.append(f"missing_required_slot:{required}")

    for label, base in zip(nums, candidate):
        parent = _label_parent(str(label or ""))
        if parent not in ("34", "35"):
            continue
        if not _is_insertion_label(str(label or "")):
            continue
        b = _norm_base(base)
        if b != "-":
            violations.append(f"forbidden_insertion_under_{parent}:{label}={b}")

    expected_slots = _trnascan_anticodon_slots(meta)
    if expected_slots and all(k in idx for k in ("34", "35", "36")):
        got = {k: _norm_base(candidate[idx[k]]) for k in ("34", "35", "36")}
        for k in ("34", "35", "36"):
            exp = expected_slots[k]
            obs = got[k]
            if obs != exp:
                violations.append(f"trnascan_anchor_mismatch:{k} expected={exp} got={obs}")

    return len(violations) == 0, violations


def _insertion_base_count(nums: List[str], targ: List[str]) -> int:
    return sum(1 for n, b in zip(nums, targ) if _is_insertion_label(str(n or "")) and _norm_base(b) != "-")


def _anticodon_anchor_penalty(nums: List[str], targ: List[str], meta: Dict[str, Any]) -> int:
    anti = _expected_anticodon(meta)
    idx = _build_idx(nums)
    if len(anti) != 3 or not all(k in idx for k in ("34", "35", "36")):
        return 0
    got = [_norm_base(targ[idx[k]]) for k in ("34", "35", "36")]
    pen = 0
    for g, e in zip(got, anti):
        if g == "-":
            pen += 2
        elif g != e:
            pen += 1
    return pen


def _objective_tuple(nums: List[str], targ: List[str], meta: Dict[str, Any]) -> Tuple[int, int, int, int]:
    pairs = _pair_stats(nums, targ)
    score, _ = _score_anticodon_block(nums, targ, meta)
    anti_pen = _anticodon_anchor_penalty(nums, targ, meta)
    ins_pen = _insertion_base_count(nums, targ)
    # lower is better
    return (pairs["unmatched_pairs"], anti_pen, ins_pen, score)


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


def _objective_to_dict(obj: Tuple[int, int, int, int]) -> Dict[str, int]:
    return {
        "unmatched_pairs": int(obj[0]),
        "anticodon_anchor_penalty": int(obj[1]),
        "insertion_penalty": int(obj[2]),
        "score": int(obj[3]),
    }


def _changed_columns_from_masks(
    nums: List[str],
    mask_before: Tuple[bool, ...],
    mask_after: Tuple[bool, ...],
    limit: int = 16,
) -> List[str]:
    out: List[str] = []
    for i in range(min(len(mask_before), len(mask_after))):
        if mask_before[i] == mask_after[i]:
            continue
        sid = str(nums[i] or "").strip() or f"col_{i}"
        out.append(sid)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _deterministic_local_search_candidate(
    nums: List[str],
    targ_before: List[str],
    meta: Dict[str, Any],
    max_steps: int = 6,
    beam_width: int = 24,
    trace_out: Optional[Dict[str, Any]] = None,
) -> Optional[List[str]]:
    """
    Deterministic search for insertion-vs-gap misplacement:
    - move occupancy from insertion columns (e.g., 36i1) to canonical gap columns,
    - preserve non-gap base order strictly by rebuilding from the original sequence.
    """
    base_targ = [_norm_base(x) for x in targ_before]
    base_obj = _objective_tuple(nums, base_targ, meta)
    n = len(nums)
    if n == 0:
        return None

    mask0 = tuple(b != "-" for b in base_targ)
    best_mask = mask0
    best_targ = base_targ
    best_obj = base_obj
    visited = {mask0}
    beam: List[Tuple[Tuple[bool, ...], Tuple[int, int, int, int], List[str]]] = [(mask0, base_obj, base_targ)]
    important_labels = set(_STEM_BASE_LABELS) | {"34", "35", "36"}
    step_traces: List[Dict[str, Any]] = []

    if trace_out is not None:
        trace_out.clear()
        trace_out.update(
            {
                "config": {
                    "max_steps": int(max_steps),
                    "beam_width": int(beam_width),
                },
                "baseline_objective": _objective_to_dict(base_obj),
                "baseline_non_gap": "".join(_non_gap_tokens(base_targ)),
                "iterations": step_traces,
            }
        )

    for step in range(1, max_steps + 1):
        cand_pool: List[Tuple[Tuple[int, int, int, int], Tuple[bool, ...], List[str]]] = []
        step_trace: Dict[str, Any] = {
            "step": step,
            "beam_size_in": len(beam),
        }
        move_count = 0
        for mask, _, _ in beam:
            donors_ins = [i for i in range(n) if mask[i] and _is_insertion_label(str(nums[i] or ""))]
            acceptors_can = [j for j in range(n) if (not mask[j]) and (not _is_insertion_label(str(nums[j] or "")))]
            moves: set[Tuple[int, int]] = set()

            # user-specified pattern: insertion before downstream gap
            for d in donors_ins:
                for a in acceptors_can:
                    if a > d:
                        moves.add((d, a))

            # fallback: insertion -> any canonical gap
            if not moves:
                for d in donors_ins:
                    for a in acceptors_can:
                        if a != d:
                            moves.add((d, a))

            # secondary: move from non-important filled canonical cols to important gaps
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
            move_count += len(moves)

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
                cand_obj = _objective_tuple(nums, cand_targ, meta)
                cand_pool.append((cand_obj, nmt, cand_targ))
        step_trace["expanded_moves"] = int(move_count)
        step_trace["candidate_pool_size"] = len(cand_pool)

        if not cand_pool:
            step_trace["status"] = "no_candidate_pool"
            step_traces.append(step_trace)
            break

        cand_pool.sort(key=lambda x: x[0])
        next_beam: List[Tuple[Tuple[bool, ...], Tuple[int, int, int, int], List[str]]] = []
        for obj, m, t in cand_pool[:beam_width]:
            next_beam.append((m, obj, t))
        beam = next_beam

        top_obj, top_m, top_t = cand_pool[0]
        step_trace["best_objective"] = _objective_to_dict(top_obj)
        step_trace["best_changed_columns"] = _changed_columns_from_masks(nums, mask0, top_m)
        top_candidates: List[Dict[str, Any]] = []
        for obj, m, t in cand_pool[: min(3, len(cand_pool))]:
            hard_ok, hard_violations = _validate_anticodon_hard_constraints(nums, t, meta)
            top_candidates.append(
                {
                    "objective": _objective_to_dict(obj),
                    "changed_columns": _changed_columns_from_masks(nums, mask0, m),
                    "hard_ok": bool(hard_ok),
                    "violations": hard_violations[:6],
                }
            )
        step_trace["top_candidates"] = top_candidates
        step_trace["status"] = "ok"
        step_traces.append(step_trace)

        if top_obj < best_obj:
            best_obj = top_obj
            best_mask = top_m
            best_targ = top_t

    best_improved = bool(best_obj < base_obj and best_mask != mask0)
    best_hard_ok = False
    best_hard_violations: List[str] = []
    if best_improved:
        best_hard_ok, best_hard_violations = _validate_anticodon_hard_constraints(nums, best_targ, meta)
    if trace_out is not None:
        trace_out["best_objective"] = _objective_to_dict(best_obj)
        trace_out["best_changed_columns"] = _changed_columns_from_masks(nums, mask0, best_mask)
        trace_out["found_improvement"] = bool(best_improved)
        trace_out["best_hard_ok"] = bool(best_hard_ok)
        trace_out["best_hard_violations"] = best_hard_violations[:12]

    if best_obj < base_obj and best_mask != mask0:
        return best_targ
    return None


def _score_anticodon_block(nums: List[str], targ: List[str], meta: Dict[str, Any]) -> Tuple[int, List[str]]:
    idx = _build_idx(nums)
    score = 0
    reasons: List[str] = []

    for left, right in _stem_pairs_present(nums):
        a = _norm_base(targ[idx[left]])
        b = _norm_base(targ[idx[right]])
        if a == "-" or b == "-":
            score += 4
            reasons.append(f"gap_pair:{left}-{right}={a}/{b}")
            continue
        if a + b not in _CANONICAL_PAIRS:
            score += 2
            reasons.append(f"noncanonical:{left}-{right}={a}{b}")

    anti = _expected_anticodon(meta)
    if len(anti) == 3 and all(k in idx for k in ("34", "35", "36")):
        got = "".join(_norm_base(targ[idx[k]]) for k in ("34", "35", "36"))
        if "-" in got:
            score += 6
            reasons.append(f"anticodon_gap:got={got} expected={anti}")
        else:
            mism = sum(1 for x, y in zip(got, anti) if x != y)
            if mism:
                score += 3 * mism
                reasons.append(f"anticodon_mismatch:got={got} expected={anti}")

    for label, base in zip(nums, targ):
        sid = str(label or "")
        if "i" not in sid:
            continue
        parent = _label_parent(sid)
        b = _norm_base(base)
        if parent in ("34", "35") and b != "-":
            score += 8
            reasons.append(f"forbidden_ac_insertion:{sid}={b}")
            continue
        if parent in _STEM_BASE_LABELS and b != "-":
            score += 2
            reasons.append(f"stem_insertion:{sid}={b}")

    return score, reasons


def _is_improved_candidate(
    baseline_pairs: Dict[str, int],
    baseline_score: int,
    cand_pairs: Dict[str, int],
    cand_score: int,
) -> bool:
    if cand_pairs["unmatched_pairs"] < baseline_pairs["unmatched_pairs"]:
        return True
    if (
        cand_pairs["unmatched_pairs"] == baseline_pairs["unmatched_pairs"]
        and cand_score < baseline_score
    ):
        return True
    return False


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
    if "target_tokens" in data:
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

    # Fast path: whole response is already a JSON object.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and any(k in obj for k in keys):
            return obj
    except Exception:
        pass

    # Robust scan: decode every JSON object substring and keep the last matching one.
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


def _parse_target_tokens(raw: str, expected_len: int) -> Tuple[Optional[List[str]], str]:
    content = _extract_model_content(raw)
    obj = _find_json_object_with_keys(content, ("target_tokens", "target", "decision", "action", "no_change", "keep_current"))
    if obj is None:
        return None, "json_not_found"

    decision = str(obj.get("decision") or obj.get("action") or "").strip().lower()
    if decision in {"keep_current", "no_change", "keep", "pass_through"} or bool(obj.get("no_change")) or bool(obj.get("keep_current")):
        reason = str(obj.get("reason") or obj.get("message") or "").strip()
        return None, f"keep_current:{reason}" if reason else "keep_current"

    arr = obj.get("target_tokens")
    if arr is None:
        arr = obj.get("target")
    if not isinstance(arr, list):
        return None, "missing_target_tokens"
    if len(arr) != expected_len:
        return None, f"length_mismatch:{len(arr)}!={expected_len}"

    out: List[str] = []
    for tok in arr:
        t = _norm_base(tok)
        if t not in _VALID_BASES:
            return None, f"invalid_token:{tok}"
        out.append(t)
    return out, "ok"


def _format_global_ctx(meta: Dict[str, Any]) -> str:
    g = meta.get("global_struct_summary")
    if not isinstance(g, dict):
        return "(unavailable)"
    keep_keys = [
        "annotation_source",
        "segmentation_status",
        "segmentation_errors",
        "trnascan_hit",
        "trnascan_isotype",
        "trnascan_anticodon",
        "trnascan_anticodon_start",
        "trnascan_anticodon_end",
        "non_variable_pair_count",
    ]
    slim = {k: g.get(k) for k in keep_keys}
    return json.dumps(slim, ensure_ascii=False)


def _build_messages(
    nums: List[str],
    templ: List[str],
    targ_before: List[str],
    meta: Dict[str, Any],
    cfg: Dict[str, Any],
) -> List[Dict[str, str]]:
    region_struct = str(meta.get("input_struct") or "")
    struct_tokens = _build_struct_tokens_for_columns(targ_before, region_struct)
    pairs_present = _stem_pairs_present(nums)
    pair_stats = _pair_stats(nums, targ_before)
    template_stats = _template_similarity_stats(templ, targ_before)
    anticodon_window_stats = _anticodon_window_template_stats(nums, templ, targ_before)
    anti = _expected_anticodon(meta)
    anti_expected_slots = _trnascan_anticodon_slots(meta)
    idx = _build_idx(nums)
    anti_slots = {k: _norm_base(targ_before[idx[k]]) for k in ("34", "35", "36") if k in idx}

    rows = []
    for i, (n, tb, qb, st) in enumerate(zip(nums, templ, targ_before, struct_tokens)):
        rows.append(
            {
                "col_idx": i,
                "id": str(n or ""),
                "template": _norm_base(tb),
                "target": _norm_base(qb),
                "struct": st,
            }
        )

    sys_prompt = str(cfg.get("anticodon_system_prompt") or DEFAULT_ANTICODON_SYSTEM_PROMPT).strip()
    user_prompt = (
        "PROJECT BACKGROUND:\n"
        "- This project aligns tRNA sequences to Sprinzl numbering with structure awareness.\n"
        "- We are editing ONLY the Anticodon loop + Anticodon stem block.\n"
        "- Never edit outside this block.\n\n"
        "HARD ACCEPTANCE RULES:\n"
        "1) Return EXACTLY same number of tokens as current target block.\n"
        "2) Allowed tokens: A/U/G/C/N/- only.\n"
        "3) Preserve exact order of non-gap bases from current block.\n"
        "4) Prefer canonical stem pairing at 27-43, 28-42, 29-41, 30-40, 31-39.\n"
        "5) Minimize unmatched stem pairs (gap pairs + non-canonical pairs).\n"
        "6) Sprinzl 34 and 35 must NOT carry insertion labels with bases (34i*, 35i* must stay '-').\n"
        "7) If tRNAscan anticodon exists, Sprinzl 34/35/36 must match it exactly.\n"
        "8) Treat template bases as soft prior only; structure + anticodon evidence has higher priority.\n"
        "9) If template reliability is LOW, ignore template mismatch pressure and optimize structure first.\n\n"
        "NO-CHANGE PROTOCOL:\n"
        "- If the current block already satisfies hard constraints and you cannot STRICTLY improve the objective,\n"
        "  return {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}.\n"
        "- Do not invent edits just to return a different target.\n\n"
        "TEMPLATE RELIABILITY:\n"
        f"- template_similarity={json.dumps(template_stats, ensure_ascii=False)}\n"
        f"- anticodon_window_template={json.dumps(anticodon_window_stats, ensure_ascii=False)}\n"
        "- template_weight is advisory only; unmatched stem pairs and anticodon anchor are primary objectives.\n\n"
        "TRNASCAN + STRUCTURE EVIDENCE:\n"
        f"- global_context={_format_global_ctx(meta)}\n"
        f"- region_struct_tokens(raw)={region_struct or '(empty)'}\n"
        f"- expected_anticodon={anti or '(unknown)'}\n"
        f"- expected_34_35_36={json.dumps(anti_expected_slots, ensure_ascii=False)}\n"
        f"- current_34_35_36={json.dumps(anti_slots, ensure_ascii=False)}\n"
        f"- canonical_pairs_present={json.dumps(pairs_present, ensure_ascii=False)}\n"
        f"- current_pair_stats={json.dumps(pair_stats, ensure_ascii=False)}\n"
        f"- primary_objective=minimize_unmatched_pairs(current={pair_stats.get('unmatched_pairs', 0)})\n\n"
        "CURRENT ANTICODON BLOCK COLUMNS (left to right):\n"
        f"{json.dumps(rows, ensure_ascii=False)}\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\"target_tokens\": [\"A\", \"-\", \"U\", ...]}\n"
        "or {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}"
    )

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _llm_propose_target(
    nums: List[str],
    templ: List[str],
    targ_before: List[str],
    meta: Dict[str, Any],
    cfg: Dict[str, Any],
    attempt_logs: Optional[List[Dict[str, Any]]] = None,
    runtime_values: Optional[Dict[str, Any]] = None,
) -> Optional[List[str]]:
    messages = _build_messages(nums, templ, targ_before, meta, cfg)
    expected_len = len(targ_before)
    baseline_score, _ = _score_anticodon_block(nums, targ_before, meta)
    baseline_pairs = _pair_stats(nums, targ_before)
    template_stats = _template_similarity_stats(templ, targ_before)
    anticodon_window_stats = _anticodon_window_template_stats(nums, templ, targ_before)
    expected_slots = _trnascan_anticodon_slots(meta)
    det_candidate: Optional[List[str]] = None
    det_trace: Dict[str, Any] = {}
    det_improved = False
    det_hard_ok = False
    det_hard_violations: List[str] = []

    if attempt_logs is not None:
        attempt_logs.append(
            {
                "attempt": "template_similarity",
                "status": "ready",
                "template_similarity": template_stats,
            }
        )

    # Deterministic local search runs first.
    # If it already satisfies hard constraints and improves score, accept directly.
    try:
        det_try = _deterministic_local_search_candidate(
            nums,
            targ_before,
            meta,
            max_steps=6,
            beam_width=24,
            trace_out=det_trace,
        )
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
        det_trace = {}

    if det_try is not None and _order_preserved(targ_before, det_try):
        det_score, _ = _score_anticodon_block(nums, det_try, meta)
        det_pairs = _pair_stats(nums, det_try)
        det_improved = _is_improved_candidate(
            baseline_pairs=baseline_pairs,
            baseline_score=baseline_score,
            cand_pairs=det_pairs,
            cand_score=det_score,
        )
        if det_improved:
            det_hard_ok, det_hard_violations = _validate_anticodon_hard_constraints(nums, det_try, meta)
        det_log: Dict[str, Any] = {
            "attempt": "deterministic_search",
            "status": "candidate_ready" if det_improved else "not_improved",
            "baseline_score": baseline_score,
            "candidate_score": det_score,
            "baseline_unmatched_pairs": baseline_pairs["unmatched_pairs"],
            "candidate_unmatched_pairs": det_pairs["unmatched_pairs"],
            "hard_ok": bool(det_hard_ok),
            "trace": det_trace,
        }
        if det_hard_violations:
            det_log["violations"] = det_hard_violations[:12]
        if attempt_logs is not None:
            attempt_logs.append(det_log)
        if det_improved and det_hard_ok:
            return det_try
        if det_improved:
            det_candidate = det_try
            changed = [
                {
                    "id": str(nums[i] or ""),
                    "before": _norm_base(targ_before[i]),
                    "suggested": _norm_base(det_candidate[i]),
                }
                for i in range(len(nums))
                if _norm_base(targ_before[i]) != _norm_base(det_candidate[i])
            ]
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "DETERMINISTIC SEARCH HINT (candidate only, not final):\n"
                        "- This candidate is built by insertion-gap local relocation "
                        "(e.g., 36i1 -> downstream canonical gap).\n"
                        "- Use this candidate if it helps, or return a better one.\n"
                        "- Final objective order: "
                        "1) lower unmatched stem pairs first, "
                        "2) lower total anticodon block score.\n"
                        f"- baseline_pair_stats={json.dumps(baseline_pairs, ensure_ascii=False)}\n"
                        f"- candidate_pair_stats={json.dumps(det_pairs, ensure_ascii=False)}\n"
                        f"- baseline_score={baseline_score}, candidate_score={det_score}\n"
                        f"- candidate_hard_ok={json.dumps(det_hard_ok, ensure_ascii=False)}\n"
                        f"- candidate_hard_violations={json.dumps(det_hard_violations[:12], ensure_ascii=False)}\n"
                        f"- candidate_target_tokens={json.dumps(det_candidate, ensure_ascii=False)}\n"
                        f"- changed_columns={json.dumps(changed, ensure_ascii=False)}"
                    ),
                }
            )
    elif attempt_logs is not None:
        attempt_logs.append(
            {
                "attempt": "deterministic_search",
                "status": "no_candidate",
                "trace": det_trace,
            }
        )

    # For hard cases where deterministic did not directly converge/apply,
    # provide compact deterministic trace to LLM as additional evidence.
    if det_trace and not (det_improved and det_hard_ok):
        trace_for_prompt = {
            "baseline_objective": det_trace.get("baseline_objective"),
            "best_objective": det_trace.get("best_objective"),
            "found_improvement": det_trace.get("found_improvement"),
            "best_hard_ok": det_trace.get("best_hard_ok"),
            "best_hard_violations": det_trace.get("best_hard_violations"),
            "best_changed_columns": det_trace.get("best_changed_columns"),
            "iterations": det_trace.get("iterations"),
        }
        trace_text = _log_trunc(json.dumps(trace_for_prompt, ensure_ascii=False), 3200)
        messages.append(
            {
                "role": "user",
                "content": (
                    "DETERMINISTIC SEARCH TRACE (compact, must treat as evidence):\n"
                    f"{trace_text}\n"
                    "Use this trace to avoid repeated invalid moves. "
                    "Return ONLY JSON target_tokens."
                ),
            }
        )

    start = time.monotonic()
    attempts = 0
    base_max_attempts = int(cfg["max_retries"]) + 1
    hard_bonus_attempts = 1
    hard_failure_seen = False
    last_hard_candidate: Optional[List[str]] = None
    last_hard_violations: List[str] = []

    def _evaluate_candidate(candidate: List[str]) -> Dict[str, Any]:
        nonlocal hard_failure_seen, last_hard_candidate, last_hard_violations

        if not _order_preserved(targ_before, candidate):
            before = "".join(_non_gap_tokens(targ_before))
            after = "".join(_non_gap_tokens(candidate))
            return {
                "accepted": False,
                "status": "order_changed",
                "feedback": (
                    "FEEDBACK: non-gap order changed. "
                    f"before={before} after={after}. "
                    "Regenerate while preserving exact base order."
                ),
            }

        hard_ok, hard_violations = _validate_anticodon_hard_constraints(nums, candidate, meta)
        if not hard_ok:
            hard_failure_seen = True
            last_hard_candidate = list(candidate)
            last_hard_violations = list(hard_violations)
            return {
                "accepted": False,
                "status": "hard_constraint_failed",
                "log_extra": {"violations": hard_violations[:12]},
                "feedback": (
                    "FEEDBACK: hard constraint check failed. Fix only these violations and regenerate JSON.\n"
                    f"- violations={json.dumps(hard_violations, ensure_ascii=False)}\n"
                    f"- failed_candidate_target_tokens={json.dumps(candidate, ensure_ascii=False)}\n"
                    f"- expected_34_35_36={json.dumps(expected_slots, ensure_ascii=False)}\n"
                    f"- anticodon_window_template={json.dumps(anticodon_window_stats, ensure_ascii=False)}\n"
                    "- Keep non-gap base order unchanged and do not edit unrelated columns."
                ),
            }

        cand_score, reasons = _score_anticodon_block(nums, candidate, meta)
        cand_pairs = _pair_stats(nums, candidate)
        log_extra = {
            "candidate_score": cand_score,
            "baseline_score": baseline_score,
            "candidate_unmatched_pairs": cand_pairs["unmatched_pairs"],
            "baseline_unmatched_pairs": baseline_pairs["unmatched_pairs"],
            "score_reasons": reasons[:8],
        }
        improved = _is_improved_candidate(
            baseline_pairs=baseline_pairs,
            baseline_score=baseline_score,
            cand_pairs=cand_pairs,
            cand_score=cand_score,
        )
        if improved:
            return {"accepted": True, "status": "accepted", "log_extra": log_extra}
        return {
            "accepted": False,
            "status": "not_improved",
            "log_extra": log_extra,
            "feedback": (
                "FEEDBACK: your result did not improve the structural score. "
                f"baseline_score={baseline_score}, candidate_score={cand_score}, "
                f"baseline_unmatched_pairs={baseline_pairs['unmatched_pairs']}, "
                f"candidate_unmatched_pairs={cand_pairs['unmatched_pairs']}. "
                "Reduce unmatched stem pairs first, then improve anticodon anchor mismatch. "
                f"template_reliability={template_stats['reliability']} "
                "(low reliability means template should not override structure). "
                f"anticodon_window_invalid_ratio={anticodon_window_stats.get('invalid_ratio', 1.0)}."
            ),
        }

    while attempts < (base_max_attempts + (hard_bonus_attempts if hard_failure_seen else 0)):
        attempts += 1
        result = run_target_candidate_attempt(
            cfg=cfg,
            messages=messages,
            expected_len=expected_len,
            attempt_label=attempts,
            attempt_logs=attempt_logs,
            http_post_json=_http_post_json,
            parse_candidate=_parse_target_tokens,
            evaluate_candidate=_evaluate_candidate,
            parse_feedback_builder=lambda parse_status: (
                f"FEEDBACK: invalid JSON response ({parse_status}). "
                "Return only JSON with key target_tokens and exact required length."
            ),
            log_trunc=_log_trunc,
        )
        if bool(result.get("accepted")):
            return result.get("candidate")
        if bool(result.get("terminal")) or str(result.get("status") or "") == "keep_current":
            return None
        if str(result.get("status") or "") == "http_failed":
            if (time.monotonic() - start) >= float(cfg["hard_budget_s"]):
                return None
            delay = min(float(cfg["backoff_base_s"]) * (2 ** (attempts - 1)), 2.0)
            time.sleep(delay)

    # DeepSeek reasoner may exhaust its final JSON budget; other providers retain
    # their configured model so a retry never crosses provider boundaries.
    if cfg["mode"] == "openai" and "reasoner" in str(cfg.get("model_name") or "").lower():
        result = run_target_candidate_attempt(
            cfg=cfg,
            messages=messages,
            expected_len=expected_len,
            attempt_label="fallback_chat",
            attempt_logs=attempt_logs,
            http_post_json=_http_post_json,
            parse_candidate=_parse_target_tokens,
            evaluate_candidate=_evaluate_candidate,
            parse_feedback_builder=lambda parse_status: (
                f"FEEDBACK: invalid JSON response ({parse_status}). "
                "Return only JSON with key target_tokens and exact required length."
            ),
            log_trunc=_log_trunc,
            model_name=provider_fallback_model(
                cfg,
                deepseek_fallback_model(runtime_values),
            ),
            append_feedback=False,
        )
        if bool(result.get("accepted")):
            return result.get("candidate")
        if bool(result.get("terminal")) or str(result.get("status") or "") == "keep_current":
            return None

    # One last provider-local retry if hard constraints kept failing.
    if (
        cfg["mode"] == "openai"
        and hard_failure_seen
        and last_hard_candidate is not None
        and last_hard_violations
    ):
        fb2_messages = list(messages)
        fb2_messages.append(
            {
                "role": "user",
                "content": (
                    "FINAL FEEDBACK: your previous candidate violated hard constraints.\n"
                    f"- last_violations={json.dumps(last_hard_violations, ensure_ascii=False)}\n"
                    f"- last_candidate_target_tokens={json.dumps(last_hard_candidate, ensure_ascii=False)}\n"
                    "Regenerate now with strict compliance. Return only valid JSON."
                ),
            }
        )
        result = run_target_candidate_attempt(
            cfg=cfg,
            messages=fb2_messages,
            expected_len=expected_len,
            attempt_label="hard_feedback_fallback_chat",
            attempt_logs=attempt_logs,
            http_post_json=_http_post_json,
            parse_candidate=_parse_target_tokens,
            evaluate_candidate=_evaluate_candidate,
            parse_feedback_builder=lambda parse_status: (
                f"FEEDBACK: invalid JSON response ({parse_status}). "
                "Return only JSON with key target_tokens and exact required length."
            ),
            log_trunc=_log_trunc,
            model_name=provider_fallback_model(
                cfg,
                deepseek_fallback_model(runtime_values),
            ),
            append_feedback=False,
        )
        if bool(result.get("accepted")):
            return result.get("candidate")
        if bool(result.get("terminal")) or str(result.get("status") or "") == "keep_current":
            return None

    return None


def trace_anticodon_block(
    block: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
    run_llm: bool = True,
) -> Dict[str, Any]:
    return trace_single_block_postcheck(
        block=block,
        runtime_values=runtime_values,
        run_llm=run_llm,
        load_cfg=_load_cfg,
        prompt_field="anticodon_system_prompt",
        default_prompt=DEFAULT_ANTICODON_SYSTEM_PROMPT,
        norm_base=_norm_base,
        build_messages=_build_messages,
        propose_target=_llm_propose_target,
        build_baseline_payload=lambda nums, templ, targ, meta: {
            "enabled": True,
            "baseline_score": _score_anticodon_block(nums, targ, meta)[0],
            "baseline_reasons": _score_anticodon_block(nums, targ, meta)[1][:12],
            "baseline_pair_stats": _pair_stats(nums, targ),
            "template_similarity": _template_similarity_stats(templ, targ),
            "anticodon_window_template": _anticodon_window_template_stats(nums, templ, targ),
            "expected_34_35_36": _trnascan_anticodon_slots(meta),
        },
        build_success_payload=lambda nums, templ, cand, meta: {
            "candidate_score": _score_anticodon_block(nums, cand, meta)[0],
            "candidate_pair_stats": _pair_stats(nums, cand),
        },
        no_candidate_message="No valid improved target returned.",
        success_message="Anticodon prompt replay succeeded.",
    )


def postcheck_anticodon(
    blk: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_single_block_postcheck(
        block=blk,
        runtime_values=runtime_values,
        load_cfg=_load_cfg,
        log_runtime_cfg=_log_runtime_cfg,
        prompt_field="anticodon_system_prompt",
        default_prompt=DEFAULT_ANTICODON_SYSTEM_PROMPT,
        norm_base=_norm_base,
        build_messages=_build_messages,
        propose_target=_llm_propose_target,
        build_success_payload=lambda nums, templ, cand, meta: {
            "candidate_score": _score_anticodon_block(nums, cand, meta)[0],
            "candidate_pair_stats": _pair_stats(nums, cand),
        },
        no_candidate_message="No valid improved target returned.",
        success_message="Anticodon prompt replay succeeded.",
        apply_success_message="Applied improved TARGET from LLM.",
        region_label="Anticodon",
    )
