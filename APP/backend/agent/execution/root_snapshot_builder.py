from __future__ import annotations

import csv
import io
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Tuple

from ..contracts import AlignmentRequest, RootSnapshot
from backend.domain.alignment.engine import (
    STANDARD_ALIGNMENT_REGIONS,
    build_alignment_blocks,
    build_global_struct_summary,
    collect_annotation_context,
    finalize_alignment_rows,
    norm_rna_token,
    postcheck_runtime_overrides,
    safe_int_value,
)
from backend.domain.alignment.engine.regional_dp import align_with_dp
from backend.domain.alignment.preparation.segmentation import compare_input_template
from backend.domain.alignment.presentation.views import build_llm_trace_view, build_structure_view
from backend.domain.alignment.safety.normalization import (
    compact_and_prune_insertion_columns,
    maybe_reanchor_anticodon_slots,
    normalize_blocks_with_rollback,
)
from backend.domain.alignment.safety.validation import validate_alignment


def _parse_csv_alignment(csv_raw: str) -> Tuple[List[str], List[str], List[str]]:
    numbering: List[str] = []
    template: List[str] = []
    sequence: List[str] = []
    try:
        rows = list(csv.reader(io.StringIO(csv_raw or "")))
        if len(rows) >= 3:
            numbering = list(rows[0])
            template = list(rows[1])
            sequence = list(rows[2])
    except Exception:
        return [], [], []
    return numbering, template, sequence


