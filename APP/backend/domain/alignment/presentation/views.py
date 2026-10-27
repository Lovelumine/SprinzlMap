from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from ..alignment_types import AlignmentBlock


def pair_indices_from_structure(struct_tokens: List[str]) -> List[Tuple[int, int]]:
    stack: List[int] = []
    pairs: List[Tuple[int, int]] = []
    for idx, ch in enumerate(struct_tokens, start=1):
        if ch == ">":
            stack.append(idx)
        elif ch == "<" and stack:
            left = stack.pop()
            pairs.append((left, idx))
    pairs.sort(key=lambda item: (item[0], item[1]))
    return pairs


def build_structure_view(
    *,
    num_row: List[str],
    seq_row: List[str],
    ann_meta: Mapping[str, Any],
) -> Dict[str, Any]:
    struct_raw = str(ann_meta.get("secondary_structure", "") or "")
    struct_tokens = [ch for ch in struct_raw if ch in (">", "<", ".")]
    if not struct_tokens:
        return {}

    residue_to_id: Dict[int, str] = {}
    residue_idx = 0
    for label, base in zip(num_row, seq_row):
        b = str(base or "").upper()
        if b in ("A", "U", "G", "C", "N", "T"):
            residue_idx += 1
            residue_to_id[residue_idx] = str(label or "")

    if residue_idx <= 0:
        return {}

    if len(struct_tokens) != residue_idx:
        min_len = min(len(struct_tokens), residue_idx)
        struct_tokens = struct_tokens[:min_len]

    pair_idx = pair_indices_from_structure(struct_tokens)
    id_pairs: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for left_idx, right_idx in pair_idx:
        left_id = residue_to_id.get(left_idx, "")
        right_id = residue_to_id.get(right_idx, "")
        if not left_id or not right_id or left_id == right_id:
            continue
        key = tuple(sorted((left_id, right_id)))
        if key in seen:
            continue
        seen.add(key)
        id_pairs.append(
            {
                "left_id": left_id,
                "right_id": right_id,
                "left_index": int(left_idx),
                "right_index": int(right_idx),
            }
        )

    return {
        "annotation_source": str(ann_meta.get("annotation_source", "") or ""),
        "secondary_structure": "".join(struct_tokens),
        "residue_count": int(residue_idx),
        "pair_count": int(len(pair_idx)),
        "id_pairs": id_pairs,
    }


def target_sequence_length(raw: str) -> int:
    return sum(1 for ch in str(raw or "").upper() if ch in {"A", "U", "G", "C", "N", "T"})


def build_llm_trace_view(
    *,
    block_rows: List[AlignmentBlock],
    template_name: str,
    run_llm_flag: bool,
    target_seq: str,
) -> Dict[str, Any]:
    regions_trace: List[Dict[str, Any]] = []
    for blk in block_rows:
        region_name = str((blk or {}).get("region") or "")
        meta_obj = (blk or {}).get("meta")
        runtime_trace = None
        if isinstance(meta_obj, dict):
            runtime_trace = meta_obj.get("llm_runtime_trace")
        if isinstance(runtime_trace, dict):
            item = dict(runtime_trace)
        else:
            default_status = "skipped_use_llm_off" if (not run_llm_flag) else "trace_not_recorded"
            default_message = (
                "LLM postcheck was disabled for this request."
                if (not run_llm_flag)
                else "No runtime trace recorded for this region."
            )
            item = {
                "implemented": True,
                "region": region_name,
                "status": default_status,
                "raw_status": default_status,
                "final_status": "pass_through_disabled" if (not run_llm_flag) else "pass_through_no_improvement",
                "message": default_message,
                "run_llm": bool(run_llm_flag),
                "enabled": bool(run_llm_flag),
                "attempts": 0,
                "llm_attempted": False,
                "fallback_used": None,
                "rejection_reasons": [],
                "input_block": None,
                "accepted_candidate": None,
                "output_changed": False,
                "llm_attempts": [],
            }
        if "region" not in item or not str(item.get("region") or "").strip():
            item["region"] = region_name
        if "llm_attempts" not in item or not isinstance(item.get("llm_attempts"), list):
            item["llm_attempts"] = []
        if "attempts" not in item:
            item["attempts"] = len(item.get("llm_attempts") or [])
        if "raw_status" not in item:
            item["raw_status"] = str(item.get("status") or "")
        if "final_status" not in item:
            item["final_status"] = str(item.get("status") or "")
        if "rejection_reasons" not in item or not isinstance(item.get("rejection_reasons"), list):
            item["rejection_reasons"] = []
        regions_trace.append(item)

    return {
        "ok": True,
        "template_name": str(template_name or ""),
        "sequence_length": int(target_sequence_length(target_seq)),
        "run_llm": bool(run_llm_flag),
        "warnings": [],
        "regions": regions_trace,
    }
