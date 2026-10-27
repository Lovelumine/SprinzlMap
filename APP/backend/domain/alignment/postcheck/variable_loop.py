# -*- coding: utf-8 -*-
# ==============================================
# File: pythonscript/postcheck/variable_loop.py
# ----------------------------------------------
# Variable loop/arm post-check (TARGET-ONLY over variable block):
# - NEVER move columns; frontend order is fixed.
# - KEEP LINE1 (labels/numbering) and LINE2 (template) EXACTLY as-is.
# - ONLY rewrite LINE3 (target) within the VARIABLE BLOCK:
#     * Preserve the exact left-to-right order of all non-gap bases
#       (same sequence, same multiplicity, same order).
#     * Prefer standard Sprinzl positions before unsupported V-loop occupancy,
#       while assigning the 46-48 bridge by the canonical 48; 46/48;
#       46/47/48 occupancy rule.
#       Exception: if structure explicitly supports a long variable arm,
#       V1..V5 may remain occupied even when 44 or 48 is '-'.
#     * If the variable-region structure predicts ZERO pairs, treat the block as
#       unpaired first:
#         - fill standard sites in revised occupancy order 44, 48, 46, 47, 45
#         - exhaust V1..V5 before using unsupported stem-extension labels
#         - if unsupported stem-extension labels are still needed, expand from
#           fixed mirror shells k=1 outward and keep the two arm lengths as
#           balanced as possible (for example V11/V21, then V12/V22)
#     * Stem mirror rule:
#         - If BOTH labels {V1k, V2k} exist, they remain a fixed mirror pair.
#           One side may carry a base while the other side is '-'.
#         - If ONLY ONE side label exists (mate label missing), that side MUST be '-'.
#     * Loop labels (V1..V5) take at most one base each (or '-').
# - LLM first: propose "var_target" (variable-block tokens). Validate strictly.
#   On ANY violation, feed explicit FEEDBACK and retry.
# - If LLM fails, use a deterministic allocator (order-preserving):
#   * For an unpaired short region, consume 44, 48, 46, 47, 45 in that order.
#     Exception: if structure hints a long variable arm, reserve enough tokens for the hinted V-stem pairs
#     and allow V1..V5 occupancy.
#   * If structure hint is exactly 0 pairs, fill the canonical standard positions first, then V1..V5,
#     then only use balanced mirror-shell stem extension from k=1 outward.
#   * Compute x pairs and y extra-loop columns so 2*x + y == remaining tokens,
#     y <= extra-loop capacity, x <= #available pairs, and x as large as capacity allows.
#   * Build the exact ENABLED column set = {standard core labels} ∪ {both ends of the chosen x pairs} ∪ {y extra-loop columns}.
#   * Single left-to-right pass: for each ENABLED column, consume the next token and write it;
#     all other variable columns write '-'. This guarantees order-preserving.
# ==============================================

from __future__ import annotations
import json
import re
import time
import traceback
from typing import List, Dict, Any, Optional, Tuple

import requests
from requests.exceptions import RequestException, Timeout, SSLError, ConnectionError, ReadTimeout

from backend.infra.llm.provider_config import (
    apply_openai_provider_extras,
    load_provider_runtime_config,
    openai_chat_completions_url as _openai_chat_completions_url,
    openai_temperature as _openai_temperature,
)
from backend.infra.llm.usage_tracker import record_llm_usage

try:
    from backend.infra.config.runtime_settings import (
        DEFAULT_VARLOOP_SYSTEM_PROMPT,
    )
except Exception:  # pragma: no cover
    DEFAULT_VARLOOP_SYSTEM_PROMPT = (
        "You are a strict RNA alignment post-editor.\n"
        "CRITICAL RULES:\n"
        "1) NEVER move columns; NEVER change numbering (LINE1) or template (LINE2).\n"
        "2) ONLY produce a new TARGET token list for the VARIABLE BLOCK.\n"
        "3) Preserve the EXACT left-to-right order of ALL non-gap bases from the original variable block.\n"
        "4) Standard Sprinzl variable-loop labels have priority over unsupported V-loop labels.\n"
        "   Assign residues between the variable arm and T stem in canonical order: one residue uses 48; two use 46/48; three use 46/47/48.\n"
        "   If V1..V5 carries bases while 44-48 still has gaps, that is allowed ONLY when structure supports a long variable arm.\n"
        "   If the structure pair hint is 0, fill an unpaired short region in occupancy order 44, 48, 46, 47, 45; then exhaust V1..V5, then use variable-stem labels only as a balanced mirror-shell extension from k=1 outward (for example V11/V21 before V12/V22).\n"
        "5) Stem mirror rule:\n"
        "   - If BOTH labels V1k and V2k exist, keep the fixed mirror correspondence. One side may be a base while the other side is '-'.\n"
        "   - If ONLY ONE side label exists (mate missing), that side MUST be '-' (do not place a base there).\n"
        "6) Loop labels (V1..V5) take at most one base each; otherwise '-'.\n"
        "7) Use only A/U/G/C/N or '-' (dash). Return ONLY JSON with key 'var_target' (array of strings)."
    )
from ..runtime_helpers import (
    deepseek_fallback_model,
    load_runtime_values,
    runtime_int,
    runtime_prompt_value,
)
from .common import (
    _norm_base,
    build_canonical_trace_fields,
    block_with_runtime_trace,
    build_runtime_trace,
    extract_single_block_lines,
    finalized_runtime_trace,
    invalid_trace_preview,
    provider_fallback_model,
)
from ..variable_boundary import (
    assess_variable_bridge,
    canonical_bridge_labels,
    preferred_standard_variable_labels,
)

DEFAULT_MAX_LOG_CHARS = 4000


