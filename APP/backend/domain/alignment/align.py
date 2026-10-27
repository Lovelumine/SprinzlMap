# ==============================================
# File: pythonscript/dp_align.py
# ----------------------------------------------
# Pure DP-based alignment workflow.
# Exposes: perform_full_alignment_dp(...)
# ==============================================
from __future__ import annotations
from typing import Any, List, Dict, Tuple, Optional, Union

from .engine import (
    AlignmentStatusCallback,
    align_with_dp as _engine_align_with_dp,
    maybe_refine_anticodon_boundaries as _engine_maybe_refine_anticodon_boundaries,
    run_alignment_pipeline,
)
from .engine.blocks import build_alignment_blocks, build_global_struct_summary
from .llm_postcheck import postcheck_alignment
from .preparation.segmentation import compare_input_template
from .presentation.views import build_llm_trace_view, build_structure_view
from .safety.normalization import (
    compact_and_prune_insertion_columns,
    maybe_reanchor_anticodon_slots,
    normalize_blocks_with_rollback,
)
from .safety.validation import validate_alignment as _validate_alignment

__all__ = [
    "perform_full_alignment",
    "_align_with_dp",
    "validate_alignment",
]

def validate_alignment(target_seq: str, aligned_seq) -> bool:
    return _validate_alignment(target_seq, aligned_seq)


def _maybe_fix_anticodon_boundaries_in_llm_mode(
    cmp: Dict[str, Any],
    region_order: List[str],
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> None:
    _engine_maybe_refine_anticodon_boundaries(
        cmp,
        region_order,
        runtime_values_override=runtime_values_override,
    )


def _align_with_dp(
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
    return _engine_align_with_dp(
        template_num,
        template_seq,
        target_seq,
        match,
        mismatch,
        gap_open,
        gap_extend,
        region=region,
        prev_num=prev_num,
    )


def perform_full_alignment(
    target_seq: str,
    output_csv_path: str,
    anticode: str = "",
    use_llm: bool = True,
    llm_reasoning: bool = False,
    template_name: str | None = None,
    return_meta: bool = False,
    status_callback: Optional[AlignmentStatusCallback] = None,
    runtime_values_override: Optional[Dict[str, Any]] = None,
) -> Union[Tuple[str, str, List[str]], Tuple[str, str, List[str], Dict[str, Any]]]:
    return run_alignment_pipeline(
        target_seq=target_seq,
        output_csv_path=output_csv_path,
        use_llm=bool(use_llm),
        llm_reasoning=bool(llm_reasoning),
        template_name=template_name,
        return_meta=bool(return_meta),
        status_callback=status_callback,
        runtime_values_override=runtime_values_override,
        compare_input_template_fn=compare_input_template,
        build_global_struct_summary_fn=build_global_struct_summary,
        build_alignment_blocks_fn=build_alignment_blocks,
        align_region_fn=_align_with_dp,
        postcheck_alignment_fn=postcheck_alignment,
        normalize_blocks_with_rollback_fn=normalize_blocks_with_rollback,
        maybe_reanchor_anticodon_slots_fn=maybe_reanchor_anticodon_slots,
        compact_and_prune_insertion_columns_fn=compact_and_prune_insertion_columns,
        build_structure_view_fn=build_structure_view,
        build_llm_trace_view_fn=build_llm_trace_view,
        validate_alignment_fn=validate_alignment,
    )
