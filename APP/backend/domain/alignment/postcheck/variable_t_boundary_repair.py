# -*- coding: utf-8 -*-
"""
Conservative repair for cross-boundary phase shifts between Variable loop and T region.

Goal:
- Catch a very specific failure mode where an unsupported V1 is used as a buffer,
  the downstream T region shifts by one column, and a compensatory T insertion
  (for example 65i1) appears.
- Apply only when evidence is strong, so normal long variable-arm or true
  insertion cases are not touched.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .t_region import (
    _deterministic_local_search_candidate as _repair_t_block,
    _is_insertion_label as _is_t_insertion_label,
)
from .variable_loop import (
    _alloc_deterministic as _repair_variable_block,
    _pair_hint_from_struct,
)


def _norm_base(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("T", "U")
    return s if s in {"A", "U", "G", "C", "N", "-"} else "-"


def _find_region_index(blocks: List[Dict[str, Any]], region_name: str) -> Optional[int]:
    for i, blk in enumerate(blocks):
        if str((blk or {}).get("region") or "") == region_name:
            return i
    return None


def _block_lines(blk: Dict[str, Any]) -> Optional[Tuple[List[str], List[str], List[str]]]:
    lines = (blk or {}).get("lines")
    if not (isinstance(lines, list) and len(lines) == 3):
        return None
    nums = [str(x or "") for x in list(lines[0])]
    templ = [_norm_base(x) for x in list(lines[1])]
    targ = [_norm_base(x) for x in list(lines[2])]
    if not (len(nums) == len(templ) == len(targ)):
        return None
    return nums, templ, targ


def _is_var_loop_label(label: str) -> bool:
    return label in {"V1", "V2", "V3", "V4", "V5"}


def _is_var_extension_label(label: str) -> bool:
    return label.startswith("V")


def _occupied_indices(nums: List[str], targ: List[str], predicate) -> List[int]:
    return [i for i, (n, b) in enumerate(zip(nums, targ)) if predicate(str(n or "")) and _norm_base(b) != "-"]


def _occupied_supported_t_gap_count(nums: List[str], templ: List[str], targ: List[str]) -> int:
    count = 0
    for n, tb, qb in zip(nums, templ, targ):
        label = str(n or "")
        if _is_t_insertion_label(label):
            continue
        if not label or not label[0].isdigit():
            continue
        try:
            num = int(label)
        except ValueError:
            continue
        if 49 <= num <= 65 and _norm_base(tb) != "-" and _norm_base(qb) == "-":
            count += 1
    return count


def _occupied_t_insertions(nums: List[str], targ: List[str]) -> int:
    return sum(
        1
        for n, b in zip(nums, targ)
        if _is_t_insertion_label(str(n or "")) and _norm_base(b) != "-"
    )


def _t_supported_indices(nums: List[str], templ: List[str]) -> List[int]:
    out: List[int] = []
    for i, (n, tb) in enumerate(zip(nums, templ)):
        label = str(n or "")
        if _is_t_insertion_label(label):
            continue
        if _norm_base(tb) == "-":
            continue
        out.append(i)
    return out


def _var_extension_template_supported(nums: List[str], templ: List[str]) -> bool:
    return any(
        _is_var_extension_label(str(n or "")) and _norm_base(tb) != "-"
        for n, tb in zip(nums, templ)
    )


def _var_meta_pair_hint(blk: Dict[str, Any]) -> Optional[int]:
    meta = (blk or {}).get("meta")
    if not isinstance(meta, dict):
        return None
    raw_struct = str(meta.get("input_struct") or meta.get("var_struct") or "")
    return _pair_hint_from_struct(raw_struct)


def _boundary_pattern_score(
    var_nums: List[str],
    var_templ: List[str],
    var_targ: List[str],
    t_nums: List[str],
    t_templ: List[str],
    t_targ: List[str],
    *,
    pair_hint: Optional[int],
) -> Tuple[int, int, int, int]:
    unsupported_v_loop = 0
    if not _var_extension_template_supported(var_nums, var_templ) and (pair_hint in (None, 0)):
        unsupported_v_loop = len(_occupied_indices(var_nums, var_targ, _is_var_loop_label))
    return (
        unsupported_v_loop,
        _occupied_t_insertions(t_nums, t_targ),
        _occupied_supported_t_gap_count(t_nums, t_templ, t_targ),
        len(_occupied_indices(var_nums, var_targ, _is_var_extension_label)),
    )


def _should_attempt_boundary_repair(
    var_nums: List[str],
    var_templ: List[str],
    var_targ: List[str],
    t_nums: List[str],
    t_templ: List[str],
    t_targ: List[str],
    *,
    pair_hint: Optional[int],
) -> bool:
    unsupported_v1 = any(
        str(n or "") == "V1" and _norm_base(b) != "-"
        for n, b in zip(var_nums, var_targ)
    )
    if not unsupported_v1:
        return False
    if _var_extension_template_supported(var_nums, var_templ):
        return False
    if pair_hint not in (None, 0):
        return False
    if len(_occupied_indices(var_nums, var_targ, lambda n: n.startswith("V1") and n != "V1")) > 0:
        return False
    if len(_occupied_indices(var_nums, var_targ, lambda n: n.startswith("V2"))) > 0:
        return False
    if _occupied_t_insertions(t_nums, t_targ) == 0:
        return False
    if _occupied_supported_t_gap_count(t_nums, t_templ, t_targ) == 0:
        return False
    return True


def _non_gap_tokens(seq: List[str]) -> List[str]:
    return [_norm_base(x) for x in seq if _norm_base(x) != "-"]


def _refill_by_indices(length: int, fill_indices: List[int], tokens: List[str]) -> Optional[List[str]]:
    if len(fill_indices) != len(tokens):
        return None
    out = ["-"] * length
    for idx, tok in zip(fill_indices, tokens):
        out[idx] = _norm_base(tok)
    return out


def _try_simple_leading_gap_shift(
    var_nums: List[str],
    var_templ: List[str],
    var_targ: List[str],
    t_nums: List[str],
    t_templ: List[str],
    t_targ: List[str],
    *,
    pair_hint: Optional[int],
) -> Optional[Tuple[List[str], List[str]]]:
    if _should_attempt_boundary_repair(
        var_nums, var_templ, var_targ, t_nums, t_templ, t_targ, pair_hint=pair_hint
    ):
        return None
    if _var_extension_template_supported(var_nums, var_templ):
        return None
    if pair_hint not in (None, 0):
        return None

    v1_idx = var_nums.index("V1") if "V1" in var_nums else None
    if v1_idx is None:
        return None

    occupied_v_loops = _occupied_indices(var_nums, var_targ, _is_var_loop_label)
    occupied_v_ext = _occupied_indices(var_nums, var_targ, _is_var_extension_label)
    if occupied_v_loops != [v1_idx]:
        return None
    if len(occupied_v_ext) != 1:
        return None

    core_indices = [i for i, n in enumerate(var_nums) if str(n or "") in {"44", "45", "46", "47", "48"}]
    var_tokens = _non_gap_tokens(var_targ)
    if len(core_indices) + 1 != len(var_tokens):
        return None

    supported_t = _t_supported_indices(t_nums, t_templ)
    if not supported_t:
        return None
    if str(t_nums[supported_t[0]] or "") != "49":
        return None
    if _norm_base(t_targ[supported_t[0]]) != "-":
        return None
    if _occupied_t_insertions(t_nums, t_targ) != 0:
        return None
    if any(_norm_base(t_targ[i]) == "-" for i in supported_t[1:]):
        return None

    keep_var = var_tokens[: len(core_indices)]
    push_t = var_tokens[len(core_indices) :]
    if len(push_t) != 1:
        return None
    new_var = _refill_by_indices(len(var_nums), core_indices, keep_var)
    if new_var is None:
        return None

    t_tokens = _non_gap_tokens(t_targ)
    new_t = _refill_by_indices(len(t_nums), supported_t, push_t + t_tokens)
    if new_t is None:
        return None
    return new_var, new_t


def _with_lines(blk: Dict[str, Any], targ: List[str]) -> Dict[str, Any]:
    nums, templ, _ = _block_lines(blk) or ([], [], [])
    out = dict(blk)
    out["lines"] = [list(nums), list(templ), list(targ)]
    return out


def repair_variable_t_boundary_shift(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    idx_var = _find_region_index(blocks, "Variable loop")
    idx_t = _find_region_index(blocks, "T loop + T stem")
    if idx_var is None or idx_t is None:
        return list(blocks)

    blk_var = blocks[idx_var]
    blk_t = blocks[idx_t]
    var_lines = _block_lines(blk_var)
    t_lines = _block_lines(blk_t)
    if var_lines is None or t_lines is None:
        return list(blocks)

    var_nums, var_templ, var_targ = var_lines
    t_nums, t_templ, t_targ = t_lines
    pair_hint = _var_meta_pair_hint(blk_var)

    simple_shift = _try_simple_leading_gap_shift(
        var_nums, var_templ, var_targ, t_nums, t_templ, t_targ, pair_hint=pair_hint
    )
    if simple_shift is not None:
        var_candidate, t_candidate = simple_shift
    else:
        if not _should_attempt_boundary_repair(
            var_nums, var_templ, var_targ, t_nums, t_templ, t_targ, pair_hint=pair_hint
        ):
            return list(blocks)

        var_candidate = _repair_variable_block(var_nums, var_targ, pair_hint=pair_hint)
        t_candidate = _repair_t_block(t_nums, t_targ)
        if var_candidate is None or t_candidate is None:
            return list(blocks)

    before_score = _boundary_pattern_score(
        var_nums, var_templ, var_targ, t_nums, t_templ, t_targ, pair_hint=pair_hint
    )
    after_score = _boundary_pattern_score(
        var_nums, var_templ, var_candidate, t_nums, t_templ, t_candidate, pair_hint=pair_hint
    )
    if after_score >= before_score:
        return list(blocks)

    out = list(blocks)
    out[idx_var] = _with_lines(blk_var, var_candidate)
    out[idx_t] = _with_lines(blk_t, t_candidate)

    var_meta = dict((out[idx_var].get("meta") or {}) if isinstance(out[idx_var].get("meta"), dict) else {})
    t_meta = dict((out[idx_t].get("meta") or {}) if isinstance(out[idx_t].get("meta"), dict) else {})
    trace = {
        "status": "applied",
        "pair_hint": pair_hint,
        "before_score": list(before_score),
        "after_score": list(after_score),
        "message": "Applied conservative Variable/T boundary phase-shift repair.",
    }
    var_meta["boundary_phase_repair"] = trace
    t_meta["boundary_phase_repair"] = trace
    out[idx_var]["meta"] = var_meta
    out[idx_t]["meta"] = t_meta
    return out