def _extract_reasoning_details_text(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    details = obj.get("reasoning_details")
    if not isinstance(details, list):
        return ""
    parts: List[str] = []
    for item in details:
        if isinstance(item, dict) and item.get("text"):
            parts.append(str(item.get("text")))
    return "".join(parts)


def _extract_openai_message_content(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    content = obj.get("content")
    if content:
        return str(content).strip()
    reasoning = obj.get("reasoning_content")
    if reasoning:
        return str(reasoning).strip()
    details = _extract_reasoning_details_text(obj)
    return details.strip()

def _load_runtime_cfg(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    runtime_values = load_runtime_values(runtime_values)
    cfg = load_provider_runtime_config(runtime_values, include_postcheck=True, include_max_tokens=True)
    cfg.update({
        "varloop_system_prompt": runtime_prompt_value("LLM_VARLOOP_SYSTEM_PROMPT", runtime_values),
        "varloop_user_prompt_suffix": runtime_prompt_value("LLM_VARLOOP_USER_PROMPT_SUFFIX", runtime_values),
        "max_log_chars": runtime_int("LLM_MAX_LOG_CHARS", DEFAULT_MAX_LOG_CHARS, 200, 50000, runtime_values=runtime_values),
        "usage_run_id": runtime_values.get("LLM_USAGE_RUN_ID", ""),
        "usage_case_id": runtime_values.get("LLM_USAGE_CASE_ID", ""),
    })
    return cfg


_LAST_CFG_FINGERPRINT: Optional[Tuple[Any, ...]] = None


def _log_runtime_cfg(cfg: Dict[str, Any]) -> None:
    global _LAST_CFG_FINGERPRINT
    fp = (
        cfg["provider"],
        cfg["base_url"],
        cfg["model_name"],
        cfg["postcheck_on"],
        cfg["verify_ssl"],
        cfg["streaming"],
        cfg["timeout_connect"],
        cfg["timeout_read"],
        cfg["hard_budget_s"],
        cfg["max_tokens"],
        cfg["max_retries"],
        cfg["backoff_base_s"],
    )
    if fp == _LAST_CFG_FINGERPRINT:
        return
    _LAST_CFG_FINGERPRINT = fp
    print(
        f"[POSTCHECK][CFG] provider={cfg['provider']} base={cfg['base_url']} model={cfg['model_name']} "
        f"connect={cfg['timeout_connect']}s read={cfg['timeout_read']}s budget={cfg['hard_budget_s']}s "
        f"max_tokens={cfg['max_tokens']} verify_ssl={cfg['verify_ssl']} stream={cfg['streaming']} postcheck_on={cfg['postcheck_on']} "
        f"retries={cfg['max_retries']}"
    )


# ===== Logging helper =====
def _log_trunc(s: str, limit: int = DEFAULT_MAX_LOG_CHARS) -> str:
    if s is None:
        return ""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated {len(s)-limit} chars]"

# -----------------------------
# Variable-label helpers
# -----------------------------
_RE_VAR_5 = re.compile(r'^V1([1-7])$')   # V11..V17 (5' stem)
_RE_VAR_L = re.compile(r'^V([1-5])$')    # V1..V5   (loop)
_RE_VAR_3 = re.compile(r'^V2([1-7])$')   # V21..V27 (3' stem)
_CORE_VAR_LABELS = {"44", "45", "46", "47", "48"}

def _classify_var_position(num: str) -> Optional[str]:
    """Return variable-block position type (None for non-variable)."""
    if not isinstance(num, str):
        return None
    if num in _CORE_VAR_LABELS:
        return "core_loop"
    if _RE_VAR_5.match(num): return "stem5"
    if _RE_VAR_L.match(num): return "loop"
    if _RE_VAR_3.match(num): return "stem3"
    return None

def _var_indices(nums: List[str]) -> List[int]:
    return [i for i, n in enumerate(nums) if _classify_var_position(n or "") is not None]

def _stem_k_from_label(lbl: str) -> Optional[int]:
    m5 = _RE_VAR_5.match(lbl)
    if m5: return int(m5.group(1))
    m3 = _RE_VAR_3.match(lbl)
    if m3: return int(m3.group(1))
    return None

def _pairs_map(nums: List[str]) -> Dict[int, Tuple[int, int]]:
    """
    Return {k: (i_left, i_right)} for every k where BOTH V1k and V2k exist.
    i_left < i_right are absolute column indices.
    """
    pos = {}
    for i, n in enumerate(nums):
        k = _stem_k_from_label(n or "")
        if k is None:
            continue
        if n.startswith("V1"):
            pos.setdefault(k, {})["L"] = i
        elif n.startswith("V2"):
            pos.setdefault(k, {})["R"] = i
    pairs = {}
    for k, d in pos.items():
        if "L" in d and "R" in d:
            iL, iR = d["L"], d["R"]
            pairs[k] = (iL, iR) if iL < iR else (iR, iL)
    return pairs


def _var_pair_maps(nums: List[str]) -> Tuple[Dict[int, int], Dict[int, int]]:
    left: Dict[int, int] = {}
    right: Dict[int, int] = {}
    for i, label in enumerate(nums):
        m5 = _RE_VAR_5.match(str(label or ""))
        if m5:
            left[int(m5.group(1))] = i
            continue
        m3 = _RE_VAR_3.match(str(label or ""))
        if m3:
            right[int(m3.group(1))] = i
    return left, right

def _loop_indices(nums: List[str]) -> List[int]:
    return [i for i, n in enumerate(nums) if _classify_var_position(n or "") == "loop"]

def _core_loop_indices(nums: List[str]) -> List[int]:
    return [i for i, n in enumerate(nums) if _classify_var_position(n or "") == "core_loop"]


def _preferred_core_indices(nums: List[str], residue_count: int) -> Optional[List[int]]:
    labels = preferred_standard_variable_labels(residue_count, nums)
    if labels is None:
        return None
    index_by_label = {str(label or ""): index for index, label in enumerate(nums)}
    if any(label not in index_by_label for label in labels):
        return None
    return [index_by_label[label] for label in labels]


def _check_sprinzl_bridge_occupancy(
    nums: List[str],
    targ: List[str],
) -> Tuple[bool, str]:
    evidence = assess_variable_bridge(
        nums,
        targ,
        require_t_stem_anchor=False,
    )
    if str(evidence.get("status") or "") != "conflict":
        return True, ""
    return (
        False,
        "Sprinzl variable-to-T bridge occupancy mismatch: "
        f"observed={','.join(evidence.get('observed_labels') or []) or '(empty)'} "
        f"expected={','.join(evidence.get('expected_labels') or []) or '(empty)'}",
    )


def _singletons_indices(nums: List[str]) -> List[int]:
    """
    Indices of stem labels where ONLY ONE side exists (mate label missing).
    These MUST be '-' by rule.
    """
    seenL: Dict[int, int] = {}
    seenR: Dict[int, int] = {}
    for i, n in enumerate(nums):
        k = _stem_k_from_label(n or "")
        if k is None:
            continue
        if n.startswith("V1"): seenL[k] = i
        elif n.startswith("V2"): seenR[k] = i
    singles = []
    for k, iL in seenL.items():
        if k not in seenR:
            singles.append(iL)
    for k, iR in seenR.items():
        if k not in seenL:
            singles.append(iR)
    return sorted(singles)


def _check_core_before_extension(nums: List[str], targ: List[str]) -> Tuple[bool, str]:
    """
    Prefer filling standard Sprinzl labels 44..48 before unsupported V-loop occupancy.
    Long variable arms are allowed to occupy V1..V5 while some 44/48 labels remain '-',
    but only when at least one V11k/V21k stem pair is actually filled.
    """
    core_idx = _core_loop_indices(nums)
    if not core_idx:
        return True, ""

    empty_core = [nums[i] for i in core_idx if targ[i] == "-"]
    if not empty_core:
        return True, ""

    occupied_extra_loops = [
        nums[i]
        for i, label in enumerate(nums)
        if _classify_var_position(str(label or "")) == "loop" and targ[i] != "-"
    ]
    if not occupied_extra_loops:
        return True, ""

    if _count_filled_pairs(nums, targ) > 0:
        return True, ""

    return (
        False,
        "standard variable-loop labels must be filled before unsupported V-loop occupancy: "
        f"empty_core={','.join(empty_core)} occupied_v_loop={','.join(occupied_extra_loops)} filled_v_stem_pairs=0",
    )


def _check_no_pair_left_stem_priority(
    nums: List[str],
    targ: List[str],
    *,
    pair_hint: Optional[int],
) -> Tuple[bool, str]:
    """
    When the variable-region structure predicts zero stem pairs, keep unsupported
    5' variable-stem occupancy behind later unpaired labels. This is the
    strongest priority rule we can enforce without reordering columns.
    """
    if pair_hint != 0:
        return True, ""

    occupied_left_stem = [
        nums[i]
        for i, label in enumerate(nums)
        if _classify_var_position(str(label or "")) == "stem5" and targ[i] != "-"
    ]
    if not occupied_left_stem:
        return True, ""

    later_available = [
        nums[i]
        for i, label in enumerate(nums)
        if (
            _classify_var_position(str(label or "")) in {"loop", "stem3"}
            or str(label or "") in {"46", "47", "48"}
        )
        and targ[i] == "-"
    ]
    if not later_available:
        return True, ""

    return (
        False,
        "unpaired variable-region should postpone 5' variable-stem occupancy until later unpaired labels are exhausted: "
        f"occupied_left_stem={','.join(occupied_left_stem)} later_available={','.join(later_available)}",
    )


def _balanced_unpaired_stem_indices(nums: List[str], stem_token_count: int) -> Optional[List[int]]:
    """
    For pair_hint == 0, occupy unsupported stem-extension labels only as a
    balanced mirror-shell expansion from k=1 outward.

    Examples:
      1 token  -> V11
      2 tokens -> V11, V21
      4 tokens -> V11, V12, V21, V22
    """
    if stem_token_count <= 0:
        return []

    left, right = _var_pair_maps(nums)
    paired_ks = sorted(set(left) & set(right))
    if stem_token_count > len(paired_ks) * 2:
        return None

    left_quota = (stem_token_count + 1) // 2
    right_quota = stem_token_count // 2
    if left_quota > len(paired_ks) or right_quota > len(paired_ks):
        return None

    enabled = [left[k] for k in paired_ks[:left_quota]]
    enabled.extend(right[k] for k in paired_ks[:right_quota])
    return sorted(enabled)


def _check_unpaired_balanced_extension(
    nums: List[str],
    targ: List[str],
    *,
    pair_hint: Optional[int],
) -> Tuple[bool, str]:
    if pair_hint != 0:
        return True, ""

    left, right = _var_pair_maps(nums)
    occupied_left_ks = sorted(k for k, idx in left.items() if targ[idx] != "-")
    occupied_right_ks = sorted(k for k, idx in right.items() if targ[idx] != "-")
    if not occupied_left_ks and not occupied_right_ks:
        return True, ""

    empty_loops = [nums[i] for i in _loop_indices(nums) if targ[i] == "-"]
    if empty_loops:
        return (
            False,
            "unpaired variable-region should exhaust V1..V5 before stem extension: "
            f"empty_loops={','.join(empty_loops)} "
            f"occupied_left={','.join(f'V1{k}' for k in occupied_left_ks) or '(none)'} "
            f"occupied_right={','.join(f'V2{k}' for k in occupied_right_ks) or '(none)'}",
        )

    if len(occupied_left_ks) < len(occupied_right_ks) or (len(occupied_left_ks) - len(occupied_right_ks)) > 1:
        return (
            False,
            "unpaired variable-region stem extension must stay left/right balanced: "
            f"occupied_left={','.join(f'V1{k}' for k in occupied_left_ks) or '(none)'} "
            f"occupied_right={','.join(f'V2{k}' for k in occupied_right_ks) or '(none)'}",
        )

    expected_left = sorted(left)[: len(occupied_left_ks)]
    expected_right = sorted(right)[: len(occupied_right_ks)]
    if occupied_left_ks != expected_left or occupied_right_ks != expected_right:
        return (
            False,
            "unpaired variable-region stem extension must grow from k=1 outward: "
            f"occupied_left={','.join(f'V1{k}' for k in occupied_left_ks) or '(none)'} "
            f"occupied_right={','.join(f'V2{k}' for k in occupied_right_ks) or '(none)'}",
        )

    return True, ""

# -----------------------------
# Validation on target
# -----------------------------
_VALID_BASES = set(list("AUGCN"))

def _check_pairing(nums: List[str], targ: List[str]) -> Tuple[bool, List[str]]:
    """
    Mirror-label rule on stems:
      - If both labels exist: the mirror correspondence is fixed, but one-sided gaps are allowed.
      - If only one label exists: that side MUST be '-'.
    """
    errs = []

    # only one side exists → that side must be '-'
    singles = _singletons_indices(nums)
    for idx in singles:
        if targ[idx] != '-':
            lbl = nums[idx]
            mate = f"V2{_stem_k_from_label(lbl)}" if lbl.startswith("V1") else f"V1{_stem_k_from_label(lbl)}"
            errs.append(f"Singleton stem must be '-': have {lbl}='{targ[idx]}', missing mate {mate}")

    return (len(errs) == 0, errs)

def _non_gap_sequence_in_var(nums: List[str], targ: List[str]) -> List[str]:
    vi = _var_indices(nums)
    return [targ[i] for i in vi if targ[i] != '-']

def _check_order_preserved(nums: List[str], targ_before: List[str], targ_after: List[str]) -> bool:
    before = _non_gap_sequence_in_var(nums, targ_before)
    after  = _non_gap_sequence_in_var(nums, targ_after)
    ok = (before == after)
    if not ok:
        print("[POSTCHECK][CHK] Variable-block token order changed! before:",
              ''.join(before), "after:", ''.join(after))
    return ok

def _check_var_target_shape_and_order(
    var_target: List[str],
    nums: List[str],
    targ_before: List[str],
    *,
    pair_hint: Optional[int] = None,
) -> Tuple[bool, str]:
    vi = _var_indices(nums)
    if not isinstance(var_target, list) or len(var_target) != len(vi):
        return False, f"length mismatch (expected {len(vi)}, got {len(var_target) if isinstance(var_target,list) else 'non-list'})"
    for ch in var_target:
        if ch != '-' and ch.upper() not in _VALID_BASES:
            return False, f"invalid token '{ch}'"

    # order of non-gap tokens must be preserved
    before_bases = [targ_before[i] for i in vi if targ_before[i] != '-']
    after_bases  = [x for x in var_target if x != '-']
    if before_bases != after_bases:
        return False, f"base order mismatch: expected {''.join(before_bases)}, got {''.join(after_bases)}"

    # singleton stems must be '-'
    singles = _singletons_indices(nums)
    for j, i in enumerate(vi):
        if i in singles and var_target[j] != '-':
            return False, f"singleton at index {i} ({nums[i]}) must be '-', got '{var_target[j]}'"

    targ_after = list(targ_before)
    for j, i in enumerate(vi):
        targ_after[i] = str(var_target[j]).upper()
    ok_core, core_err = _check_core_before_extension(nums, targ_after)
    if not ok_core:
        return False, core_err

    ok_unpaired, unpaired_err = _check_unpaired_balanced_extension(nums, targ_after, pair_hint=pair_hint)
    if not ok_unpaired:
        return False, unpaired_err

    ok_bridge, bridge_err = _check_sprinzl_bridge_occupancy(nums, targ_after)
    if not ok_bridge:
        return False, bridge_err

    return True, ""

# -----------------------------
# Structure hint -> pair count
# -----------------------------
def _pair_hint_from_struct(var_struct: Optional[str]) -> Optional[int]:
    """Estimate how many stem pairs the variable arm likely has."""
    if not var_struct:
        return None
    gt = var_struct.count('>')
    lt = var_struct.count('<')
    if gt != lt:
        print(f"[POSTCHECK][HINT] variable struct imbalanced: '>'={gt}, '<'={lt}; ignore hint.")
        return None
    return gt


def _clean_struct_chars(raw_struct: Optional[str]) -> List[str]:
    if not raw_struct:
        return []
    return [ch for ch in str(raw_struct) if not ch.isspace()]


def _expand_struct_to_aligned_target(targ: List[str], raw_struct: Optional[str]) -> Tuple[List[str], str]:
    """
    Map per-base structure chars to aligned target columns.
    - non-gap target col consumes one struct char
    - gap target col gets 'GAP'
    """
    struct_chars = _clean_struct_chars(raw_struct)
    out: List[str] = []
    ptr = 0
    non_gap = 0
    for base in targ:
        if base == "-":
            out.append("GAP")
            continue
        non_gap += 1
        if ptr < len(struct_chars):
            ch = struct_chars[ptr]
            out.append(ch if ch else "UNK")
        else:
            out.append("UNK")
        ptr += 1

    if not struct_chars:
        return out, "empty_struct"
    if len(struct_chars) == non_gap:
        return out, "ok"
    if len(struct_chars) > non_gap:
        return out, f"struct_longer_by_{len(struct_chars) - non_gap}"
    return out, f"struct_shorter_by_{non_gap - len(struct_chars)}"


def _var_struct_tokens(nums: List[str], targ: List[str], raw_struct: Optional[str]) -> Tuple[List[str], str]:
    aligned_struct, note = _expand_struct_to_aligned_target(targ, raw_struct)
    vi = _var_indices(nums)
    return [aligned_struct[i] for i in vi], note

_REGION_ORDER = [
    "Aminoacyl arm 5' end",
    "D loop + D stem",
    "Anticodon loop + Anticodon stem",
    "Variable loop",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_global_struct_summary(summary: Optional[Dict[str, Any]]) -> str:
    if not isinstance(summary, dict) or not summary:
        return "(unavailable)"

    source = str(summary.get("annotation_source", "") or "").strip() or "unknown"
    seg_status = str(summary.get("segmentation_status", "") or "").strip() or "unknown"
    seg_errors = str(summary.get("segmentation_errors", "") or "").strip()
    trnascan_hit = bool(summary.get("trnascan_hit", True))
    empty_regions = [str(x) for x in (summary.get("empty_regions") or []) if str(x).strip()]
    non_var_pairs = _to_int(summary.get("non_variable_pair_count"), 0)

    lines = [
        f"annotation_source={source}",
        f"segmentation_status={seg_status}",
        f"trnascan_hit={'yes' if trnascan_hit else 'no'}",
        f"empty_regions={', '.join(empty_regions) if empty_regions else '(none)'}",
        f"non_variable_pair_count={non_var_pairs}",
    ]
    if seg_errors:
        lines.append(f"segmentation_errors={seg_errors[:200]}")

    stats = summary.get("region_struct_stats")
    if not isinstance(stats, dict):
        lines.append("other_region_stats=(unavailable)")
        return "\n".join(lines)

    lines.append("other_region_stats (seq_len,struct_len,pair_count):")
    for region in _REGION_ORDER:
        if region == "Variable loop":
            continue
        stat = stats.get(region)
        if not isinstance(stat, dict):
            lines.append(f"{region}: (unavailable)")
            continue
        seq_len = _to_int(stat.get("seq_len"), -1)
        struct_len = _to_int(stat.get("struct_len"), -1)
        pair_count = _to_int(stat.get("pair_count"), -1)
        lines.append(f"{region}: ({seq_len},{struct_len},{pair_count})")
    return "\n".join(lines)


def _count_filled_pairs(nums: List[str], targ: List[str]) -> int:
    """Count how many variable-arm stem pairs have bases on both sides."""
    pairs = _pairs_map(nums)
    cnt = 0
    for _, (iL, iR) in pairs.items():
        if targ[iL] != '-' and targ[iR] != '-':
            cnt += 1
    return cnt

# -----------------------------
# Deterministic allocator (order-preserving; enable-set + single pass)
# -----------------------------
def _alloc_deterministic(nums: List[str], targ_before: List[str], pair_hint: Optional[int] = None) -> List[str]:
    """
    Order-preserving deterministic allocator:

      1) Collect tokens T = original non-gap bases in variable block.
      2) For an unpaired short region, fill standard positions in revised
         Sprinzl occupancy order 44, 48, 46, 47, 45.
         Exception: if pair_hint suggests a long variable arm, reserve enough tokens
         for the hinted V-stem pairs and one V-loop token if possible.
      3) Compute pairs (both labels exist) and extra-loop capacity (V1..V5).
      4) Choose x,y so 2*x + y == remaining tokens, with x <= #pairs, y <= extra-loop capacity.
      5) ENABLED columns = the canonical c standard variable labels
                           ∪ both ends of the first x pairs (by left-index order)
                           ∪ first y extra-loop columns (by index).
         NOTE: any singleton stems are NOT enabled (must remain '-').
      6) Single left-to-right pass over all columns:
         if idx ∈ ENABLED → take next token and write; else write '-' for variable columns.
         Non-variable columns unchanged.

      This preserves the non-gap order exactly and prevents orphans.
    """
    vi = _var_indices(nums)
    new_targ = list(targ_before)
    # Zero out variable block
    for i in vi:
        new_targ[i] = '-'

    tokens = _non_gap_sequence_in_var(nums, targ_before)
    T = len(tokens)
    if T == 0:
        return new_targ

    pairs_dict = _pairs_map(nums)  # {k: (iL,iR)}
    pairs_sorted = sorted(pairs_dict.items(), key=lambda kv: kv[1][0])  # by left index
    P = len(pairs_sorted)

    loops_sorted = sorted(_loop_indices(nums))
    L = len(loops_sorted)
    core_sorted = sorted(_core_loop_indices(nums))
    left_stem_sorted = sorted(i for i, label in enumerate(nums) if _classify_var_position(str(label or "")) == "stem5")
    right_stem_sorted = sorted(i for i, label in enumerate(nums) if _classify_var_position(str(label or "")) == "stem3")

    desired_pairs = 0
    reserve_loop = 0
    if pair_hint is not None:
        desired_pairs = min(max(0, int(pair_hint)), P, T // 2)
        if desired_pairs > 0 and L > 0 and (T - 2 * desired_pairs) > 0:
            reserve_loop = 1

    if pair_hint == 0:
        core_count = min(len(core_sorted), T)
        core_enabled = _preferred_core_indices(nums, core_count)
        if core_enabled is None:
            print("[POSTCHECK][FALLBACK] canonical standard-position capacity infeasible; keep var-block as-is.")
            return list(targ_before)
        remaining = T - core_count
        loop_count = min(L, remaining)
        remaining -= loop_count
        stem_enabled = _balanced_unpaired_stem_indices(nums, remaining)
        if stem_enabled is None:
            print("[POSTCHECK][FALLBACK] unpaired variable capacity infeasible; keep var-block as-is.")
            return list(targ_before)

        enabled: set[int] = set()
        for idx in core_enabled:
            enabled.add(idx)
        for idx in loops_sorted[:loop_count]:
            enabled.add(idx)
        for idx in stem_enabled:
            enabled.add(idx)
        for idx in _singletons_indices(nums):
            enabled.discard(idx)

        p = 0
        for idx, lbl in enumerate(nums):
            if _classify_var_position(lbl or "") is None:
                continue
            if idx in enabled and p < T:
                new_targ[idx] = tokens[p]
                p += 1

        if not _check_order_preserved(nums, targ_before, new_targ):
            before = ''.join(_non_gap_sequence_in_var(nums, targ_before))
            after = ''.join(_non_gap_sequence_in_var(nums, new_targ))
            print(f"[POSTCHECK][FALLBACK] unpaired allocator order changed. before={before} after={after}")
            return list(targ_before)
        ok_pair, errs = _check_pairing(nums, new_targ)
        if not ok_pair:
            print("[POSTCHECK][WARN] unpaired allocator pairing failed:", "; ".join(errs))
            return list(targ_before)
        ok_unpaired, unpaired_err = _check_unpaired_balanced_extension(nums, new_targ, pair_hint=pair_hint)
        if not ok_unpaired:
            print("[POSTCHECK][WARN] unpaired allocator balanced-extension check failed:", unpaired_err)
            return list(targ_before)
        ok_bridge, bridge_err = _check_sprinzl_bridge_occupancy(nums, new_targ)
        if not ok_bridge:
            print("[POSTCHECK][WARN] unpaired allocator bridge-occupancy check failed:", bridge_err)
            return list(targ_before)
        return new_targ

    core_budget = max(0, T - 2 * desired_pairs - reserve_loop)
    core_count = min(len(core_sorted), core_budget)
    core_enabled = _preferred_core_indices(nums, core_count)
    if core_enabled is None:
        print("[POSTCHECK][FALLBACK] canonical standard-position capacity infeasible; keep var-block as-is.")
        return list(targ_before)
    remaining = T - core_count

    # choose x,y for extension labels after reserving standard 44..48
    if desired_pairs > 0:
        x = desired_pairs
    else:
        x = min(P, remaining // 2)
    while x >= 0 and (remaining - 2*x) > L:
        x -= 1
    if x < 0:
        # capacity infeasible; safest: keep original var-block
        print("[POSTCHECK][FALLBACK] capacity infeasible; keep var-block as-is.")
        return list(targ_before)

    y = remaining - 2*x  # 0..L

    # Build ENABLED columns
    chosen_pairs = pairs_sorted[:x]
    enabled: set[int] = set()
    for idx in core_enabled:
        enabled.add(idx)
    for _, (iL, iR) in chosen_pairs:
        enabled.add(iL); enabled.add(iR)
    enabled_loops = loops_sorted[:y]
    for idx in enabled_loops:
        enabled.add(idx)

    # Enforce singleton stems MUST be '-' (do not enable them)
    for idx in _singletons_indices(nums):
        if idx in enabled:
            enabled.remove(idx)

    # Fill in order
    p = 0
    for idx, lbl in enumerate(nums):
        if _classify_var_position(lbl or "") is None:
            continue
        if idx in enabled:
            if p < T:
                new_targ[idx] = tokens[p]; p += 1
        # else remains '-'

    # Safety checks
    if not _check_order_preserved(nums, targ_before, new_targ):
        before = ''.join(_non_gap_sequence_in_var(nums, targ_before))
        after  = ''.join(_non_gap_sequence_in_var(nums, new_targ))
        print(f"[POSTCHECK][FATAL] deterministic allocator order changed (must never happen). before={before} after={after}")
        return list(targ_before)

    ok_pair, errs = _check_pairing(nums, new_targ)
    if not ok_pair:
        print("[POSTCHECK][WARN] deterministic allocator pairing failed:", "; ".join(errs))
        return list(targ_before)

    ok_core, core_err = _check_core_before_extension(nums, new_targ)
    if not ok_core:
        print("[POSTCHECK][WARN] deterministic allocator core-priority failed:", core_err)
        return list(targ_before)

    ok_bridge, bridge_err = _check_sprinzl_bridge_occupancy(nums, new_targ)
    if not ok_bridge:
        print("[POSTCHECK][WARN] deterministic allocator bridge-occupancy check failed:", bridge_err)
        return list(targ_before)

    return new_targ


def _topology_guided_variable_label_order(
    nums: List[str],
    raw_struct: Optional[str],
    token_count: int,
) -> Optional[List[str]]:
    """
    Build a fixed-label occupancy order for the variable block using the raw
    structure tokens from segmentation instead of the current DP insertion layout.

    Supported pattern:
      prefix dots + contiguous opens + contiguous middle dots + contiguous closes + suffix dots

    The key constraint is fixed mirror topology:
      V11..V17 pair only with the matching tail slice of V27..V21.
    """
    struct_chars = _clean_struct_chars(raw_struct)
    if token_count <= 0 or len(struct_chars) != token_count:
        return None

    pattern = "".join(struct_chars)
    m = re.fullmatch(r"(\.*)(>+)(\.*)(<+)(\.*)", pattern)
    if not m:
        return None

    prefix_dots, opens, middle_dots, closes, suffix_dots = m.groups()
    if len(opens) != len(closes):
        return None

    prefix_core = [label for label in nums if label in {"44", "45"}]
    left_stem = [label for label in nums if _RE_VAR_5.match(label or "")]
    loop_labels = [label for label in nums if _RE_VAR_L.match(label or "")]
    right_stem = [label for label in nums if _RE_VAR_3.match(label or "")]
    suffix_core = [label for label in nums if label in {"46", "47", "48"}]

    pair_count = len(opens)
    if (
        len(prefix_dots) > len(prefix_core)
        or pair_count > len(left_stem)
        or pair_count > len(right_stem)
        or len(middle_dots) > len(loop_labels)
        or len(suffix_dots) > len(suffix_core)
    ):
        return None

    chosen_right = right_stem[-pair_count:] if pair_count else []
    chosen_suffix = canonical_bridge_labels(len(suffix_dots))
    if chosen_suffix is None or any(label not in suffix_core for label in chosen_suffix):
        return None
    label_order = (
        prefix_core[: len(prefix_dots)]
        + left_stem[:pair_count]
        + loop_labels[: len(middle_dots)]
        + chosen_right
        + list(chosen_suffix)
    )
    if len(label_order) != token_count:
        return None
    return label_order


def rebuild_variable_block_topology(
    nums: List[str],
    templ: List[str],
    targ: List[str],
    raw_struct: Optional[str],
) -> Optional[Tuple[List[str], List[str], List[str]]]:
    """
    Rebuild the variable block against fixed Sprinzl mirror labels.

    This is stronger than TARGET-only postcheck:
    - variable insertion columns are dropped
    - residues are reassigned onto non-insertion labels according to the raw
      variable-region structure pattern
    - final pair topology stays on fixed mirror labels (for example
      V11↔V21, V12↔V22), never on 46/47/48 or insertion labels
    """
    if not nums or not templ or not targ or len(nums) != len(templ) or len(nums) != len(targ):
        return None

    reduced_idx = [i for i, label in enumerate(nums) if "i" not in str(label or "")]
    reduced_nums = [nums[i] for i in reduced_idx]
    reduced_templ = [templ[i] for i in reduced_idx]
    tokens = [_norm_base(base) for base in targ if _norm_base(base) != "-"]
    if not tokens:
        return None

    label_order = _topology_guided_variable_label_order(reduced_nums, raw_struct, len(tokens))
    if not label_order:
        return None

    index_by_label = {label: idx for idx, label in enumerate(reduced_nums)}
    if any(label not in index_by_label for label in label_order):
        return None

    rebuilt_targ = ["-"] * len(reduced_nums)
    for base, label in zip(tokens, label_order):
        rebuilt_targ[index_by_label[label]] = base

    before_seed = ["-"] * len(reduced_nums)
    seed_pos = 0
    for idx in range(len(reduced_nums)):
        if seed_pos >= len(tokens):
            break
        before_seed[idx] = tokens[seed_pos]
        seed_pos += 1

    if not _check_order_preserved(reduced_nums, before_seed, rebuilt_targ):
        return None

    ok_pair, _ = _check_pairing(reduced_nums, rebuilt_targ)
    if not ok_pair:
        return None

    ok_core, _ = _check_core_before_extension(reduced_nums, rebuilt_targ)
    if not ok_core:
        return None

    ok_bridge, _ = _check_sprinzl_bridge_occupancy(reduced_nums, rebuilt_targ)
    if not ok_bridge:
        return None

    return reduced_nums, reduced_templ, rebuilt_targ

# -----------------------------
# LLM proposer with FEEDBACK loop (returns var_target list)
# -----------------------------
def _headers_with_auth(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = cfg.get("api_key") or ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers

def _http_post_json(url: str, payload: dict, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    def _should_abort_non_json_stream(
        content_parts: List[str],
        reasoning_parts: List[str],
        total_chars: int,
    ) -> bool:
        if total_chars < 120:
            return False
        probe = ("".join(content_parts) or "".join(reasoning_parts)).lstrip()
        if not probe:
            return False
        if probe.startswith("{"):
            return False
        if "{" in probe[:240]:
            return False
        compact_probe = probe[:240].replace("\n", " ").strip()
        if any(token in compact_probe for token in ("First", "I need", "Let me", "Sure", "步骤", "分析")):
            return True
        alpha_count = sum(1 for ch in compact_probe if ch.isalpha())
        return alpha_count >= 40

    print("\n[POSTCHECK][HTTP] POST", url)
    headers = _headers_with_auth(cfg)
    session = requests.Session()
    session.trust_env = False
    log_headers = dict(headers)
    if "Authorization" in log_headers:
        log_headers["Authorization"] = "Bearer ****(hidden)****"
    print("[POSTCHECK][HTTP] headers:", log_headers)
    print("[POSTCHECK][HTTP] payload:", _log_trunc(json.dumps(payload, ensure_ascii=False), int(cfg["max_log_chars"])))
    start = time.monotonic()
    stream_opt = payload.get("stream", bool(cfg["streaming"]))
    try:
        resp = session.post(
            url,
            data=json.dumps(payload),
            headers=headers,
            timeout=(float(cfg["timeout_connect"]), float(cfg["timeout_read"])),
            verify=bool(cfg["verify_ssl"]),
            stream=stream_opt
        )
    except (ReadTimeout, Timeout, SSLError, ConnectionError, RequestException) as e:
        print(f"[POSTCHECK][HTTP] request exception after {time.monotonic()-start:.2f}s:", repr(e))
        print(traceback.format_exc())
        return False, ""
    elapsed = time.monotonic() - start
    print(f"[POSTCHECK][HTTP] status: {resp.status_code} (elapsed {elapsed:.2f}s)")
    if resp.status_code != 200 or elapsed > float(cfg["hard_budget_s"]):
        record_llm_usage(
            cfg=cfg,
            source="postcheck",
            url=url,
            payload=payload,
            status_code=resp.status_code,
            elapsed_sec=elapsed,
            response_text="" if stream_opt else (resp.text or ""),
        )
        if not stream_opt:
            print("[POSTCHECK][HTTP] raw text:", _log_trunc(resp.text, int(cfg["max_log_chars"])))
        return False, ""

    if stream_opt:
        tail = ""
        total_chars = 0
        last_log_t = 0.0
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        stream_usage: Optional[Dict[str, Any]] = None
        try:
            for line in resp.iter_lines(decode_unicode=True, chunk_size=1024):
                if not line:
                    continue
                raw_line = line
                if raw_line.startswith("data:"):
                    raw_line = raw_line[len("data:"):].strip()
                if raw_line in ("[DONE]", ""):
                    continue

                # 尝试从流式 JSON 中提取内容片段。
                # DeepSeek reasoner 可能只给 reasoning_content，不给 content。
                piece_content = ""
                piece_reasoning = ""
                try:
                    parsed = json.loads(raw_line)
                    # OpenAI 样式
                    choices = parsed.get("choices")
                    if isinstance(choices, list) and choices:
                        delta = choices[0].get("delta") or choices[0].get("message") or {}
                        piece_content = str(delta.get("content") or "")
                        piece_reasoning = str(delta.get("reasoning_content") or "")
                        if not piece_reasoning:
                            piece_reasoning = _extract_reasoning_details_text(delta)
                    if isinstance(parsed.get("usage"), dict):
                        stream_usage = dict(parsed["usage"])
                    # Ollama 样式
                    if isinstance(parsed.get("message"), dict):
                        piece_content = str(parsed["message"].get("content") or piece_content)
                except Exception:
                    piece_content = ""
                    piece_reasoning = ""

                if piece_content:
                    content_parts.append(piece_content)
                if piece_reasoning:
                    reasoning_parts.append(piece_reasoning)

                piece_for_log = piece_content or piece_reasoning
                if not piece_for_log:
                    continue

                total_chars += len(piece_for_log)
                tail = (tail + piece_for_log)[-120:]
                now_t = time.monotonic()
                if (now_t - last_log_t) >= 0.5:
                    safe_tail = tail.replace("\n", " ")
                    print(
                        f"\r[POSTCHECK][STREAM] chars={total_chars} tail={_log_trunc(safe_tail, 120)}",
                        end="",
                        flush=True,
                    )
                    last_log_t = now_t
                if _should_abort_non_json_stream(content_parts, reasoning_parts, total_chars):
                    probe = ("".join(content_parts) or "".join(reasoning_parts)).lstrip()
                    print(
                        "\n[POSTCHECK][STREAM] abort_non_json_stream:",
                        _log_trunc(probe.replace("\n", " "), 180),
                    )
                    try:
                        resp.close()
                    except Exception:
                        pass
                    return False, ""
            print()  # 换行
        except Exception as e:
            print("\n[POSTCHECK][STREAM] error while reading:", repr(e))
        content = "".join(content_parts).strip()
        if not content:
            content = "".join(reasoning_parts).strip()
        if content:
            print(f"[POSTCHECK][STREAM] done chars={len(content)}")
        record_llm_usage(
            cfg=cfg,
            source="postcheck",
            url=url,
            payload=payload,
            status_code=resp.status_code,
            elapsed_sec=elapsed,
            usage=stream_usage,
        )
        return True, content
    else:
        text = resp.text or ""
        record_llm_usage(
            cfg=cfg,
            source="postcheck",
            url=url,
            payload=payload,
            status_code=resp.status_code,
            elapsed_sec=elapsed,
            response_text=text,
        )
        # 非流模式才打印最终文本，避免重复
        print("[POSTCHECK][HTTP] raw text:", _log_trunc(text, int(cfg["max_log_chars"])))
        return True, text


def _extract_probe_content(mode: str, raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        return text

    if mode == "ollama":
        msg = (data.get("message") or {}).get("content")
        return str(msg or "").strip() or text

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg_obj = choices[0].get("message") or {}
        msg = _extract_openai_message_content(msg_obj)
        return str(msg or "").strip() or text
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


def probe_llm_connection(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _load_runtime_cfg(runtime_values)
    provider = str(cfg.get("provider") or "")
    mode = str(cfg.get("mode") or "")
    base_url = str(cfg.get("base_url") or "")
    model_name = str(cfg.get("model_name") or "")

    if mode == "openai" and not str(cfg.get("api_key") or "").strip():
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "message": "API key is empty for OpenAI-compatible provider.",
        }

    headers = _headers_with_auth(cfg)
    start = time.monotonic()
    session = requests.Session()
    session.trust_env = False
    try:
        if mode == "ollama":
            url = f"{base_url}/api/chat"
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "Reply with PONG only."}],
                "stream": False,
            }
        else:
            url = _openai_chat_completions_url(base_url)
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "Reply with PONG only."}],
                "temperature": _openai_temperature(cfg, 0.0),
                "max_tokens": 16,
                "stream": False,
            }
            apply_openai_provider_extras(payload, cfg, model_name=model_name)
        resp = session.post(
            url,
            data=json.dumps(payload),
            headers=headers,
            timeout=(float(cfg["timeout_connect"]), float(cfg["timeout_read"])),
            verify=bool(cfg["verify_ssl"])
        )
    except (ReadTimeout, Timeout, SSLError, ConnectionError, RequestException) as exc:
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "message": f"LLM request failed: {exc}",
        }

    elapsed_ms = int((time.monotonic() - start) * 1000)
    raw = resp.text or ""
    content = _extract_probe_content(mode, raw)
    if resp.status_code != 200:
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "status_code": resp.status_code,
            "elapsed_ms": elapsed_ms,
            "message": _log_trunc(content or raw, 600),
        }

    return {
        "ok": True,
        "provider": provider,
        "mode": mode,
        "base_url": base_url,
        "model_name": model_name,
        "status_code": resp.status_code,
        "elapsed_ms": elapsed_ms,
        "message": _log_trunc(content or "LLM reachable.", 600),
    }


