# -*- coding: utf-8 -*-
"""
T loop + T stem postcheck.

Goal:
- Keep numbering/template lines unchanged.
- Let LLM propose a new TARGET token list for this block only.
- Enforce hard constraints (length, token set, base-order preservation).
- Accept only if T-stem objective improves.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .variable_loop import (
    _http_post_json,
    _load_runtime_cfg,
    _log_runtime_cfg,
    _log_trunc,
)
from .common import (
    run_single_block_postcheck,
    run_standard_target_candidate_loop,
    trace_single_block_postcheck,
)
from ..runtime_helpers import deepseek_fallback_model, runtime_prompt_value

DEFAULT_T_SYSTEM_PROMPT = (
    "You are a strict RNA alignment post-editor for T loop + T stem.\n"
    "Task: only rewrite TARGET tokens for this block.\n"
    "Hard rules:\n"
    "1) Never change numbering/template columns.\n"
    "2) Return exactly the same number of TARGET tokens.\n"
    "3) Preserve exact left-to-right order of all non-gap bases (no base loss, no reordering).\n"
    "4) Prioritize canonical T-stem pairing by Sprinzl pairs: "
    "49-65, 50-64, 51-63, 52-62, 53-61.\n"
    "5) Minimize unmatched T-stem pairs (gap pairs + non-canonical pairs).\n"
    "6) Template bases are soft prior only; if template reliability is low, prioritize structure.\n"
    "7) Use only A/U/G/C/N or '-'.\n"
    "8) If the current block cannot be STRICTLY improved, return a no-change decision instead of fabricating edits.\n"
    "Return ONLY JSON in one of these forms:\n"
    "{\"target_tokens\": [\"A\", \"-\", ...]}\n"
    "or {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}."
)

_VALID_BASES = {"A", "U", "G", "C", "N", "-"}
_CANONICAL_PAIRS = {"AU", "UA", "GC", "CG", "GU", "UG"}
_STEM_PAIR_LABELS: List[Tuple[str, str]] = [
    ("49", "65"),
    ("50", "64"),
    ("51", "63"),
    ("52", "62"),
    ("53", "61"),
]
_STEM_BASE_LABELS = {x for pair in _STEM_PAIR_LABELS for x in pair}
_TEMPLATE_RELIABILITY_LOW_OVERLAP = 4
_TEMPLATE_RELIABILITY_LOW_IDENTITY = 0.45
_TEMPLATE_RELIABILITY_MED_IDENTITY = 0.70

def _load_cfg(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _load_runtime_cfg(runtime_values)
    cfg["t_system_prompt"] = runtime_prompt_value("LLM_T_SYSTEM_PROMPT", runtime_values)
    return cfg


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


def _stem_pairs_present(nums: List[str]) -> List[Tuple[str, str]]:
    idx = _build_idx(nums)
    return [(l, r) for l, r in _STEM_PAIR_LABELS if l in idx and r in idx]


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


def _score_t_block(nums: List[str], targ: List[str]) -> Tuple[int, List[str]]:
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

    for label, base in zip(nums, targ):
        sid = str(label or "")
        if "i" not in sid:
            continue
        parent = sid.split("i", 1)[0]
        if parent in _STEM_BASE_LABELS and _norm_base(base) != "-":
            score += 2
            reasons.append(f"stem_insertion:{sid}={_norm_base(base)}")

    return score, reasons


def _objective_tuple(nums: List[str], targ: List[str]) -> Tuple[int, int, int]:
    pairs = _pair_stats(nums, targ)
    score, _ = _score_t_block(nums, targ)
    ins_pen = _stem_insertion_count(nums, targ)
    return (pairs["unmatched_pairs"], ins_pen, score)


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


def _deterministic_local_search_candidate(
    nums: List[str],
    targ_before: List[str],
    max_steps: int = 4,
    beam_width: int = 24,
) -> Optional[List[str]]:
    """
    Deterministic search for insertion-vs-gap misplacement in T region:
    - move occupancy from insertion columns to canonical gap columns,
    - preserve non-gap base order by rebuilding from original sequence.
    """
    base_targ = [_norm_base(x) for x in targ_before]
    base_obj = _objective_tuple(nums, base_targ)
    n = len(nums)
    if n == 0:
        return None

    mask0 = tuple(b != "-" for b in base_targ)
    best_mask = mask0
    best_targ = base_targ
    best_obj = base_obj
    visited = {mask0}
    beam: List[Tuple[Tuple[bool, ...], Tuple[int, int, int], List[str]]] = [(mask0, base_obj, base_targ)]

    for _ in range(max_steps):
        cand_pool: List[Tuple[Tuple[int, int, int], Tuple[bool, ...], List[str]]] = []
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
                if (not mask[j]) and (str(nums[j] or "") in _STEM_BASE_LABELS)
            ]
            donor_soft = [
                i for i in range(n)
                if mask[i]
                and (str(nums[i] or "") not in _STEM_BASE_LABELS)
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
                cand_obj = _objective_tuple(nums, cand_targ)
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
        "empty_regions",
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

    sys_prompt = str(cfg.get("t_system_prompt") or DEFAULT_T_SYSTEM_PROMPT).strip()
    user_prompt = (
        "PROJECT BACKGROUND:\n"
        "- This project aligns tRNA sequences to Sprinzl numbering with structure awareness.\n"
        "- We are editing ONLY the T loop + T stem block.\n"
        "- Never edit outside this block.\n\n"
        "HARD ACCEPTANCE RULES:\n"
        "1) Return EXACTLY same number of tokens as current target block.\n"
        "2) Allowed tokens: A/U/G/C/N/- only.\n"
        "3) Preserve exact order of non-gap bases from current block.\n"
        "4) Prefer canonical T-stem pairing at 49-65, 50-64, 51-63, 52-62, 53-61.\n"
        "5) Minimize unmatched T-stem pairs (gap pairs + non-canonical pairs).\n"
        "6) Template is soft prior only; if template reliability is low, prioritize structure first.\n\n"
        "NO-CHANGE PROTOCOL:\n"
        "- If the current block cannot be STRICTLY improved under the objective,\n"
        "  return {\"decision\": \"keep_current\", \"reason\": \"brief reason\"}.\n"
        "- Do not invent edits only to make the output look different.\n\n"
        "TEMPLATE RELIABILITY:\n"
        f"- template_similarity={json.dumps(template_stats, ensure_ascii=False)}\n\n"
        "STRUCTURE EVIDENCE:\n"
        f"- global_context={_format_global_ctx(meta)}\n"
        f"- region_struct_tokens(raw)={region_struct or '(empty)'}\n"
        f"- canonical_pairs_present={json.dumps(pairs_present, ensure_ascii=False)}\n"
        f"- current_pair_stats={json.dumps(pair_stats, ensure_ascii=False)}\n"
        f"- primary_objective=minimize_unmatched_pairs(current={pair_stats.get('unmatched_pairs', 0)})\n\n"
        "CURRENT T BLOCK COLUMNS (left to right):\n"
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
    baseline_score, _ = _score_t_block(nums, targ_before)
    baseline_pairs = _pair_stats(nums, targ_before)
    template_stats = _template_similarity_stats(templ, targ_before)
    det_candidate: Optional[List[str]] = None

    if attempt_logs is not None:
        attempt_logs.append(
            {
                "attempt": "template_similarity",
                "status": "ready",
                "template_similarity": template_stats,
            }
        )

    try:
        det_try = _deterministic_local_search_candidate(nums, targ_before)
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

    if det_try is not None and _order_preserved(targ_before, det_try):
        det_score, _ = _score_t_block(nums, det_try)
        det_pairs = _pair_stats(nums, det_try)
        det_improved = _is_improved_candidate(
            baseline_pairs=baseline_pairs,
            baseline_score=baseline_score,
            cand_pairs=det_pairs,
            cand_score=det_score,
        )
        det_log: Dict[str, Any] = {
            "attempt": "deterministic_search",
            "status": "candidate_ready" if det_improved else "not_improved",
            "baseline_score": baseline_score,
            "candidate_score": det_score,
            "baseline_unmatched_pairs": baseline_pairs["unmatched_pairs"],
            "candidate_unmatched_pairs": det_pairs["unmatched_pairs"],
        }
        if attempt_logs is not None:
            attempt_logs.append(det_log)
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
                        "- This candidate is built by insertion-gap local relocation.\n"
                        "- Use this candidate if it helps, or return a better one.\n"
                        "- Final objective order: "
                        "1) lower unmatched T-stem pairs first, "
                        "2) lower total T-block score.\n"
                        f"- baseline_pair_stats={json.dumps(baseline_pairs, ensure_ascii=False)}\n"
                        f"- candidate_pair_stats={json.dumps(det_pairs, ensure_ascii=False)}\n"
                        f"- baseline_score={baseline_score}, candidate_score={det_score}\n"
                        f"- candidate_target_tokens={json.dumps(det_candidate, ensure_ascii=False)}\n"
                        f"- changed_columns={json.dumps(changed, ensure_ascii=False)}"
                    ),
                }
            )
    elif attempt_logs is not None:
        attempt_logs.append({"attempt": "deterministic_search", "status": "no_candidate"})

    def _evaluate_candidate(candidate: List[str]) -> Dict[str, Any]:
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

        cand_score, reasons = _score_t_block(nums, candidate)
        cand_pairs = _pair_stats(nums, candidate)
        improved = _is_improved_candidate(
            baseline_pairs=baseline_pairs,
            baseline_score=baseline_score,
            cand_pairs=cand_pairs,
            cand_score=cand_score,
        )
        log_extra = {
            "candidate_score": cand_score,
            "baseline_score": baseline_score,
            "candidate_unmatched_pairs": cand_pairs["unmatched_pairs"],
            "baseline_unmatched_pairs": baseline_pairs["unmatched_pairs"],
            "score_reasons": reasons[:8],
        }
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
                "Reduce unmatched T-stem pairs first. "
                f"template_reliability={template_stats['reliability']} "
                "(low reliability means template should not override structure)."
            ),
        }

    return run_standard_target_candidate_loop(
        cfg=cfg,
        runtime_values=runtime_values,
        messages=messages,
        expected_len=expected_len,
        attempt_logs=attempt_logs,
        http_post_json=_http_post_json,
        parse_candidate=_parse_target_tokens,
        evaluate_candidate=_evaluate_candidate,
        parse_feedback_builder=lambda parse_status: (
            f"FEEDBACK: invalid JSON response ({parse_status}). "
            "Return only JSON with key target_tokens and exact required length."
        ),
        log_trunc=_log_trunc,
        fallback_model_resolver=deepseek_fallback_model,
    )


def trace_t_region_block(
    block: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
    run_llm: bool = True,
) -> Dict[str, Any]:
    return trace_single_block_postcheck(
        block=block,
        runtime_values=runtime_values,
        run_llm=run_llm,
        load_cfg=_load_cfg,
        prompt_field="t_system_prompt",
        default_prompt=DEFAULT_T_SYSTEM_PROMPT,
        norm_base=_norm_base,
        build_messages=_build_messages,
        propose_target=_llm_propose_target,
        build_baseline_payload=lambda nums, templ, targ, meta: {
            "baseline_score": _score_t_block(nums, targ)[0],
            "baseline_reasons": _score_t_block(nums, targ)[1][:12],
            "baseline_pair_stats": _pair_stats(nums, targ),
            "template_similarity": _template_similarity_stats(templ, targ),
        },
        build_success_payload=lambda nums, templ, cand, meta: {
            "candidate_score": _score_t_block(nums, cand)[0],
            "candidate_pair_stats": _pair_stats(nums, cand),
        },
        no_candidate_message="No valid improved target returned.",
        success_message="T-region prompt replay succeeded.",
    )


def postcheck_t_region(
    blk: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_single_block_postcheck(
        block=blk,
        runtime_values=runtime_values,
        load_cfg=_load_cfg,
        log_runtime_cfg=_log_runtime_cfg,
        prompt_field="t_system_prompt",
        default_prompt=DEFAULT_T_SYSTEM_PROMPT,
        norm_base=_norm_base,
        build_messages=_build_messages,
        propose_target=_llm_propose_target,
        build_success_payload=lambda nums, templ, cand, meta: {
            "candidate_score": _score_t_block(nums, cand)[0],
            "candidate_pair_stats": _pair_stats(nums, cand),
        },
        no_candidate_message="No valid improved target returned.",
        success_message="T-region prompt replay succeeded.",
        apply_success_message="Applied improved TARGET from LLM.",
        region_label="T region",
    )
