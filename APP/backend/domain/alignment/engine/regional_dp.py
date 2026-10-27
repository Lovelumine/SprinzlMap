from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..debug_config import (
    DEBUG_ALIGN as _ALIGN_DEBUG,
    DEBUG_ALIGN_FULL as _ALIGN_DEBUG_FULL,
    DEBUG_ALIGN_MAXLEN as _ALIGN_DEBUG_MAXLEN,
)
from ..safety.validation import validate_alignment
from .scoring import alignment_score_and_str


logger = logging.getLogger(__name__)


def _is_terminal_reserved_label(label: str) -> bool:
    value = str(label or "").strip()
    return value in {"74", "75", "76"} or value.startswith(
        ("74i", "75i", "76i")
    )


def _short(s: str, max_len: int = _ALIGN_DEBUG_MAXLEN) -> str:
    if s is None:
        return ""
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}...(len={len(s)})"


def _dprint(msg: str) -> None:
    if _ALIGN_DEBUG:
        print(msg)


def _count_tail_slots(template_num: List[str], start_idx: int, tail_set: set[str]) -> int:
    cnt = 0
    for n in template_num[start_idx:]:
        if n in tail_set:
            cnt += 1
    return cnt


def _build_aa3_order_preserving_fallback(
    template_num: List[str],
    template_seq: str,
    target_seq: str,
) -> Optional[Dict[str, List[str]]]:
    """
    Conservative AA3 fallback used only when DP has already broken base preservation.
    """
    if not template_num:
        return None

    norm_target = [
        ch.upper().replace("T", "U")
        for ch in target_seq
        if ch.upper().replace("T", "U") in {"A", "U", "G", "C", "N"}
    ]
    if not norm_target:
        aligned_templ = list(template_seq[: len(template_num)])
        while len(aligned_templ) < len(template_num):
            aligned_templ.append("-")
        return {
            "aligned_numbering": list(template_num),
            "aligned_sequence": ["-"] * len(template_num),
            "aligned_template": aligned_templ,
        }

    reserved_labels: List[str] = []
    reserved_bases: List[str] = []
    template_labels = set(template_num)
    for suffix, labels in (("CCA", ["74", "75", "76"]),):
        if target_seq.upper().replace("T", "U").endswith(suffix) and all(
            label in template_labels for label in labels
        ):
            reserved_labels = labels
            reserved_bases = list(suffix)
            norm_target = norm_target[: len(norm_target) - len(suffix)]
            break

    tail_slot_labels = {
        label for label in template_num if _is_terminal_reserved_label(label)
    }
    available_positions = [
        idx
        for idx, label in enumerate(template_num)
        if (
            not str(label or "").startswith("-")
            and label not in reserved_labels
            and label not in tail_slot_labels
        )
    ]
    if len(norm_target) > len(available_positions):
        return None

    aligned_nums = list(template_num)
    aligned_templ = list(template_seq[: len(template_num)])
    while len(aligned_templ) < len(template_num):
        aligned_templ.append("-")
    aligned_seqs = ["-"] * len(template_num)

    for pos, base in zip(available_positions, norm_target):
        aligned_seqs[pos] = base

    if reserved_labels:
        label_to_idx = {str(label): idx for idx, label in enumerate(template_num)}
        for label, base in zip(reserved_labels, reserved_bases):
            pos = label_to_idx.get(label)
            if pos is None:
                return None
            aligned_seqs[pos] = base

    if not validate_alignment(target_seq, aligned_seqs):
        return None

    return {
        "aligned_numbering": aligned_nums,
        "aligned_sequence": aligned_seqs,
        "aligned_template": aligned_templ,
    }