def _varloop_probe_case() -> Tuple[List[str], List[str], Optional[int], List[str], Dict[str, Any]]:
    """
    Build a compact synthetic variable-loop case for prompt verification.
    The case is intentionally deterministic and independent from user data.
    """
    nums = ["44", "45", "46", "V11", "V12", "V1", "V2", "V21", "V22"]
    targ_before = ["A", "U", "G", "C", "U", "A", "-", "-", "-"]
    pair_hint = 2
    var_struct_tokens = [".", ".", ".", ">", ">", ".", ".", "<", "<"]
    global_struct_summary: Dict[str, Any] = {
        "annotation_source": "probe_case",
        "segmentation_status": "ok",
        "segmentation_errors": "",
        "trnascan_hit": True,
        "empty_regions": [],
        "non_variable_pair_count": 9,
        "region_struct_stats": {
            "Aminoacyl arm 5' end": {"seq_len": 7, "struct_len": 7, "pair_count": 3},
            "D loop + D stem": {"seq_len": 10, "struct_len": 10, "pair_count": 3},
            "Anticodon loop + Anticodon stem": {"seq_len": 15, "struct_len": 15, "pair_count": 5},
            "Variable loop": {"seq_len": 9, "struct_len": 9, "pair_count": 2},
            "T loop + T stem": {"seq_len": 10, "struct_len": 10, "pair_count": 3},
            "Aminoacyl arm 3' end": {"seq_len": 7, "struct_len": 7, "pair_count": 3},
        },
    }
    return nums, targ_before, pair_hint, var_struct_tokens, global_struct_summary


