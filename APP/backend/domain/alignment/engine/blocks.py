from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..alignment_types import AlignmentBlock


STANDARD_ALIGNMENT_REGIONS: List[str] = [
    "Aminoacyl arm 5' end",
    "D loop + D stem",
    "Anticodon loop + Anticodon stem",
    "Variable loop",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
]


def clean_struct(s: str) -> List[str]:
    return [ch for ch in (s or "") if not ch.isspace()]


def pair_count_from_struct(s: str) -> int:
    chars = clean_struct(s)
    return min(chars.count(">"), chars.count("<"))


def anchor_from_aligned(nums: List[str]) -> Optional[str]:
    for n in reversed(nums):
        if not n or n.startswith("-") or n.startswith("V"):
            continue
        if "i" in n:
            base = n.split("i", 1)[0]
            return base if base else None
        return n
    return None


def fix_boundary_fill(prev_blk: AlignmentBlock, cur_blk: AlignmentBlock) -> None:
    prev_nums, prev_templ, prev_targ = prev_blk["lines"]
    cur_nums, cur_templ, cur_targ = cur_blk["lines"]
    if not prev_nums or not cur_nums:
        return
    prev_last_num = prev_nums[-1]
    if not prev_last_num or prev_last_num.startswith("-") or prev_last_num.startswith("V"):
        return
    if prev_targ[-1] != "-":
        return
    first_num = cur_nums[0]
    if not first_num or not first_num.startswith(f"{prev_last_num}i"):
        return
    if cur_targ[0] == "-":
        return

    prev_targ[-1] = cur_targ[0]
    cur_nums.pop(0)
    cur_templ.pop(0)
    cur_targ.pop(0)
    base = prev_last_num
    for idx, n in enumerate(cur_nums):
        if not n.startswith(base + "i"):
            break
        suffix = n[len(base) + 1 :]
        if not suffix.isdigit():
            break
        new_i = int(suffix) - 1
        cur_nums[idx] = f"{base}i{1 if new_i <= 0 else new_i}"


def _default_norm_rna_token(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper().replace("T", "U") if ch in {"A", "U", "G", "C", "N"})


def _default_safe_int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def build_global_struct_summary(
    *,
    cmp: Mapping[str, Any],
    regions: Sequence[str],
    ann_meta: Mapping[str, Any],
    empty_regions: Sequence[str],
    normalize_token: Optional[Callable[[str], str]] = None,
    safe_int_value: Optional[Callable[[Any, int], int]] = None,
) -> Dict[str, Any]:
    normalize = normalize_token or _default_norm_rna_token
    safe_int = safe_int_value or _default_safe_int_value
    region_map = (cmp.get("regions") or {})

    region_struct_stats: Dict[str, Dict[str, int]] = {}
    non_variable_pair_count = 0
    for region in regions:
        region_data = region_map.get(region) or {}
        region_seq = str(region_data.get("input_seq", "") or "")
        region_struct = str(region_data.get("input_struct", "") or "")
        pair_count = pair_count_from_struct(region_struct)
        if region != "Variable loop":
            non_variable_pair_count += pair_count
        region_struct_stats[region] = {
            "seq_len": len([ch for ch in region_seq if not ch.isspace()]),
            "struct_len": len(clean_struct(region_struct)),
            "pair_count": pair_count,
        }

    return {
        "annotation_source": str(ann_meta.get("annotation_source", "") or ""),
        "segmentation_status": str(ann_meta.get("segmentation_status", "") or ""),
        "segmentation_errors": str(ann_meta.get("segmentation_errors", "") or ""),
        "trnascan_hit": bool(ann_meta.get("trnascan_hit", True)),
        "trnascan_isotype": str(ann_meta.get("trnascan_isotype", "") or ""),
        "trnascan_anticodon": normalize(str(ann_meta.get("trnascan_anticodon", "") or "")),
        "trnascan_anticodon_start": safe_int(ann_meta.get("trnascan_anticodon_start", 0), 0),
        "trnascan_anticodon_end": safe_int(ann_meta.get("trnascan_anticodon_end", 0), 0),
        "empty_regions": list(empty_regions),
        "boundary_resolved": bool(ann_meta.get("boundary_resolved", False)),
        "boundary_resolver_mode": str(ann_meta.get("boundary_resolver_mode", "") or ""),
        "boundary_before_lengths": list(ann_meta.get("boundary_before_lengths") or []),
        "boundary_after_lengths": list(ann_meta.get("boundary_after_lengths") or []),
        "boundary_template_lengths": list(ann_meta.get("boundary_template_lengths") or []),
        "boundary_deltas": dict(ann_meta.get("boundary_deltas") or {}),
        "non_variable_pair_count": int(non_variable_pair_count),
        "region_struct_stats": region_struct_stats,
    }


def build_alignment_blocks(
    *,
    cmp: Mapping[str, Any],
    regions: Sequence[str],
    global_struct_summary: Mapping[str, Any],
    align_region_fn: Callable[..., Dict[str, List[str]]],
) -> List[AlignmentBlock]:
    region_map = (cmp.get("regions") or {})
    blocks: List[AlignmentBlock] = []
    prev_anchor_num: Optional[str] = None

    for region in regions:
        data = region_map.get(region) or {}
        if not data:
            raise RuntimeError(f"No data for region: {region}")

        tpl_num = list(data.get("template_numbering", []) or [])
        tpl_seq = str(data.get("template_seq", "") or "")
        inp_seq = str(data.get("input_seq", "") or "")
        inp_struct = str(data.get("input_struct", "") or "")

        out = align_region_fn(tpl_num, tpl_seq, inp_seq, region=region, prev_num=prev_anchor_num)
        nums = list(out.get("aligned_numbering", []))
        targ = list(out.get("aligned_sequence", []))
        templ = list(out.get("aligned_template", []))
        if not templ or len(templ) != len(nums):
            templ = ["-"] * len(nums)

        cur_block: AlignmentBlock = {
            "region": region,
            "lines": [nums, templ, targ],
            "meta": {
                "input_struct": inp_struct,
                "global_struct_summary": dict(global_struct_summary),
            },
        }
        if blocks:
            fix_boundary_fill(blocks[-1], cur_block)
        blocks.append(cur_block)
        prev_anchor_num = anchor_from_aligned(nums)

    return blocks