class RootSnapshotBuilder:
    """
    Root builder for the Agent v2 search.

    The root build is the only place where the project still assembles a full
    initial alignment. The produced snapshot carries enough structured state for
    later local tools to keep working without calling the whole pipeline again.
    """

    def build(self, request: AlignmentRequest) -> RootSnapshot:
        cmp = self.compare(request, template_name=request.template_name)
        return self.build_from_compare(
            request=request,
            cmp=cmp,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            snapshot_id="root",
            parent_snapshot_id=None,
            source_action="root_snapshot",
            root_source="standard_pipeline",
            write_output=True,
        )

    def compare(self, request: AlignmentRequest, template_name: str | None = None) -> Dict[str, Any]:
        runtime_overrides = dict(request.runtime_values_override or {})
        template_top_k = runtime_overrides.get("AGENT_TEMPLATE_TOP_K", 5)
        try:
            template_top_k = max(1, min(int(template_top_k), 16))
        except (TypeError, ValueError):
            template_top_k = 5
        cmp = compare_input_template(
            request.target_seq,
            template_name=template_name,
            template_top_k=template_top_k,
            input_type=request.input_type,
            cca_status=request.cca_status,
            domain_hint=request.domain_hint,
            mitochondrial_subtype=request.mitochondrial_subtype,
        ) or {}
        if not cmp:
            if template_name:
                raise ValueError(f"Template not found: {template_name}")
            raise RuntimeError("compare_input_template failed")
        return cmp

    def build_from_compare(
        self,
        *,
        request: AlignmentRequest,
        cmp: Mapping[str, Any],
        use_llm: bool,
        llm_reasoning: bool,
        snapshot_id: str,
        parent_snapshot_id: str | None,
        source_action: str,
        root_source: str,
        write_output: bool,
    ) -> RootSnapshot:
        (
            cmp_local,
            ann_meta,
            warnings,
            runtime_overrides,
            global_struct_summary,
        ) = self.prepare_compare_state(
            request=request,
            cmp=cmp,
            use_llm=use_llm,
            llm_reasoning=llm_reasoning,
        )
        regions = list(STANDARD_ALIGNMENT_REGIONS)
        blocks = build_alignment_blocks(
            cmp=cmp_local,
            regions=regions,
            global_struct_summary=global_struct_summary,
            align_region_fn=align_with_dp,
        )
        return self.snapshot_from_components(
            request=request,
            cmp=cmp_local,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(blocks),
            warnings=warnings,
            runtime_overrides=runtime_overrides,
            global_struct_summary=global_struct_summary,
            use_llm=bool(use_llm),
            llm_reasoning=bool(llm_reasoning),
            snapshot_id=snapshot_id,
            parent_snapshot_id=parent_snapshot_id,
            source_action=source_action,
            root_source=root_source,
            write_output=write_output,
        )

    def prepare_compare_state(
        self,
        *,
        request: AlignmentRequest,
        cmp: Mapping[str, Any],
        use_llm: bool,
        llm_reasoning: bool,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], Dict[str, Any], Dict[str, Any]]:
        cmp_local = deepcopy(dict(cmp or {}))
        template_name = str(cmp_local.get("template_name") or "")
        if not template_name:
            raise RuntimeError("compare_input_template returned no template_name")

        annotation_context = collect_annotation_context(template_name=template_name, cmp=cmp_local)
        ann_meta = dict(annotation_context["ann_meta"])
        warnings: List[str] = list(annotation_context["warnings"])
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

        runtime_overrides = self._runtime_overrides(
            llm_reasoning=llm_reasoning,
            runtime_values_override=request.runtime_values_override,
        )
        regions = list(STANDARD_ALIGNMENT_REGIONS)
        global_struct_summary = build_global_struct_summary(
            cmp=cmp_local,
            regions=regions,
            ann_meta=ann_meta,
            empty_regions=empty_regions,
            normalize_token=norm_rna_token,
            safe_int_value=safe_int_value,
        )
        return cmp_local, ann_meta, warnings, runtime_overrides, global_struct_summary

    def snapshot_from_components(
        self,
        *,
        request: AlignmentRequest,
        cmp: Mapping[str, Any],
        ann_meta: Mapping[str, Any],
        blocks: List[Dict[str, Any]],
        fallback_blocks: List[Dict[str, Any]] | None = None,
        warnings: List[str],
        runtime_overrides: Mapping[str, Any],
        global_struct_summary: Mapping[str, Any],
        use_llm: bool,
        llm_reasoning: bool,
        snapshot_id: str,
        parent_snapshot_id: str | None,
        source_action: str,
        root_source: str,
        write_output: bool = False,
    ) -> RootSnapshot:
        template_name = str(cmp.get("template_name") or "")
        effective_blocks = deepcopy(list(blocks or []))
        effective_use_llm = bool(use_llm)
        fallback_applied = False
        fallback_reason = ""
        base_warnings = list(warnings or [])
        try:
            csv_content, final_warnings, structure_view, llm_trace_view = finalize_alignment_rows(
                blocks=effective_blocks,
                target_seq=request.target_seq,
                ann_meta=ann_meta,
                use_llm=effective_use_llm,
                template_name=template_name,
                status_callback=request.status_callback if write_output else None,
                warnings=list(base_warnings),
                validate_alignment_fn=validate_alignment,
                normalize_blocks_with_rollback_fn=normalize_blocks_with_rollback,
                maybe_reanchor_anticodon_slots_fn=maybe_reanchor_anticodon_slots,
                compact_and_prune_insertion_columns_fn=compact_and_prune_insertion_columns,
                norm_rna_token_fn=norm_rna_token,
                build_structure_view_fn=build_structure_view,
                build_llm_trace_view_fn=build_llm_trace_view,
            )
        except ValueError as exc:
            if not self._should_retry_without_llm_finalization(
                exc=exc,
                use_llm=effective_use_llm,
                blocks=effective_blocks,
                fallback_blocks=fallback_blocks,
            ):
                raise
            fallback_applied = True
            fallback_reason = str(exc)
            effective_blocks = deepcopy(list(fallback_blocks or []))
            effective_use_llm = False
            fallback_warnings = list(base_warnings)
            fallback_warnings.append(
                "LLM final-row rollback applied after insertion-column canonicalization broke base preservation."
            )
            csv_content, final_warnings, structure_view, llm_trace_view = finalize_alignment_rows(
                blocks=effective_blocks,
                target_seq=request.target_seq,
                ann_meta=ann_meta,
                use_llm=False,
                template_name=template_name,
                status_callback=request.status_callback if write_output else None,
                warnings=fallback_warnings,
                validate_alignment_fn=validate_alignment,
                normalize_blocks_with_rollback_fn=normalize_blocks_with_rollback,
                maybe_reanchor_anticodon_slots_fn=maybe_reanchor_anticodon_slots,
                compact_and_prune_insertion_columns_fn=compact_and_prune_insertion_columns,
                norm_rna_token_fn=norm_rna_token,
                build_structure_view_fn=build_structure_view,
                build_llm_trace_view_fn=build_llm_trace_view,
            )
        if write_output and request.output_csv_path:
            with open(request.output_csv_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_content)

        numbering_row, template_row, sequence_row = _parse_csv_alignment(csv_content)
        meta_dict: Dict[str, Any] = {
            "structure_view": dict(structure_view or {}),
            "llm_trace_view": dict(llm_trace_view or {}),
            "annotation_meta": deepcopy(dict(ann_meta or {})),
            "template_score": cmp.get("template_score"),
            "template_margin": cmp.get("template_margin"),
            "template_candidates": deepcopy(list(cmp.get("template_candidates") or [])),
            "template_name": template_name,
            "agent_cmp": deepcopy(dict(cmp or {})),
            "agent_ann_meta": deepcopy(dict(ann_meta or {})),
            "agent_blocks": deepcopy(list(effective_blocks or [])),
            "agent_runtime_overrides": dict(runtime_overrides or {}),
            "agent_global_struct_summary": deepcopy(dict(global_struct_summary or {})),
            "agent_regions": list(STANDARD_ALIGNMENT_REGIONS),
            "agent_target_seq": request.normalized_target_seq(),
            "agent_use_llm": bool(use_llm),
            "agent_effective_use_llm": bool(effective_use_llm),
            "agent_llm_reasoning": bool(llm_reasoning),
            "agent_finalization_fallback": bool(fallback_applied),
            "agent_finalization_fallback_reason": fallback_reason,
        }
        return RootSnapshot(
            template_name=template_name,
            csv_content=str(csv_content or ""),
            warnings=list(final_warnings or []),
            numbering_row=numbering_row,
            template_row=template_row,
            sequence_row=sequence_row,
            structure_view=dict(structure_view or {}),
            llm_trace_view=dict(llm_trace_view or {}),
            raw_meta=meta_dict,
            template_candidates=deepcopy(list(cmp.get("template_candidates") or [])),
            template_score=self._as_float(cmp.get("template_score")),
            template_margin=self._as_float(cmp.get("template_margin")),
            root_source=root_source,
            snapshot_id=snapshot_id,
            parent_snapshot_id=parent_snapshot_id,
            source_action=source_action,
        )

    @staticmethod
    def _runtime_overrides(
        *,
        llm_reasoning: bool,
        runtime_values_override: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        internal_overrides = postcheck_runtime_overrides(
            llm_reasoning=bool(llm_reasoning),
            runtime_values_override=runtime_values_override,
        )
        merged = dict(internal_overrides)
        if isinstance(runtime_values_override, Mapping):
            merged.update(dict(runtime_values_override))
        return merged

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _variable_pair_hint(blocks: List[Dict[str, Any]] | None) -> int | None:
        for blk in list(blocks or []):
            if str((blk or {}).get("region") or "") != "Variable loop":
                continue
            meta = (blk or {}).get("meta") or {}
            if not isinstance(meta, Mapping):
                continue
            runtime_trace = meta.get("llm_runtime_trace")
            if isinstance(runtime_trace, Mapping):
                value = runtime_trace.get("pair_hint")
                try:
                    if value not in (None, ""):
                        return int(value)
                except (TypeError, ValueError):
                    pass
            value = meta.get("pair_hint")
            try:
                if value not in (None, ""):
                    return int(value)
            except (TypeError, ValueError):
                pass
            raw_struct = str(meta.get("input_struct") or meta.get("var_struct") or "")
            if raw_struct:
                gt = raw_struct.count(">")
                lt = raw_struct.count("<")
                if gt == lt:
                    return gt
        return None

    def _should_retry_without_llm_finalization(
        self,
        *,
        exc: Exception,
        use_llm: bool,
        blocks: List[Dict[str, Any]] | None,
        fallback_blocks: List[Dict[str, Any]] | None,
    ) -> bool:
        if not bool(use_llm):
            return False
        if not fallback_blocks:
            return False
        if "Insertion-column canonicalization broke base-preservation check" not in str(exc):
            return False
        return self._variable_pair_hint(blocks) == 0


__all__ = ["RootSnapshotBuilder"]