def probe_varloop_prompt(runtime_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    End-to-end probe for variable-loop prompt:
    - Build final system/user messages with current runtime settings.
    - Execute one synthetic variable-loop postcheck call.
    """
    cfg = _load_runtime_cfg(runtime_values)
    provider = str(cfg.get("provider") or "")
    mode = str(cfg.get("mode") or "")
    base_url = str(cfg.get("base_url") or "")
    model_name = str(cfg.get("model_name") or "")

    if not bool(cfg.get("postcheck_on")):
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "message": "LLM_POSTCHECK is disabled.",
        }
    if not str(cfg.get("varloop_system_prompt") or "").strip():
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "message": "LLM_VARLOOP_SYSTEM_PROMPT is empty.",
        }

    nums, targ_before, pair_hint, var_struct_tokens, global_struct_summary = _varloop_probe_case()
    messages = _build_llm_messages(
        nums, targ_before, pair_hint, var_struct_tokens, global_struct_summary, cfg
    )
    start = time.monotonic()
    var_target = _llm_propose_var_target_with_feedback(
        nums,
        targ_before,
        pair_hint,
        var_struct_tokens,
        global_struct_summary,
        cfg,
        runtime_values=runtime_values,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    user_prompt = str(messages[1].get("content") or "")
    if var_target is None:
        return {
            "ok": False,
            "provider": provider,
            "mode": mode,
            "base_url": base_url,
            "model_name": model_name,
            "elapsed_ms": elapsed_ms,
            "message": "Prompt test failed: no valid var_target returned.",
            "prompt_preview": _log_trunc(user_prompt, 1200),
        }
    return {
        "ok": True,
        "provider": provider,
        "mode": mode,
        "base_url": base_url,
        "model_name": model_name,
        "elapsed_ms": elapsed_ms,
        "message": "Prompt test succeeded.",
        "var_target": var_target,
        "prompt_preview": _log_trunc(user_prompt, 1200),
    }

def _build_llm_messages(
    nums: List[str],
    targ_before: List[str],
    pair_hint: Optional[int],
    var_struct_tokens: Optional[List[str]],
    global_struct_summary: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, str]]:
    vi = _var_indices(nums)
    var_labels = [nums[i] for i in vi]
    orig_bases = [targ_before[i] for i in vi if targ_before[i] != '-']

    pairs = _pairs_map(nums)
    pairs_lr = [f"V1{k}↔V2{k}" for k, _ in sorted(pairs.items(), key=lambda kv: kv[1][0])]
    core_labels = [nums[i] for i in sorted(_core_loop_indices(nums))]
    loops = [nums[i] for i in sorted(_loop_indices(nums))]
    singles_idx = _singletons_indices(nums)
    singles_desc = [f"{nums[i]}(idx={i})" for i in singles_idx]
    hint_line = str(pair_hint) if pair_hint is not None else "(no hint)"
    struct_line = " ".join(var_struct_tokens or []) if var_struct_tokens else "(unavailable)"
    global_ctx_line = _format_global_struct_summary(global_struct_summary)

    sys_prompt = str(cfg.get("varloop_system_prompt") or DEFAULT_VARLOOP_SYSTEM_PROMPT).strip()
    user_prompt = (
        "VARIABLE LABELS (left-to-right; fixed):\n"
        f"{' '.join(var_labels)}\n\n"
        "ORIGINAL NON-GAP BASES in this variable block (order to preserve):\n"
        f"{' '.join(orig_bases) if orig_bases else '(empty)'}\n\n"
        "STANDARD CORE LABELS (preferred before unsupported V-loop occupancy):\n"
        f"{' '.join(core_labels) if core_labels else '(none)'}\n"
        "PRESENT STEM MIRROR PAIRS (fixed mirror labels; do not remap across numbers):\n"
        f"{', '.join(pairs_lr) if pairs_lr else '(none)'}\n"
        "PRESENT EXTRA LOOP LABELS (V1..V5):\n"
        f"{' '.join(loops) if loops else '(none)'}\n"
        "SINGLETON STEM POSITIONS (MUST be '-'):\n"
        f"{', '.join(singles_desc) if singles_desc else '(none)'}\n\n"
        "VARIABLE STRUCT TOKENS (same order as VARIABLE LABELS):\n"
        f"{struct_line}\n"
        "Token semantics: '>'/'<'/'.' are structure states, 'GAP' means no base at this aligned column, 'UNK' means unavailable.\n\n"
        "GLOBAL CONTEXT (other regions; read-only; do not edit outside variable block):\n"
        f"{global_ctx_line}\n\n"
        "STRUCTURE PAIR HINT (approx stem pairs to realize if feasible):\n"
        f"{hint_line}\n\n"
        f"Variable-block length = {len(vi)}. Return exactly this many tokens.\n"
        "Priority rule: do not put a base on V1..V5 while required standard variable positions remain empty, unless your candidate also realizes filled V11k/V21k stem pairs (long variable arm).\n"
        "For the bridge before T-stem 49, one residue uses 48, two use 46/48, and three use 46/47/48; never retain 47 at the expense of 48.\n"
        "If the structure pair hint is 0, fill an unpaired short region in occupancy order 44, 48, 46, 47, 45; then exhaust V1..V5, then extend into variable-stem labels only as balanced mirror shells from k=1 outward. For example, prefer V11/V21 then V12/V22, not a one-sided run such as V21/V22/V23/V24.\n"
        "Return only JSON: {\"var_target\": [\"A\", \"-\", \"U\", ...]}"
    )
    suffix = str(cfg.get("varloop_user_prompt_suffix") or "").strip()
    if suffix:
        user_prompt = f"{user_prompt}\n\nEXTRA INSTRUCTION:\n{suffix}"
    return [{"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt}]

def _llm_propose_var_target_with_feedback(
    nums: List[str],
    targ_before: List[str],
    pair_hint: Optional[int],
    var_struct_tokens: Optional[List[str]],
    global_struct_summary: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
    attempt_logs: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[str]]:
    messages = _build_llm_messages(
        nums, targ_before, pair_hint, var_struct_tokens, global_struct_summary, cfg
    )
    attempts = 0
    start = time.monotonic()

    while attempts <= int(cfg["max_retries"]):
        attempts += 1
        print(f"[POSTCHECK][LLM] Variable block TARGET proposal attempt #{attempts} ...")
        attempt_log: Dict[str, Any] = {"attempt": attempts}

        # Call
        if cfg["mode"] == "ollama":
            url = f"{cfg['base_url']}/api/chat"
            payload = {
                "model": cfg["model_name"],
                "messages": messages,
                "options": {"temperature": 0},
                "format": "json",
                "stream": bool(cfg["streaming"]),
            }
            ok, raw = _http_post_json(url, payload, cfg)
            if not ok:
                attempt_log["status"] = "http_failed"
                attempt_log["raw_response"] = ""
                if attempt_logs is not None:
                    attempt_logs.append(attempt_log)
                if (time.monotonic() - start) >= float(cfg["hard_budget_s"]):
                    return None
                delay = min(float(cfg["backoff_base_s"]) * (2 ** (attempts - 1)), 2.0)
                print(f"[POSTCHECK][LLM] call failed; backoff {delay:.2f}s")
                time.sleep(delay)
                continue
            content = _extract_probe_content("ollama", raw)
        else:
            url = _openai_chat_completions_url(str(cfg["base_url"]))
            payload = {
                "model": cfg["model_name"],
                "messages": messages,
                "temperature": _openai_temperature(cfg, 0.0),
                "max_tokens": int(cfg["max_tokens"]),
                "stream": bool(cfg["streaming"]),
                "response_format": {"type": "json_object"},
            }
            if str(cfg.get("provider") or "").lower() == "minimax":
                payload["reasoning_split"] = True
            if "reasoner" in str(cfg.get("model_name") or "").lower():
                payload["reasoning_effort"] = "low"
            apply_openai_provider_extras(payload, cfg, model_name=str(cfg.get("model_name") or ""))
            ok, raw = _http_post_json(url, payload, cfg)
            if not ok:
                attempt_log["status"] = "http_failed"
                attempt_log["raw_response"] = ""
                if attempt_logs is not None:
                    attempt_logs.append(attempt_log)
                if (time.monotonic() - start) >= float(cfg["hard_budget_s"]):
                    return None
                delay = min(float(cfg["backoff_base_s"]) * (2 ** (attempts - 1)), 2.0)
                print(f"[POSTCHECK][LLM] call failed; backoff {delay:.2f}s")
                time.sleep(delay)
                continue
            content = _extract_probe_content("openai", raw)
        attempt_log["raw_response"] = _log_trunc(content or "", 1200)

        obj = _find_json_object_with_keys(content, ("var_target",))
        if obj is None:
            attempt_log["status"] = "json_not_found"
            attempt_log["feedback"] = "JSON object not found"
            if attempt_logs is not None:
                attempt_logs.append(attempt_log)
            messages.append({"role": "user",
                             "content": "FEEDBACK: JSON object not found. Return only {\"var_target\": [...]}."})
            continue

        var_target = obj.get("var_target", None)

        if not isinstance(var_target, list):
            attempt_log["status"] = "var_target_not_list"
            attempt_log["feedback"] = "'var_target' must be an array"
            if attempt_logs is not None:
                attempt_logs.append(attempt_log)
            messages.append({"role": "user",
                             "content": "FEEDBACK: 'var_target' must be an array of tokens."})
            continue

        # Shape & order & singleton checks
        ok_shape, err = _check_var_target_shape_and_order(
            var_target,
            nums,
            targ_before,
            pair_hint=pair_hint,
        )
        if not ok_shape:
            attempt_log["status"] = "rejected_shape_or_order"
            attempt_log["feedback"] = err
            if attempt_logs is not None:
                attempt_logs.append(attempt_log)
            vi = _var_indices(nums)
            before_bases = [targ_before[i] for i in vi if targ_before[i] != '-']
            after_bases  = [x for x in var_target if x != '-']
            singles = _singletons_indices(nums)
            must_dash = [nums[i] for i in singles]
            fb = (
                "FEEDBACK: Rejected.\n"
                f"- Reason: {err}\n"
                f"- Expected non-gap order: {' '.join(before_bases) if before_bases else '(empty)'}\n"
                f"- You returned order     : {' '.join(after_bases) if after_bases else '(empty)'}\n"
                f"- These singleton labels MUST be '-': {' '.join(must_dash) if must_dash else '(none)'}\n"
                "Please regenerate 'var_target' strictly preserving the non-gap order and length, "
                "and NEVER place a base at any singleton stem."
            )
            messages.append({"role": "user", "content": fb})
            continue

        # Build candidate targ and validate pairing + final order
        vi = _var_indices(nums)
        targ_llm = list(targ_before)
        for j, i in enumerate(vi):
            tok = str(var_target[j]).upper()
            targ_llm[i] = tok

        ok_pair, errs = _check_pairing(nums, targ_llm)
        if not ok_pair:
            attempt_log["status"] = "rejected_pairing"
            attempt_log["feedback"] = "; ".join(errs)
            if attempt_logs is not None:
                attempt_logs.append(attempt_log)
            messages.append({"role": "user",
                             "content": "FEEDBACK: Pairing violation: " + "; ".join(errs)})
            continue

        # If we have a structure hint, require the LLM to realize at least that many pairs.
        if pair_hint is not None:
            T = len(_non_gap_sequence_in_var(nums, targ_before))
            P = len(_pairs_map(nums))
            desired = min(int(pair_hint), P, T // 2)
            realized = _count_filled_pairs(nums, targ_llm)
            if realized < desired:
                attempt_log["status"] = "rejected_pair_hint"
                attempt_log["feedback"] = f"realized={realized}, desired={desired}"
                if attempt_logs is not None:
                    attempt_logs.append(attempt_log)
                messages.append({"role": "user", "content":
                    "FEEDBACK: Too few stem pairs in variable arm. "
                    f"Need about {desired} paired stems, but got {realized}. "
                    "Preserve order, but place more bases onto paired V1k/V2k columns."})
                continue

        if not _check_order_preserved(nums, targ_before, targ_llm):
            before = ''.join(_non_gap_sequence_in_var(nums, targ_before))
            after  = ''.join(_non_gap_sequence_in_var(nums, targ_llm))
            attempt_log["status"] = "rejected_final_order"
            attempt_log["feedback"] = f"before={before} after={after}"
            if attempt_logs is not None:
                attempt_logs.append(attempt_log)
            messages.append({"role": "user", "content":
                f"FEEDBACK: Non-gap order changed! before='{before}', after='{after}'. "
                "Regenerate so the order is IDENTICAL."})
            continue

        print("[POSTCHECK][LLM] var_target accepted.")
        attempt_log["status"] = "accepted"
        attempt_log["var_target"] = [str(x).upper() for x in var_target]
        if attempt_logs is not None:
            attempt_logs.append(attempt_log)
        return [targ_llm[i] for i in vi]  # normalized accepted var-target

    # Reasoner may stop at token limit with reasoning only and no final JSON.
    # Keep fallback model selection within the configured provider.
    if cfg["mode"] == "openai" and "reasoner" in str(cfg.get("model_name") or "").lower():
        fb_log: Dict[str, Any] = {"attempt": "fallback_chat"}
        url = _openai_chat_completions_url(str(cfg["base_url"]))
        payload = {
            "model": provider_fallback_model(
                cfg,
                deepseek_fallback_model(runtime_values),
            ),
            "messages": messages,
            "temperature": _openai_temperature(cfg, 0.0),
            "max_tokens": int(cfg["max_tokens"]),
            "stream": bool(cfg["streaming"]),
            "response_format": {"type": "json_object"},
        }
        if str(cfg.get("provider") or "").lower() == "minimax":
            payload["reasoning_split"] = True
        apply_openai_provider_extras(payload, cfg, model_name=str(payload.get("model") or ""))
        ok, raw = _http_post_json(url, payload, cfg)
        if not ok:
            fb_log["status"] = "http_failed"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        content = _extract_probe_content("openai", raw)
        fb_log["raw_response"] = _log_trunc(content or "", 1200)
        obj = _find_json_object_with_keys(content, ("var_target",))
        if obj is None:
            fb_log["status"] = "json_not_found"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        var_target = obj.get("var_target", None)
        if not isinstance(var_target, list):
            fb_log["status"] = "var_target_not_list"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        ok_shape, err = _check_var_target_shape_and_order(
            var_target,
            nums,
            targ_before,
            pair_hint=pair_hint,
        )
        if not ok_shape:
            fb_log["status"] = "rejected_shape_or_order"
            fb_log["feedback"] = err
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        vi = _var_indices(nums)
        targ_fb = list(targ_before)
        for j, i in enumerate(vi):
            targ_fb[i] = str(var_target[j]).upper()

        ok_pair, errs = _check_pairing(nums, targ_fb)
        if not ok_pair:
            fb_log["status"] = "rejected_pairing"
            fb_log["feedback"] = "; ".join(errs)
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        if pair_hint is not None:
            T = len(_non_gap_sequence_in_var(nums, targ_before))
            P = len(_pairs_map(nums))
            desired = min(int(pair_hint), P, T // 2)
            realized = _count_filled_pairs(nums, targ_fb)
            if realized < desired:
                fb_log["status"] = "rejected_pair_hint"
                fb_log["feedback"] = f"realized={realized}, desired={desired}"
                if attempt_logs is not None:
                    attempt_logs.append(fb_log)
                print("[POSTCHECK][LLM] give up after retries/budget.")
                return None

        if not _check_order_preserved(nums, targ_before, targ_fb):
            before = ''.join(_non_gap_sequence_in_var(nums, targ_before))
            after = ''.join(_non_gap_sequence_in_var(nums, targ_fb))
            fb_log["status"] = "rejected_final_order"
            fb_log["feedback"] = f"before={before} after={after}"
            if attempt_logs is not None:
                attempt_logs.append(fb_log)
            print("[POSTCHECK][LLM] give up after retries/budget.")
            return None

        fb_log["status"] = "accepted"
        fb_log["var_target"] = [str(x).upper() for x in var_target]
        if attempt_logs is not None:
            attempt_logs.append(fb_log)
        print("[POSTCHECK][LLM] var_target accepted via fallback_chat.")
        return [targ_fb[i] for i in vi]

    print("[POSTCHECK][LLM] give up after retries/budget.")
    return None


def trace_variable_loop_block(
    block: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
    run_llm: bool = True,
) -> Dict[str, Any]:
    """
    Build trace payload for variable-loop prompt replay.
    Returns prompts, structure hints, and optional LLM attempt logs.
    """
    cfg = _load_runtime_cfg(runtime_values)
    ext = extract_single_block_lines(block, norm_base=lambda x: str(x or ""))
    if ext is None:
        return invalid_trace_preview(
            "Invalid block: expected 3 lines.",
            extra={"var_target": None},
        )

    nums, _, targ, meta = ext
    vi = _var_indices(nums)
    var_struct = str(meta.get("input_struct") or meta.get("var_struct") or "")
    global_struct_summary = meta.get("global_struct_summary")
    if not isinstance(global_struct_summary, dict):
        global_struct_summary = None
    pair_hint = _pair_hint_from_struct(var_struct)
    var_struct_tokens, struct_note = _var_struct_tokens(nums, targ, var_struct)
    configured_prompt = str(cfg.get("varloop_system_prompt") or "").strip()
    messages = _build_llm_messages(
        nums, targ, pair_hint, var_struct_tokens, global_struct_summary, cfg
    )
    system_prompt = configured_prompt
    user_prompt = str(messages[1].get("content") or "")
    enabled = bool(configured_prompt)

    out: Dict[str, Any] = {
        "implemented": True,
        "enabled": enabled,
        "postcheck_on": bool(cfg.get("postcheck_on")),
        "run_llm": bool(run_llm),
        "var_label_count": len(vi),
        "pair_hint": pair_hint,
        "struct_note": struct_note,
        "var_struct_tokens": var_struct_tokens,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "llm_attempts": [],
        "var_target": None,
    }

    if not bool(cfg.get("postcheck_on")):
        out["status"] = "postcheck_disabled"
        out["message"] = "LLM_POSTCHECK is disabled."
        return out
    if not enabled:
        out["status"] = "skipped_prompt_empty"
        out["message"] = "LLM_VARLOOP_SYSTEM_PROMPT is empty."
        return out
    if not vi:
        out["status"] = "no_variable_positions"
        out["message"] = "No variable labels in this block."
        return out
    if not run_llm:
        out["status"] = "preview_only"
        out["message"] = "Prompt preview only (LLM call skipped)."
        return out

    attempt_logs: List[Dict[str, Any]] = []
    var_target = _llm_propose_var_target_with_feedback(
        nums,
        targ,
        pair_hint,
        var_struct_tokens,
        global_struct_summary,
        cfg,
        runtime_values=runtime_values,
        attempt_logs=attempt_logs,
    )
    out["llm_attempts"] = attempt_logs
    if var_target is None:
        out["status"] = "llm_failed"
        out["message"] = "No valid var_target returned."
        return out

    out["status"] = "llm_success"
    out["var_target"] = [str(x).upper() for x in var_target]
    out["message"] = "Variable-loop prompt replay succeeded."
    return out

# -----------------------------
# Public handler
# -----------------------------
def postcheck_variable_loop_block(
    block: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Variable loop/arm post-check (TARGET-ONLY inside variable columns).
    """
    cfg = _load_runtime_cfg(runtime_values)
    _log_runtime_cfg(cfg)

    ext = extract_single_block_lines(block, norm_base=lambda x: str(x or ""))
    if ext is None:
        return block

    nums, templ, targ, meta = ext

    out_meta = dict(meta)
    configured_prompt = str(cfg.get("varloop_system_prompt") or "").strip()
    var_struct = str(meta.get("input_struct") or meta.get("var_struct") or "")
    global_struct_summary = meta.get("global_struct_summary")
    if not isinstance(global_struct_summary, dict):
        global_struct_summary = None
    pair_hint = _pair_hint_from_struct(var_struct)
    var_struct_tokens, struct_note = _var_struct_tokens(nums, targ, var_struct)
    messages = _build_llm_messages(
        nums, targ, pair_hint, var_struct_tokens, global_struct_summary, cfg
    )
    runtime_trace = build_runtime_trace(
        region=block.get("region", "Variable loop"),
        configured_prompt=configured_prompt,
        effective_prompt=configured_prompt,
        user_prompt=str(messages[1].get("content") or "") if len(messages) > 1 else "",
        attempt_logs=[],
        run_llm=bool(cfg.get("postcheck_on")),
        enabled=bool(configured_prompt),
        extra={
            "pair_hint": pair_hint,
            "struct_note": struct_note,
        },
    )
    if not bool(cfg["postcheck_on"]):
        message = "LLM_POSTCHECK is disabled."
        runtime_trace = finalized_runtime_trace(
            runtime_trace,
            status="postcheck_disabled",
            message=message,
            extra=build_canonical_trace_fields(
                raw_status="postcheck_disabled",
                attempt_logs=[],
                message=message,
                output_changed=False,
            ),
        )
        return block_with_runtime_trace(
            region=block.get("region", "Variable loop"),
            nums=nums,
            templ=templ,
            targ=targ,
            meta=out_meta,
            runtime_trace=runtime_trace,
        )
    if not configured_prompt:
        message = "LLM_VARLOOP_SYSTEM_PROMPT is empty."
        runtime_trace = finalized_runtime_trace(
            runtime_trace,
            status="skipped_prompt_empty",
            message=message,
            extra=build_canonical_trace_fields(
                raw_status="skipped_prompt_empty",
                attempt_logs=[],
                message=message,
                output_changed=False,
            ),
        )
        return block_with_runtime_trace(
            region=block.get("region", "Variable loop"),
            nums=nums,
            templ=templ,
            targ=targ,
            meta=out_meta,
            runtime_trace=runtime_trace,
        )

    vi = _var_indices(nums)
    if not vi:
        print("[POSTCHECK] Variable loop block has no V positions; pass-through.")
        message = "No variable labels in this block."
        runtime_trace = finalized_runtime_trace(
            runtime_trace,
            status="no_variable_positions",
            message=message,
            extra=build_canonical_trace_fields(
                raw_status="no_variable_positions",
                attempt_logs=[],
                message=message,
                output_changed=False,
            ),
        )
        return block_with_runtime_trace(
            region=block.get("region", "Variable loop"),
            nums=nums,
            templ=templ,
            targ=targ,
            meta=out_meta,
            runtime_trace=runtime_trace,
        )

    if pair_hint is not None:
        print(f"[POSTCHECK][HINT] variable-arm pair_hint={pair_hint} from structure.")
    if struct_note not in ("ok", "empty_struct"):
        print(f"[POSTCHECK][HINT] variable struct alignment note: {struct_note}")

    if pair_hint == 0:
        deterministic_target = _alloc_deterministic(nums, targ, pair_hint=pair_hint)
        deterministic_var_target = [deterministic_target[i] for i in vi]
        deterministic_valid, _ = _check_var_target_shape_and_order(
            deterministic_var_target,
            nums,
            targ,
            pair_hint=pair_hint,
        )
        deterministic_pairing, _ = _check_pairing(nums, deterministic_target)
        if deterministic_valid and deterministic_pairing:
            changed = any(
                _norm_base(before) != _norm_base(after)
                for before, after in zip(targ, deterministic_target)
            )
            targ = deterministic_target
            rebuilt = rebuild_variable_block_topology(nums, templ, targ, var_struct)
            if rebuilt is not None:
                new_nums, new_templ, new_targ = rebuilt
                if [nums, templ, targ] != [new_nums, new_templ, new_targ]:
                    nums, templ, targ = new_nums, new_templ, new_targ
                    changed = True
            message = (
                "Applied deterministic Sprinzl priority rule for an unpaired "
                "variable region; LLM call was not required."
            )
            runtime_trace["llm_attempts"] = []
            runtime_trace = finalized_runtime_trace(
                runtime_trace,
                status="deterministic_structure_rule",
                message=message,
                extra=build_canonical_trace_fields(
                    raw_status="deterministic_structure_rule",
                    attempt_logs=[],
                    message=message,
                    accepted_candidate={
                        "region": block.get("region", "Variable loop"),
                        "ids": [str(x or "") for x in nums],
                        "template_tokens": [_norm_base(x) for x in templ],
                        "target_tokens": [_norm_base(x) for x in targ],
                    },
                    output_changed=changed,
                ),
            )
            return block_with_runtime_trace(
                region=block.get("region", "Variable loop"),
                nums=nums,
                templ=templ,
                targ=targ,
                meta=out_meta,
                runtime_trace=runtime_trace,
            )

    # ---- Try LLM with feedback ----
    print("[POSTCHECK][LLM] Variable loop TARGET proposer...")
    attempt_logs: List[Dict[str, Any]] = []
    var_target = _llm_propose_var_target_with_feedback(
        nums,
        targ,
        pair_hint,
        var_struct_tokens,
        global_struct_summary,
        cfg,
        runtime_values=runtime_values,
        attempt_logs=attempt_logs,
    )
    runtime_trace["llm_attempts"] = attempt_logs

    if var_target is not None:
        # Apply accepted var_target
        targ_new = list(targ)
        for j, i in enumerate(vi):
            targ_new[i] = str(var_target[j]).upper()
        changed = any(_norm_base(a) != _norm_base(b) for a, b in zip(targ, targ_new))
        # Final checks
        ok_pair, errs = _check_pairing(nums, targ_new)
        if not ok_pair:
            print("[POSTCHECK][LLM] unexpected pairing failure after accept:", "; ".join(errs))
        if not _check_order_preserved(nums, targ, targ_new):
            print("[POSTCHECK][LLM] unexpected order failure after accept (should not).")
        targ = targ_new
        rebuilt = rebuild_variable_block_topology(nums, templ, targ, var_struct)
        if rebuilt is not None:
            new_nums, new_templ, new_targ = rebuilt
            if [nums, templ, targ] != [new_nums, new_templ, new_targ]:
                nums, templ, targ = new_nums, new_templ, new_targ
                changed = True
        message = "Applied improved TARGET from LLM."
        runtime_trace = finalized_runtime_trace(
            runtime_trace,
            status="accepted",
            message=message,
            extra=build_canonical_trace_fields(
                raw_status="accepted",
                attempt_logs=attempt_logs,
                message=message,
                accepted_candidate={
                    "region": block.get("region", "Variable loop"),
                    "ids": [str(x or "") for x in nums],
                    "template_tokens": [_norm_base(x) for x in templ],
                    "target_tokens": [_norm_base(x) for x in targ],
                },
                output_changed=changed,
            ),
        )
    else:
        # ---- Deterministic fallback (order-preserving) ----
        print("[POSTCHECK] LLM unavailable/invalid; using deterministic allocator.")
        targ_d = _alloc_deterministic(nums, targ, pair_hint=pair_hint)
        changed = any(_norm_base(a) != _norm_base(b) for a, b in zip(targ, targ_d))
        targ = targ_d  # allocator内部已做顺序/配对/单侧禁填校验；失败则回退原值
        rebuilt = rebuild_variable_block_topology(nums, templ, targ, var_struct)
        if rebuilt is not None:
            new_nums, new_templ, new_targ = rebuilt
            if [nums, templ, targ] != [new_nums, new_templ, new_targ]:
                nums, templ, targ = new_nums, new_templ, new_targ
                changed = True
        message = "LLM proposal unavailable/invalid; deterministic allocator applied."
        runtime_trace = finalized_runtime_trace(
            runtime_trace,
            status="fallback_deterministic",
            message=message,
            extra=build_canonical_trace_fields(
                raw_status="fallback_deterministic",
                attempt_logs=attempt_logs,
                message=message,
                accepted_candidate={
                    "region": block.get("region", "Variable loop"),
                    "ids": [str(x or "") for x in nums],
                    "template_tokens": [_norm_base(x) for x in templ],
                    "target_tokens": [_norm_base(x) for x in targ],
                },
                output_changed=changed,
            ),
        )

    return block_with_runtime_trace(
        region=block.get("region", "Variable loop"),
        nums=nums,
        templ=templ,
        targ=targ,
        meta=out_meta,
        runtime_trace=runtime_trace,
    )

# ===== Backward-compat alias =====
def postcheck_variable_loop(
    block: Dict[str, Any],
    runtime_values: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return postcheck_variable_loop_block(block, runtime_values=runtime_values)
