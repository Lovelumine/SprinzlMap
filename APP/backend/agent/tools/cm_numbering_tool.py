from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.domain.alignment.input_evidence import assess_input_evidence
from backend.domain.alignment.calibration import DEFAULT_CALIBRATION_ARTIFACT
from backend.domain.alignment.cm_numbering import (
    CmalignNumberingService,
    DEFAULT_MODEL_ROOT,
    NumberedResidue,
    explicit_model_group_for_hint,
)
from backend.domain.alignment.sprinzl_coordinates import label_kind
from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.variable_boundary import assess_variable_bridge


class CMNumberingTool:
    """Build an immutable full-snapshot candidate from numbering CMs."""

    def __init__(self, service: CmalignNumberingService | None = None) -> None:
        self._service_override = service

    def execute(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
    ) -> RootSnapshot:
        service = self._service(request.runtime_values_override or {})
        tool_provenance = service.provenance()
        params = dict(action.params or {})
        model_group = str(params.get("model_group") or "").strip().lower()
        domain_hint = str(params.get("domain_hint") or request.domain_hint or "").strip()
        input_evidence = assess_input_evidence(
            target_seq=request.target_seq,
            input_type=request.input_type,
            domain_hint=request.domain_hint,
            cca_status=request.cca_status,
            intron_status=request.intron_status,
            trna_class=request.trna_class,
        )
        full_sequence = request.normalized_target_seq().replace("T", "U")
        project_terminal_cca = bool(
            input_evidence["cca_evidence"]["coordinate_assignment_allowed"]
        )
        cm_sequence = full_sequence[:-3] if project_terminal_cca else full_sequence
        if model_group:
            result = service.align(
                cm_sequence,
                model_group=model_group,
                sequence_id=str(params.get("sequence_id") or "agent-query"),
            )
            selection_mode = "explicit_model_group"
        else:
            explicit_group = explicit_model_group_for_hint(domain_hint)
            portfolio = service.align_portfolio(
                cm_sequence,
                sequence_id=str(params.get("sequence_id") or "agent-query"),
                domain_hint=domain_hint,
                model_groups=(explicit_group,) if explicit_group else ("arch", "bact", "euk", "all"),
            )
            result = portfolio.selected
            selection_mode = "portfolio"

        if project_terminal_cca:
            self._append_confirmed_terminal_cca(result, full_sequence)
        else:
            self._reassign_non_cca_terminal_extensions(result, full_sequence)

        meta = result.metadata()
        variable_bridge_evidence = assess_variable_bridge(
            result.labels,
            result.bases,
            require_t_stem_anchor=True,
        )
        parent_meta = dict(parent_snapshot.raw_meta or {})
        annotation_meta = dict(parent_meta.get("annotation_meta") or {})
        annotation_meta.update(
            {
                "segmentation_status": "cm_consensus_projection",
                "cm_structure_pair_count": len(result.structure_pairs),
            }
        )
        meta.update(
            {
                "annotation_meta": annotation_meta,
                "structural_region_evidence": self._region_evidence(result),
                "variable_bridge_evidence": variable_bridge_evidence,
                "cm_tool": {
                    "tool_name": "align_numbering_cm",
                    "tool_provenance": tool_provenance,
                    "selection_mode": selection_mode,
                    "input_contract": {
                        "alphabet": self._alphabet(request.target_seq),
                        "input_type": str(request.input_type or "unknown"),
                        "domain_hint": domain_hint,
                        "cca_status": str(request.cca_status or "unknown"),
                        "intron_status": str(request.intron_status or "unknown"),
                        "input_evidence": input_evidence,
                    },
                    "trace_events": list(result.trace_events),
                },
            }
        )
        if str(variable_bridge_evidence.get("status") or "") in {
            "conflict",
            "requires_review",
        }:
            meta["cm_tool"]["trace_events"].append(
                {
                    "stage": "deterministic_validation",
                    "validator": "sprinzl_variable_bridge",
                    "status": str(variable_bridge_evidence.get("status") or ""),
                    "observed_labels": list(
                        variable_bridge_evidence.get("observed_labels") or []
                    ),
                    "expected_labels": list(
                        variable_bridge_evidence.get("expected_labels") or []
                    ),
                    "answer_free": True,
                    "gold_coordinates_used": False,
                }
            )
        # CM model names (for example arch-num) are not database template
        # seqnames. Resolve a concrete tRNAs_flat reference separately for
        # export/UI consumers while retaining the CM model in numbering_model.
        from backend.domain.alignment.preparation.template_retrieval import find_best_match

        reference_template = find_best_match(full_sequence)
        reference_template_name = str((reference_template or {}).get("seqname") or "")
        if reference_template_name:
            meta["reference_template_name"] = reference_template_name
            meta["reference_template_match"] = {
                "seqname": reference_template_name,
                "score": reference_template.get("score"),
                "isotype": reference_template.get("template_isotype", ""),
                "anticodon": reference_template.get("template_anticodon", ""),
            }
        margin = result.portfolio_evidence.get("raw_bit_score_margin")
        return RootSnapshot(
            template_name=result.model.name,
            csv_content=result.csv_content(),
            warnings=list(result.warnings),
            numbering_row=list(result.labels),
            template_row=["-"] * len(result.residues),
            sequence_row=list(result.bases),
            structure_view={"id_pairs": list(result.structure_pairs)},
            raw_meta=meta,
            template_candidates=[],
            template_score=result.score.bit_score,
            template_margin=float(margin) if margin is not None else None,
            root_source="cm_tool",
            source_action="align_numbering_cm",
        )

    @staticmethod
    def _append_confirmed_terminal_cca(result, full_sequence: str) -> None:
        if not full_sequence.endswith("CCA"):
            raise ValueError("Confirmed mature CCA is not present at the input 3' end")
        if any(residue.label in {"74", "75", "76"} for residue in result.residues):
            raise RuntimeError("CM body alignment occupied mature CCA coordinates before tail projection")
        alignment_column = max(
            (int(residue.alignment_column) for residue in result.residues),
            default=0,
        )
        for offset, (base, label) in enumerate(zip("CCA", ("74", "75", "76")), start=1):
            result.residues.append(
                NumberedResidue(
                    residue_index=len(result.residues) + 1,
                    base=base,
                    label=label,
                    label_kind=label_kind(label),
                    alignment_column=alignment_column + offset,
                    posterior_code="",
                )
            )
        result.input_sequence = full_sequence
        result.aligned_sequence = f"{result.aligned_sequence}CCA"
        result.consensus_structure = f"{result.consensus_structure}..."
        result.reference_annotation = f"{result.reference_annotation}..."
        result.warnings.append("confirmed_terminal_cca_projected_to_74_76")
        result.trace_events.append(
            {
                "stage": "cca_projection",
                "tool": "deterministic_terminal_cca_projection",
                "labels": ["74", "75", "76"],
                "basis": "confirmed_input_contract",
            }
        )
        result.portfolio_evidence["cca_projection"] = {
            "applied": True,
            "labels": ["74", "75", "76"],
            "basis": "confirmed_input_contract",
        }

    @staticmethod
    def _reassign_non_cca_terminal_extensions(result, full_sequence: str) -> None:
        """Keep non-CCA terminal residues out of the 74-76 CCA block.

        A CM may place a partial or non-CCA suffix in canonical tail columns
        because those columns are present in the reference model. Such a
        placement is not a mature CCA annotation. When the candidate has an
        occupied 73 parent, retain residue order and represent the suffix as
        73i1, 73i2, ... instead. Complete terminal CCA is handled separately
        by ``_append_confirmed_terminal_cca``.
        """
        normalized = str(full_sequence or "").upper().replace("T", "U")
        if normalized.endswith("CCA"):
            return

        terminal_label_re = re.compile(r"^(?:74|75|76)(?:i\d+)?$")
        occupied_terminal = [
            residue
            for residue in list(result.residues)
            if terminal_label_re.fullmatch(str(residue.label or ""))
        ]
        if not occupied_terminal:
            return
        if not any(str(residue.label or "") == "73" for residue in result.residues):
            # Without an occupied 73 parent, inventing a terminal insertion
            # would be less defensible than retaining the candidate for review.
            return

        next_index = 1
        for residue in result.residues:
            match = re.fullmatch(r"73i(\d+)", str(residue.label or ""))
            if match:
                next_index = max(next_index, int(match.group(1)) + 1)

        label_map: dict[str, str] = {}
        updated = []
        for residue in result.residues:
            old_label = str(residue.label or "")
            if terminal_label_re.fullmatch(old_label):
                new_label = f"73i{next_index}"
                next_index += 1
                label_map[old_label] = new_label
                updated.append(
                    NumberedResidue(
                        residue_index=residue.residue_index,
                        base=residue.base,
                        label=new_label,
                        label_kind=label_kind(new_label),
                        alignment_column=residue.alignment_column,
                        posterior_code=residue.posterior_code,
                    )
                )
            else:
                updated.append(residue)
        result.residues = updated

        if label_map:
            for pair in result.structure_pairs:
                if pair.get("left_id") in label_map:
                    pair["left_id"] = label_map[pair["left_id"]]
                if pair.get("right_id") in label_map:
                    pair["right_id"] = label_map[pair["right_id"]]
            result.warnings.append("non_cca_terminal_extension_reassigned_after_73")
            result.trace_events.append(
                {
                    "stage": "terminal_coordinate_policy",
                    "tool": "deterministic_terminal_extension_reassignment",
                    "from_labels": list(label_map.keys()),
                    "to_labels": list(label_map.values()),
                    "basis": "non_complete_terminal_cca",
                }
            )
            result.portfolio_evidence["terminal_extension_reassignment"] = {
                "applied": True,
                "from_labels": list(label_map.keys()),
                "to_labels": list(label_map.values()),
                "basis": "non_complete_terminal_cca",
            }
            result.portfolio_evidence["terminal_coordinate_review"] = {
                "required": True,
                "disposition": "investigate",
                "reason": "non_complete_terminal_cca_represented_after_73",
            }

    def _service(self, runtime_values: Mapping[str, Any]) -> CmalignNumberingService:
        if self._service_override is not None:
            return self._service_override
        raw_model_root = str(runtime_values.get("CM_MODEL_ROOT") or "").strip()
        model_root = Path(raw_model_root) if raw_model_root else DEFAULT_MODEL_ROOT
        executable = str(runtime_values.get("CMALIGN_EXECUTABLE") or "cmalign")
        raw_calibration = str(runtime_values.get("CM_CALIBRATION_ARTIFACT") or "").strip()
        try:
            cpu = max(1, int(runtime_values.get("CMALIGN_CPU") or 1))
        except (TypeError, ValueError):
            cpu = 1
        return CmalignNumberingService(
            model_root=model_root,
            executable=executable,
            cpu=cpu,
            calibration_artifact=(
                Path(raw_calibration) if raw_calibration else DEFAULT_CALIBRATION_ARTIFACT
            ),
        )

    @staticmethod
    def _alphabet(sequence: str) -> str:
        value = "".join(str(sequence or "").upper().split())
        has_t = "T" in value
        has_u = "U" in value
        if has_t and has_u:
            return "mixed"
        if has_t:
            return "DNA"
        if has_u:
            return "RNA"
        return "unknown"

    @staticmethod
    def _region_evidence(result) -> dict[str, Any]:
        counts: dict[str, int] = {}
        labels: dict[str, list[str]] = {}
        for residue in list(result.residues or []):
            try:
                region = number_to_region(str(residue.label or ""))
            except ValueError:
                region = "Unknown"
            counts[region] = counts.get(region, 0) + 1
            labels.setdefault(region, []).append(str(residue.label or ""))
        pair_counts: dict[str, int] = {}
        for pair in list(result.structure_pairs or []):
            left = str((pair or {}).get("left_id") or "")
            try:
                region = number_to_region(left)
            except ValueError:
                region = "Unknown"
            pair_counts[region] = pair_counts.get(region, 0) + 1
        return {
            "source": "numbering_cm_consensus_structure",
            "region_residue_counts": counts,
            "region_pair_counts": pair_counts,
            "region_labels": labels,
        }


__all__ = ["CMNumberingTool"]
