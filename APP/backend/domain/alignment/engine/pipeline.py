from __future__ import annotations

import csv
import io
import logging
from copy import deepcopy
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..alignment_types import AlignmentBlock
from ..llm_postcheck import postcheck_alignment
from ..preparation.segmentation import compare_input_template
from ..presentation.views import build_llm_trace_view, build_structure_view
from ..safety.normalization import (
    compact_and_prune_insertion_columns,
    maybe_reanchor_anticodon_slots,
    normalize_blocks_with_rollback,
)
from ..safety.validation import validate_alignment
from .blocks import STANDARD_ALIGNMENT_REGIONS, build_alignment_blocks, build_global_struct_summary
from .boundary_refinement import (
    maybe_refine_anticodon_boundaries,
    norm_rna_token,
    postcheck_runtime_overrides,
    safe_int_value,
)
from .regional_dp import align_with_dp


logger = logging.getLogger(__name__)

AlignmentStatusCallback = Callable[[Dict[str, Any]], None]


def emit_alignment_status(
    status_callback: Optional[AlignmentStatusCallback],
    *,
    stage: str,
    message: str,
    **extra: Any,
) -> None:
    if status_callback is None:
        return
    payload: Dict[str, Any] = {
        "stage": str(stage or "").strip(),
        "message": str(message or "").strip(),
    }
    for key, value in extra.items():
        payload[key] = value
    try:
        status_callback(payload)
    except Exception:
        logger.debug("Alignment status callback failed.", exc_info=True)


def collect_annotation_context(
    *,
    template_name: str,
    cmp: Mapping[str, Any],
) -> Dict[str, Any]:
    ann_meta = dict(cmp.get("annotation_meta", {}) or {})
    has_secondary_structure = bool(str(ann_meta.get("secondary_structure", "") or "").strip())
    annotation_source = str(ann_meta.get("annotation_source", "") or "").strip()
    empty_regions = list(ann_meta.get("empty_regions", []) or [])
    ann_warnings = [str(w) for w in (ann_meta.get("warnings", []) or []) if str(w).strip()]
    trnascan_hit = bool(ann_meta.get("trnascan_hit", True))

    annotation_bits: List[str] = [f"Template {template_name} selected"]
    if has_secondary_structure:
        annotation_bits.append("secondary structure annotation is ready")
    else:
        annotation_bits.append("region annotation is ready")
    if annotation_source:
        annotation_bits.append(f"source={annotation_source}")

    warnings: List[str] = list(dict.fromkeys(ann_warnings))
    if len(empty_regions) == 1:
        warnings.append(
            "Annotation warning: one region is empty "
            f"({empty_regions[0]}). Alignment may be unreliable."
        )

    return {
        "ann_meta": ann_meta,
        "annotation_source": annotation_source,
        "has_secondary_structure": has_secondary_structure,
        "annotation_message": ". ".join([annotation_bits[0], "; ".join(annotation_bits[1:])]),
        "empty_regions": empty_regions,
        "empty_count": int(ann_meta.get("empty_count", 0) or 0),
        "trnascan_hit": trnascan_hit,
        "warnings": warnings,
    }


def maybe_run_region_postcheck(
    *,
    blocks: List[AlignmentBlock],
    blocks_before_postcheck: List[AlignmentBlock],
    use_llm: bool,
    target_seq: str,
    template_name: str,
    llm_reasoning: bool,
    status_callback: Optional[AlignmentStatusCallback],
    runtime_overrides: Mapping[str, Any],
    warnings: List[str],
    validate_alignment_fn: Callable[[str, Sequence[str] | str], bool],
    postcheck_alignment_fn: Callable[..., List[AlignmentBlock]],
) -> List[AlignmentBlock]:
    if not use_llm:
        emit_alignment_status(
            status_callback,
            stage="dp_only",
            message="DP alignment finished. Skipping LLM correction because DP-only mode is selected.",
            template_name=str(template_name or ""),
        )
        return blocks

    emit_alignment_status(
        status_callback,
        stage="llm_postcheck",
        message="Secondary structure is ready. Running LLM postcheck and regional correction.",
        template_name=str(template_name or ""),
        llm_reasoning=bool(llm_reasoning),
    )
    blocks = postcheck_alignment_fn(blocks, runtime_values_override=runtime_overrides)
    postcheck_seqs: List[str] = []
    for blk in blocks:
        _nums, _templ, targ = blk["lines"]
        postcheck_seqs.extend(targ)
    if not validate_alignment_fn(target_seq, postcheck_seqs):
        logger.warning(
            "LLM postcheck broke base preservation; rollback to pre-postcheck DP blocks."
        )
        warnings.append(
            "LLM postcheck rollback applied after breaking base preservation."
        )
        return blocks_before_postcheck
    return blocks