def align_with_dp(
    template_num: List[str],
    template_seq: str,
    target_seq: str,
    match: float = 2.0,
    mismatch: float = -1.0,
    gap_open: float = -2.0,
    gap_extend: float = -0.5,
    *,
    region: Optional[str] = None,
    prev_num: Optional[str] = None,
) -> Dict[str, List[str]]:
    """
    Global alignment using the required parasail backend. Converts to
    aligned_numbering/aligned_sequence while consuming numbered vacant slots.
    """
    _dprint(f"=== [align][DEBUG] _align_with_dp called (region={region!r}) ===")
    _dprint(f"[align][DEBUG] template_seq : {_short(template_seq)}")
    _dprint(f"[align][DEBUG] target_seq   : {_short(target_seq)}")
    _dprint(f"[align][DEBUG] template_num : {template_num[:20]} ... len={len(template_num)}")

    if not target_seq:
        templ_list = list(template_seq)
        n = min(len(template_num), len(templ_list))
        if n == 0:
            return {"aligned_numbering": [], "aligned_sequence": [], "aligned_template": []}
        aligned_nums = list(template_num[:n])
        aligned_templ = templ_list[:n]
        aligned_seqs = ["-"] * n
        return {
            "aligned_numbering": aligned_nums,
            "aligned_sequence": aligned_seqs,
            "aligned_template": aligned_templ,
        }

    score, flat = alignment_score_and_str(
        target_seq,
        template_seq,
        match,
        mismatch,
        gap_open,
        gap_extend,
        use_edlib=False,
        use_parasail=True,
    )
    _dprint(f"[align][DEBUG] DP score     : {score}")
    if _ALIGN_DEBUG_FULL:
        _dprint(f"[align][DEBUG] DP flat:\n{flat}")
    else:
        lines = flat.splitlines()
        if lines:
            shown = "\n".join(_short(ln) for ln in lines[:3])
            _dprint(f"[align][DEBUG] DP flat (trunc):\n{shown}")

    flat_target = ""
    flat_template = ""
    for line in flat.splitlines():
        parts = line.split()
        if not parts:
            continue
        if line.startswith("target"):
            flat_target = parts[1]
        elif line.startswith("query"):
            flat_template = parts[1]
    if not flat_target or not flat_template:
        raise RuntimeError("alignment_score_and_str returned unexpected format")

    L = min(len(flat_target), len(flat_template))
    t_list = list(flat_target[:L])
    q_list = list(flat_template[:L])
    cols = list(zip(q_list, t_list))

    rem_non_gap = [0] * L
    running = 0
    for i in range(L - 1, -1, -1):
        if t_list[i] != "-":
            running += 1
        rem_non_gap[i] = running

    aligned_nums: List[str] = []
    aligned_seqs: List[str] = []
    aligned_templ: List[str] = []
    idx_template = 0

    def _normalize_anchor(n: Optional[str]) -> Optional[str]:
        if not n:
            return None
        if n.startswith("-") or n.startswith("V"):
            return None
        if "i" in n:
            base = n.split("i", 1)[0]
            return base if base and not base.startswith("V") else None
        return n

    last_num: str | None = _normalize_anchor(prev_num)
    ins_count = 1

    tail_canon = {"74", "75", "76"}
    normalized_target = target_seq.upper().replace("T", "U")
    target_has_complete_cca = normalized_target.endswith("CCA")

    if (
        region == "Aminoacyl arm 3' end"
        and (
            target_has_complete_cca
            or any(_is_terminal_reserved_label(label) for label in template_num)
        )
    ):
        # The CCA columns are a reserved terminal block. Align the
        # pre-tail sequence independently, then either place one observed
        # complete CCA in 74-76 or leave all three columns unoccupied.
        body_indices = [
            index for index, label in enumerate(template_num)
            if not _is_terminal_reserved_label(label)
        ]
        reserved_indices = [
            index for index, label in enumerate(template_num)
            if _is_terminal_reserved_label(label)
        ]
        canonical_tail_present = {
            template_num[index] for index in reserved_indices
        }.intersection(tail_canon)
        if target_has_complete_cca and canonical_tail_present != tail_canon:
            raise ValueError(
                "A complete terminal CCA requires all Sprinzl coordinates "
                "74-76 in the AA3 template"
            )
        body_target = normalized_target[:-3] if target_has_complete_cca else normalized_target
        body_result = align_with_dp(
            [template_num[index] for index in body_indices],
            "".join(
                template_seq[index]
                for index in body_indices
                if index < len(template_seq)
            ),
            body_target,
            match=match,
            mismatch=mismatch,
            gap_open=gap_open,
            gap_extend=gap_extend,
            region=region,
            prev_num=prev_num,
        )
        tail_template = [
            template_seq[index] if index < len(template_seq) else "-"
            for index in reserved_indices
        ]
        tail_sequence = (
            [
                {"74": "C", "75": "C", "76": "A"}.get(
                    template_num[index],
                    "-",
                )
                for index in reserved_indices
            ]
            if target_has_complete_cca
            else ["-" for _ in reserved_indices]
        )
        return {
            "aligned_numbering": [
                *list(body_result.get("aligned_numbering", [])),
                *[template_num[index] for index in reserved_indices],
            ],
            "aligned_sequence": [
                *list(body_result.get("aligned_sequence", [])),
                *tail_sequence,
            ],
            "aligned_template": [
                *list(body_result.get("aligned_template", [])),
                *tail_template,
            ],
        }

    target_has_complete_cca = normalized_target.endswith("CCA")

    def _terminal_insertion_error(anchor: Optional[str]) -> None:
        if (
            region == "Aminoacyl arm 3' end"
            and anchor is not None
            and (
                anchor in {"74", "75", "76"}
                or anchor.startswith(("74i", "75i", "76i"))
            )
        ):
            raise ValueError(
                "Terminal residues cannot be represented with Sprinzl "
                "74i*/75i*/76i* insertion labels; 74-76 are reserved "
                "for a complete CCA tail. A non-CCA terminal extension may "
                "use 73i* when it cannot be represented by the canonical tail."
            )

    for col_idx, (templ_char, targ_char) in enumerate(cols):
        templ_is_placeholder = (
            templ_char == "-"
            and idx_template < len(template_seq)
            and template_seq[idx_template] == "-"
        )

        if templ_char == "-" and not templ_is_placeholder:
            curr_num = template_num[idx_template] if idx_template < len(template_num) else None

            if curr_num is not None and curr_num.startswith("-"):
                aligned_nums.append(curr_num)
                aligned_seqs.append(targ_char if targ_char != "-" else "-")
                aligned_templ.append("-")
                last_num = curr_num
                idx_template += 1
                ins_count = 1
                continue

            if curr_num is not None and region == "Variable loop" and curr_num.startswith("V"):
                aligned_nums.append(curr_num)
                aligned_seqs.append(targ_char if targ_char != "-" else "-")
                aligned_templ.append("-")
                last_num = curr_num
                idx_template += 1
                ins_count = 1
                if targ_char != "-":
                    _dprint(f"[align][DEBUG] Variable loop consume: {curr_num} <- {targ_char}")
                continue

            if (
                curr_num is not None
                and region == "Aminoacyl arm 3' end"
                and _is_terminal_reserved_label(curr_num)
                and targ_char != "-"
            ):
                if curr_num not in tail_canon:
                    raise ValueError(
                        "Terminal insertion labels 74i*/75i*/76i* cannot "
                        "replace canonical Sprinzl coordinates 74-76"
                    )
                if not target_has_complete_cca:
                    raise ValueError(
                        "Incomplete or non-CCA terminal extension cannot occupy "
                        "Sprinzl coordinates 74-76"
                    )
                tail_slots_remaining = _count_tail_slots(template_num, idx_template, tail_canon)
                tg_remaining = rem_non_gap[col_idx]
                if tg_remaining <= tail_slots_remaining:
                    aligned_nums.append(curr_num)
                    aligned_seqs.append(targ_char)
                    aligned_templ.append("-")
                    last_num = curr_num
                    idx_template += 1
                    ins_count = 1
                    _dprint(
                        f"[align][DEBUG] Tail consume: {curr_num} <- {targ_char} "
                        f"(tg_rem={tg_remaining}, tail_slots={tail_slots_remaining})"
                    )
                    continue
                base = last_num or (template_num[0] if template_num else "X")
                _terminal_insertion_error(base)
                num = f"{base}i{ins_count}"
                aligned_nums.append(num)
                aligned_seqs.append(targ_char)
                aligned_templ.append("-")
                ins_count += 1
                _dprint(
                    f"[align][DEBUG] Tail defer as insert: {num} <- {targ_char} "
                    f"(tg_rem>{tail_slots_remaining})"
                )
                continue

            if targ_char != "-":
                base = last_num or (template_num[0] if template_num else "X")
                _terminal_insertion_error(base)
                num = f"{base}i{ins_count}"
                aligned_nums.append(num)
                aligned_seqs.append(targ_char)
                aligned_templ.append("-")
                ins_count += 1
            continue

        if idx_template >= len(template_num):
            if targ_char != "-":
                base = last_num or (template_num[-1] if template_num else "X")
                _terminal_insertion_error(base)
                num = f"{base}i{ins_count}"
                aligned_nums.append(num)
                aligned_seqs.append(targ_char)
                aligned_templ.append("-")
                ins_count += 1
            continue

        num = template_num[idx_template]

        if (
            region == "Aminoacyl arm 3' end"
            and _is_terminal_reserved_label(num)
            and targ_char != "-"
        ):
            if num not in tail_canon:
                raise ValueError(
                    "Terminal insertion labels 74i*/75i*/76i* cannot "
                    "replace canonical Sprinzl coordinates 74-76"
                )
            if not target_has_complete_cca:
                raise ValueError(
                    "Incomplete or non-CCA terminal extension cannot occupy "
                    "Sprinzl coordinates 74-76"
                )

        if num.startswith("-"):
            aligned_nums.append(num)
            aligned_seqs.append("-" if targ_char == "-" else targ_char)
            aligned_templ.append(template_seq[idx_template] if idx_template < len(template_seq) else "-")
            last_num = num
            idx_template += 1
            ins_count = 1
            continue

        aligned_nums.append(num)
        last_num = num
        aligned_templ.append(template_seq[idx_template] if idx_template < len(template_seq) else "-")
        idx_template += 1
        ins_count = 1
        aligned_seqs.append("-" if targ_char == "-" else targ_char)

    while idx_template < len(template_num):
        num = template_num[idx_template]
        aligned_nums.append(num)
        aligned_seqs.append("-")
        aligned_templ.append(template_seq[idx_template] if idx_template < len(template_seq) else "-")
        idx_template += 1

    if not validate_alignment(target_seq, aligned_seqs):
        if region == "Aminoacyl arm 3' end":
            fallback = _build_aa3_order_preserving_fallback(
                template_num=template_num,
                template_seq=template_seq,
                target_seq=target_seq,
            )
            if fallback is not None:
                logger.warning(
                    "AA3 DP alignment broke base preservation; using order-preserving fallback projection."
                )
                return fallback
        raise ValueError(
            "DP alignment failed base-preservation check "
            "(set TRNAALIGN_DEBUG_ALIGN=1 for details)"
        )

    return {
        "aligned_numbering": aligned_nums,
        "aligned_sequence": aligned_seqs,
        "aligned_template": aligned_templ,
    }


__all__ = ["align_with_dp"]
