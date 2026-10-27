# -*- coding: utf-8 -*-
"""
Prompt replay tracing for admin console.

Given one input sequence, build six alignment blocks (pre-postcheck),
then report per-region prompt status and optional LLM replay results.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from backend.domain.alignment.alignment_blocks import (
    STANDARD_ALIGNMENT_REGIONS,
    build_alignment_blocks,
    build_global_struct_summary,
)
from backend.domain.alignment.align import _align_with_dp
from backend.domain.alignment.preparation.segmentation import compare_input_template
from backend.domain.alignment.postcheck.variable_loop import trace_variable_loop_block
from backend.domain.alignment.postcheck.anticodon import trace_anticodon_block
from backend.domain.alignment.postcheck.d_region import trace_d_region_block
from backend.domain.alignment.postcheck.t_region import trace_t_region_block
from backend.domain.alignment.postcheck.aminoacyl_joint import trace_aminoacyl_joint_blocks

REGION_PROMPT_KEYS: Dict[str, str] = {
    "Aminoacyl arm 5' end": "LLM_AA5_SYSTEM_PROMPT",
    "D loop + D stem": "LLM_D_SYSTEM_PROMPT",
    "Anticodon loop + Anticodon stem": "LLM_ANTICODON_SYSTEM_PROMPT",
    "Variable loop": "LLM_VARLOOP_SYSTEM_PROMPT",
    "T loop + T stem": "LLM_T_SYSTEM_PROMPT",
    "Aminoacyl arm 3' end": "LLM_AA3_SYSTEM_PROMPT",
}

IMPLEMENTED_REGIONS = {
    "Aminoacyl arm 5' end",
    "Variable loop",
    "Anticodon loop + Anticodon stem",
    "D loop + D stem",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
}
DEFAULT_PROMPT_FALLBACK_REGIONS = {
    "Aminoacyl arm 5' end",
    "Anticodon loop + Anticodon stem",
    "D loop + D stem",
    "T loop + T stem",
    "Aminoacyl arm 3' end",
}
def _build_blocks_before_postcheck(
    target_seq: str,
    template_name: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    cmp = compare_input_template(target_seq, template_name=template_name)
    if not cmp:
        if template_name:
            raise ValueError(f"Template not found: {template_name}")
        raise ValueError("compare_input_template failed")

    template = str(cmp.get("template_name") or "")
    ann_meta = cmp.get("annotation_meta", {}) or {}
    empty_count = int(ann_meta.get("empty_count", 0) or 0)
    empty_regions = list(ann_meta.get("empty_regions", []) or [])
    trnascan_hit = bool(ann_meta.get("trnascan_hit", True))
    ann_warnings = [str(w) for w in (ann_meta.get("warnings", []) or []) if str(w).strip()]
    if empty_count > 1:
        extra = ""
        if not trnascan_hit:
            extra = " tRNAscan-SE did not detect a tRNA hit."
        raise ValueError(
            "Annotation failed: sequence does not show a standard cloverleaf structure "
            f"(empty regions: {', '.join(empty_regions) or 'unknown'}).{extra}"
        )

    warnings: List[str] = list(dict.fromkeys(ann_warnings))
    if empty_count == 1:
        warnings.append(
            "Annotation warning: one region is empty "
            f"({empty_regions[0]}). Alignment may be unreliable."
        )

    global_struct_summary = build_global_struct_summary(
        cmp=cmp,
        regions=STANDARD_ALIGNMENT_REGIONS,
        ann_meta=ann_meta,
        empty_regions=empty_regions,
    )
    blocks = build_alignment_blocks(
        cmp=cmp,
        regions=STANDARD_ALIGNMENT_REGIONS,
        global_struct_summary=global_struct_summary,
        align_region_fn=_align_with_dp,
    )

    return template, blocks, warnings


def _runtime_prompt_value(key: str, runtime_values: Dict[str, Any]) -> str:
    raw = runtime_values.get(key)
    if raw is None:
        raw = os.getenv(key, "")
    return str(raw or "")


def _block_summary(block: Dict[str, Any]) -> Dict[str, Any]:
    lines = block.get("lines", []) or []
    if not isinstance(lines, list) or len(lines) != 3:
        return {"columns": 0, "non_gap_target": 0}
    nums = list(lines[0])
    targ = list(lines[2])
    return {
        "columns": len(nums),
        "non_gap_target": sum(1 for x in targ if str(x) != "-"),
    }


def trace_prompt_pipeline(
    sequence: str,
    runtime_values: Dict[str, Any],
    run_llm: bool = True,
    template_name: Optional[str] = None,
) -> Dict[str, Any]:
    seq = "".join(str(sequence or "").split()).upper()
    if not seq:
        raise ValueError("`sequence` is empty.")

    template_used, blocks, warnings = _build_blocks_before_postcheck(
        seq,
        template_name=template_name,
    )

    aa_joint_trace: Optional[Dict[str, Any]] = None
    try:
        aa_joint_trace = trace_aminoacyl_joint_blocks(
            blocks,
            runtime_values=runtime_values,
            run_llm=bool(run_llm),
        )
    except Exception as e:
        aa_joint_trace = {
            "implemented": True,
            "enabled": False,
            "status": "trace_error",
            "message": f"Aminoacyl joint trace failed: {e}",
            "system_prompt": "",
            "user_prompt": "",
            "llm_attempts": [],
            "aa5_target_tokens": None,
            "aa3_target_tokens": None,
        }

    region_reports: List[Dict[str, Any]] = []
    for block in blocks:
        region = str(block.get("region") or "")
        prompt_key = REGION_PROMPT_KEYS.get(region, "")
        system_prompt = _runtime_prompt_value(prompt_key, runtime_values) if prompt_key else ""
        enabled = bool(system_prompt.strip()) or (region in DEFAULT_PROMPT_FALLBACK_REGIONS)
        implemented = region in IMPLEMENTED_REGIONS
        region_report: Dict[str, Any] = {
            "region": region,
            "prompt_key": prompt_key,
            "enabled": enabled,
            "implemented": implemented,
            "block_summary": _block_summary(block),
            "status": "unknown",
            "message": "",
            "system_prompt": system_prompt,
            "user_prompt": "",
            "llm_attempts": [],
            "var_target": None,
        }

        if not enabled:
            region_report["status"] = "skipped_prompt_empty"
            region_report["message"] = "Prompt is empty, region skipped."
            region_reports.append(region_report)
            continue

        if not implemented:
            region_report["status"] = "unimplemented_pass_through"
            region_report["message"] = "Region handler is placeholder (pass-through)."
            region_reports.append(region_report)
            continue

        if region in ("Aminoacyl arm 5' end", "Aminoacyl arm 3' end"):
            traced = dict(aa_joint_trace or {})
            traced["target_tokens"] = (
                traced.get("aa5_target_tokens")
                if region == "Aminoacyl arm 5' end"
                else traced.get("aa3_target_tokens")
            )
            region_report.update(traced)
            region_reports.append(region_report)
            continue

        if region == "Variable loop":
            traced = trace_variable_loop_block(
                block,
                runtime_values=runtime_values,
                run_llm=bool(run_llm),
            )
            region_report.update(traced)
            region_reports.append(region_report)
            continue

        if region == "D loop + D stem":
            traced = trace_d_region_block(
                block,
                runtime_values=runtime_values,
                run_llm=bool(run_llm),
            )
            region_report.update(traced)
            region_reports.append(region_report)
            continue

        if region == "Anticodon loop + Anticodon stem":
            traced = trace_anticodon_block(
                block,
                runtime_values=runtime_values,
                run_llm=bool(run_llm),
            )
            region_report.update(traced)
            region_reports.append(region_report)
            continue

        if region == "T loop + T stem":
            traced = trace_t_region_block(
                block,
                runtime_values=runtime_values,
                run_llm=bool(run_llm),
            )
            region_report.update(traced)
            region_reports.append(region_report)
            continue

        region_report["status"] = "unimplemented_pass_through"
        region_report["message"] = "Region handler is placeholder (pass-through)."
        region_reports.append(region_report)

    return {
        "ok": True,
        "sequence_length": len(seq),
        "template_name": template_used,
        "run_llm": bool(run_llm),
        "warnings": warnings,
        "regions": region_reports,
    }