def finalize_alignment_rows(
    *,
    blocks: List[AlignmentBlock],
    target_seq: str,
    ann_meta: Mapping[str, Any],
    use_llm: bool,
    template_name: str,
    status_callback: Optional[AlignmentStatusCallback],
    warnings: List[str],
    validate_alignment_fn: Callable[[str, Sequence[str] | str], bool],
    normalize_blocks_with_rollback_fn: Callable[..., bool],
    maybe_reanchor_anticodon_slots_fn: Callable[..., bool],
    compact_and_prune_insertion_columns_fn: Callable[[List[str], List[str]], bool],
    norm_rna_token_fn: Callable[[str], str],
    build_structure_view_fn: Callable[..., Dict[str, Any]],
    build_llm_trace_view_fn: Callable[..., Dict[str, Any]],
) -> Tuple[str, List[str], Dict[str, Any], Dict[str, Any]]:
    emit_alignment_status(
        status_callback,
        stage="normalizing",
        message="Normalizing cross-block insertions and validating base preservation.",
        template_name=str(template_name or ""),
    )
    if not normalize_blocks_with_rollback_fn(
        blocks=blocks,
        target_seq=target_seq,
        validate_alignment_fn=validate_alignment_fn,
    ):
        logger.warning("Cross-block normalization broke base preservation; rollback to pre-normalization state.")

    full_nums: List[str] = []
    full_templ: List[str] = []
    full_seqs: List[str] = []
    for blk in blocks:
        nums, templ, targ = blk["lines"]
        full_nums.extend(nums)
        full_templ.extend(templ)
        full_seqs.extend(targ)

    if maybe_reanchor_anticodon_slots_fn(
        num_row=full_nums,
        seq_row=full_seqs,
        ann_meta=ann_meta,
        use_llm=bool(use_llm),
        target_seq=target_seq,
        validate_alignment_fn=validate_alignment_fn,
        norm_rna_token_fn=norm_rna_token_fn,
    ):
        warnings.append("LLM-mode anticodon anchor correction applied at positions 34/35/36.")

    emit_alignment_status(
        status_callback,
        stage="finalizing_rows",
        message="Checking anticodon anchors and compacting insertion columns.",
        template_name=str(template_name or ""),
    )
    compaction_backup_nums = list(full_nums)
    compaction_backup_templ = list(full_templ)
    compaction_backup_seqs = list(full_seqs)
    if compact_and_prune_insertion_columns_fn(full_nums, full_seqs, template_row=full_templ):
        if not validate_alignment_fn(target_seq, full_seqs):
            if validate_alignment_fn(target_seq, compaction_backup_seqs):
                full_nums[:] = compaction_backup_nums
                full_templ[:] = compaction_backup_templ
                full_seqs[:] = compaction_backup_seqs
                warnings.append(
                    "Insertion-column canonicalization rollback applied after breaking base preservation."
                )
                logger.warning(
                    "Insertion-column canonicalization broke base preservation; rollback to pre-compaction rows."
                )
            else:
                raise ValueError("Insertion-column canonicalization broke base-preservation check")

    emit_alignment_status(
        status_callback,
        stage="rendering_views",
        message="Assembling structure overlays and final trace metadata.",
        template_name=str(template_name or ""),
    )
    structure_view = build_structure_view_fn(
        num_row=full_nums,
        seq_row=full_seqs,
        ann_meta=ann_meta,
    )
    llm_trace_view = build_llm_trace_view_fn(
        block_rows=blocks,
        template_name=str(template_name or ""),
        run_llm_flag=bool(use_llm),
        target_seq=target_seq,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(full_nums)
    writer.writerow(full_templ)
    writer.writerow(full_seqs)
    csv_content = buffer.getvalue()

    emit_alignment_status(
        status_callback,
        stage="completed",
        message="Alignment rows are finalized and ready to return.",
        template_name=str(template_name or ""),
    )
    return csv_content, warnings, structure_view, llm_trace_view


def run_alignment_pipeline(
    *,
    target_seq: str,
    output_csv_path: str,
    use_llm: bool,
    llm_reasoning: bool,
    template_name: Optional[str],
    return_meta: bool,
    status_callback: Optional[AlignmentStatusCallback],
    runtime_values_override: Optional[Dict[str, Any]] = None,
    compare_input_template_fn: Callable[..., Dict[str, Any]] = compare_input_template,
    build_global_struct_summary_fn: Callable[..., Dict[str, Any]] = build_global_struct_summary,
    build_alignment_blocks_fn: Callable[..., List[AlignmentBlock]] = build_alignment_blocks,
    align_region_fn: Callable[..., Dict[str, List[str]]] = align_with_dp,
    postcheck_alignment_fn: Callable[..., List[AlignmentBlock]] = postcheck_alignment,
    normalize_blocks_with_rollback_fn: Callable[..., bool] = normalize_blocks_with_rollback,
    maybe_reanchor_anticodon_slots_fn: Callable[..., bool] = maybe_reanchor_anticodon_slots,
    compact_and_prune_insertion_columns_fn: Callable[[List[str], List[str]], bool] = compact_and_prune_insertion_columns,
    build_structure_view_fn: Callable[..., Dict[str, Any]] = build_structure_view,
    build_llm_trace_view_fn: Callable[..., Dict[str, Any]] = build_llm_trace_view,
    validate_alignment_fn: Callable[[str, Sequence[str] | str], bool] = validate_alignment,
) -> Tuple[str, str, List[str]] | Tuple[str, str, List[str], Dict[str, Any]]:
    emit_alignment_status(
        status_callback,
        stage="template_matching",
        message="Selecting the closest template and annotating the input sequence.",
        use_llm=bool(use_llm),
    )

    cmp = compare_input_template_fn(target_seq, template_name=template_name)
    if not cmp:
        if template_name:
            raise ValueError(f"Template not found: {template_name}")
        raise RuntimeError("compare_input_template failed")

    template_name = cmp["template_name"]
    annotation_context = collect_annotation_context(template_name=str(template_name or ""), cmp=cmp)
    ann_meta = dict(annotation_context["ann_meta"])
    has_secondary_structure = bool(annotation_context["has_secondary_structure"])
    annotation_source = str(annotation_context["annotation_source"])
    emit_alignment_status(
        status_callback,
        stage="annotation_ready",
        message=str(annotation_context["annotation_message"]),
        template_name=str(template_name or ""),
        annotation_source=annotation_source,
        has_secondary_structure=has_secondary_structure,
    )

    empty_count = int(annotation_context["empty_count"])
    empty_regions = list(annotation_context["empty_regions"])
    trnascan_hit = bool(annotation_context["trnascan_hit"])
    if empty_count > 1:
        extra = ""
        if not trnascan_hit:
            extra = " tRNAscan-SE did not detect a tRNA hit."
        raise ValueError(
            "Annotation failed: sequence does not show a standard cloverleaf structure "
            f"(empty regions: {', '.join(empty_regions) or 'unknown'}).{extra}"
        )

    warnings: List[str] = list(annotation_context["warnings"])
    internal_overrides = postcheck_runtime_overrides(
        llm_reasoning=bool(llm_reasoning),
        runtime_values_override=runtime_values_override,
    )
    runtime_overrides = dict(internal_overrides)
    if isinstance(runtime_values_override, dict):
        runtime_overrides.update(runtime_values_override)
    regions = list(STANDARD_ALIGNMENT_REGIONS)

    if use_llm:
        emit_alignment_status(
            status_callback,
            stage="boundary_refinement",
            message="Running boundary refinement around D, anticodon, and variable regions.",
            template_name=str(template_name or ""),
        )
        maybe_refine_anticodon_boundaries(
            cmp,
            regions,
            runtime_values_override=runtime_overrides,
        )
        ann_meta = cmp.get("annotation_meta", {}) or {}
        if ann_meta.get("boundary_resolved"):
            warnings.append(
                "Deterministic multi-evidence boundary refinement adjusted "
                "D/anticodon/variable cut points."
            )

    emit_alignment_status(
        status_callback,
        stage="dp_alignment",
        message="Building six regional dynamic-programming alignments.",
        region_count=len(regions),
        template_name=str(template_name or ""),
    )
    global_struct_summary = build_global_struct_summary_fn(
        cmp=cmp,
        regions=regions,
        ann_meta=ann_meta,
        empty_regions=empty_regions,
        normalize_token=norm_rna_token,
        safe_int_value=safe_int_value,
    )
    blocks = build_alignment_blocks_fn(
        cmp=cmp,
        regions=regions,
        global_struct_summary=global_struct_summary,
        align_region_fn=align_region_fn,
    )
    blocks_before_postcheck = deepcopy(blocks)

    blocks = maybe_run_region_postcheck(
        blocks=blocks,
        blocks_before_postcheck=blocks_before_postcheck,
        use_llm=bool(use_llm),
        target_seq=target_seq,
        template_name=str(template_name or ""),
        llm_reasoning=bool(llm_reasoning),
        status_callback=status_callback,
        runtime_overrides=runtime_overrides,
        warnings=warnings,
        validate_alignment_fn=validate_alignment_fn,
        postcheck_alignment_fn=postcheck_alignment_fn,
    )

    csv_content, warnings, structure_view, llm_trace_view = finalize_alignment_rows(
        blocks=blocks,
        target_seq=target_seq,
        ann_meta=ann_meta,
        use_llm=bool(use_llm),
        template_name=str(template_name or ""),
        status_callback=status_callback,
        warnings=warnings,
        validate_alignment_fn=validate_alignment_fn,
        normalize_blocks_with_rollback_fn=normalize_blocks_with_rollback_fn,
        maybe_reanchor_anticodon_slots_fn=maybe_reanchor_anticodon_slots_fn,
        compact_and_prune_insertion_columns_fn=compact_and_prune_insertion_columns_fn,
        norm_rna_token_fn=norm_rna_token,
        build_structure_view_fn=build_structure_view_fn,
        build_llm_trace_view_fn=build_llm_trace_view_fn,
    )

    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_content)

    if return_meta:
        return template_name, csv_content, warnings, {
            "structure_view": structure_view,
            "llm_trace_view": llm_trace_view,
        }
    return template_name, csv_content, warnings


__all__ = [
    "AlignmentStatusCallback",
    "collect_annotation_context",
    "emit_alignment_status",
    "finalize_alignment_rows",
    "maybe_run_region_postcheck",
    "run_alignment_pipeline",
]
