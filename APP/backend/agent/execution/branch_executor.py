from __future__ import annotations

import csv
import hashlib
import io
import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from ..contracts import AgentWorkState, AlignmentRequest, CandidateAction, RootSnapshot
from ..evaluation.acceptor_tail_reconciliation import reconcile_acceptor_tail
from ..evaluation.variable_boundary_reconciliation import (
    reconcile_variable_boundary,
)
from ..tools import CMNumberingTool
from .root_snapshot_builder import RootSnapshotBuilder
from backend.domain.alignment.engine import (
    STANDARD_ALIGNMENT_REGIONS,
    build_global_struct_summary,
    maybe_refine_anticodon_boundaries,
    maybe_run_region_postcheck,
    required_alignment_backend_provenance,
    safe_int_value,
)
from backend.domain.alignment.engine.blocks import anchor_from_aligned, fix_boundary_fill
from backend.domain.alignment.engine.regional_dp import align_with_dp
from backend.domain.alignment.engine.boundary_refinement import norm_rna_token
from backend.domain.alignment.preparation.template_retrieval import find_top_matches
from backend.domain.alignment.llm_postcheck import postcheck_alignment
from backend.domain.alignment.postcheck.variable_loop import rebuild_variable_block_topology
from backend.domain.alignment.safety.normalization import (
    compact_and_prune_insertion_columns,
    maybe_reanchor_anticodon_slots,
)
from backend.domain.alignment.safety.validation import validate_alignment
from backend.domain.alignment.sprinzl_coordinates import label_kind
from backend.domain.alignment import coordinate_proposal
from backend.domain.alignment.variable_boundary import assess_variable_bridge
from backend.domain.literature import agent_lookup
from backend.domain.common.trnascan_runtime import run_trnascan_directly


class BranchExecutor:
    """
    Executes one typed branch as a local tool action.

    After the root snapshot is initialized, later actions are required to work
    off the explicit AgentWorkState carried by the service loop instead of
    recovering execution state from snapshot.raw_meta.
    """

    _ABSTAIN_ACTIONS = {
        "route_to_degenerate_mt_handler",
        "route_to_pseudogene_filter",
        "route_to_out_of_scope_handler",
    }
    _LOCAL_TEMPLATE_ACTIONS = {
        "switch_template_topk",
        "switch_template_family_constrained",
        "switch_template_domain_or_clade_specific",
        "route_to_mitochondrial_model",
        "route_to_archaeal_ncintron_mode",
    }
    _LOCAL_POSTCHECK_ACTIONS = {
        "repair_region_with_structure_first_policy",
        "repair_region_with_template_soft_prior",
        "switch_postcheck_mode",
    }

    def __init__(
        self,
        root_builder: RootSnapshotBuilder | None = None,
        cm_numbering_tool: CMNumberingTool | None = None,
    ) -> None:
        self.root_builder = root_builder or RootSnapshotBuilder()
        self.cm_numbering_tool = cm_numbering_tool or CMNumberingTool()

    @staticmethod
    def _scientific_tool_domain_routing(
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
    ) -> dict[str, Any]:
        """Carry accepted CM domain evidence into downstream structure tools.

        Explicit biological source hints take priority. Raw CM scores or an
        abstaining selector cannot supply a missing source hint.
        """
        requested = str(request.domain_hint or "").strip()
        routing: dict[str, Any] = {
            "requested_domain_hint": request.domain_hint,
            "effective_domain_hint": request.domain_hint,
            "source": "request" if requested.lower() not in {"", "unknown"} else "unknown",
        }
        if requested.lower() not in {"", "unknown"}:
            return routing

        meta = parent_snapshot.raw_meta or {}
        portfolio = dict(meta.get("cm_portfolio") or {})
        calibration = dict(portfolio.get("calibration") or {})
        model = dict(meta.get("numbering_model") or {})
        group = portfolio.get("selected_group")
        domain = {"arch": "archaea", "bact": "bacteria", "euk": "eukaryota"}.get(group)
        if (
            meta.get("numbering_backend") == "cmalign"
            and portfolio.get("selection_policy") == "calibrated_domain_selector"
            and calibration.get("status") == "calibrated"
            and calibration.get("disposition") == "accepted"
            and group == calibration.get("predicted_group") == model.get("group")
            and domain is not None
        ):
            routing.update(
                effective_domain_hint=domain,
                source="cm_calibrated_domain_selector",
                selected_group=group,
                confidence=calibration.get("confidence"),
            )
        return routing

    def execute(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState | None = None,
    ) -> RootSnapshot:
        if action.action_type in self._ABSTAIN_ACTIONS:
            snapshot = self._copy_snapshot(parent_snapshot)
            notes = [self._mark_abstention(snapshot, action.action_type)]
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot

        notes: list[str] = []
        child_blocks: List[Dict[str, Any]] | None = None
        action_type = action.action_type
        if action_type == "propose_coordinate_revision":
            return self._execute_coordinate_proposal(
                request=request,
                parent_snapshot=parent_snapshot,
                action=action,
                branch_id=branch_id,
                work_state=work_state,
            )
        if action_type == "align_numbering_cm":
            snapshot = self.cm_numbering_tool.execute(
                request=request,
                parent_snapshot=parent_snapshot,
                action=action,
            )
            notes.append("cm_numbering_candidate_generated")
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        if action_type == "align_legacy_reference":
            runtime_values_override = dict(request.runtime_values_override or {})
            raw_template_top_k = dict(action.params or {}).get("template_top_k")
            if raw_template_top_k is not None:
                try:
                    runtime_values_override["AGENT_TEMPLATE_TOP_K"] = max(
                        1,
                        min(int(raw_template_top_k), 16),
                    )
                except (TypeError, ValueError):
                    pass
            requested_template = str(
                dict(action.params or {}).get("template_name")
                or request.template_name
                or ""
            ).strip()
            domain_routing = self._scientific_tool_domain_routing(request, parent_snapshot)
            legacy_request = AlignmentRequest(
                target_seq=request.target_seq,
                output_csv_path="/dev/null",
                anticode=request.anticode,
                use_llm=False,
                llm_reasoning=False,
                template_name=requested_template or None,
                input_type=request.input_type,
                domain_hint=domain_routing["effective_domain_hint"],
                cca_status=request.cca_status,
                intron_status=request.intron_status,
                trna_class=request.trna_class,
                mitochondrial_subtype=request.mitochondrial_subtype,
                enable_root_recovery=False,
                return_meta=True,
                runtime_values_override=runtime_values_override,
                status_callback=None,
            )
            snapshot = self.root_builder.build(legacy_request)
            snapshot.root_source = "legacy_reference_tool"
            snapshot.raw_meta = dict(snapshot.raw_meta or {})
            snapshot.raw_meta["numbering_backend"] = "template_regional_dp"
            snapshot.raw_meta["legacy_reference_tool"] = {
                "tool_name": "align_legacy_reference",
                "role": "template_segmentation_and_regional_dp_evidence",
                "deterministic": True,
                "llm_used": False,
                "domain_routing": domain_routing,
                "alignment_backend": required_alignment_backend_provenance(),
                "parent_cm_model": dict(
                    (parent_snapshot.raw_meta or {}).get("numbering_model") or {}
                ),
                "parent_cm_portfolio": dict(
                    (parent_snapshot.raw_meta or {}).get("cm_portfolio") or {}
                ),
            }
            if requested_template:
                snapshot.raw_meta["legacy_reference_tool"][
                    "requested_template"
                ] = requested_template
            snapshot.raw_meta["canonical_strategy"] = {
                "strategy": "agent_toolbox",
                "selected_tool": "align_legacy_reference",
                "selection_reason": str(action.reason or "agent_selected_tool"),
            }
            notes.append("legacy_reference_candidate_generated")
            if requested_template:
                notes.append(f"legacy_reference_template={requested_template}")
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        if action_type == "repair_acceptor_tail_with_structure":
            snapshot = self._execute_acceptor_tail_reconciliation(
                request,
                parent_snapshot,
                notes,
            )
            snapshot = self._enforce_action_contract(
                parent_snapshot,
                snapshot,
                action,
                {"blocks": []},
                notes,
                child_blocks=[],
            )
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        if action_type == "reconcile_variable_boundary_with_structure":
            snapshot = self._execute_variable_boundary_reconciliation(
                request,
                parent_snapshot,
                notes,
            )
            snapshot = self._enforce_action_contract(
                parent_snapshot,
                snapshot,
                action,
                {"blocks": []},
                notes,
                child_blocks=[],
            )
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        state = self._restore_parent_state(request, parent_snapshot, work_state=work_state)
        if action_type == "compact_insertions":
            if state is None:
                raise ValueError("compact_insertions requires explicit AgentWorkState")
            snapshot = self._copy_snapshot(parent_snapshot)
            self._apply_compaction(request, snapshot, notes)
            child_blocks = self._sync_row_level_blocks(snapshot, state["blocks"])
            snapshot = self._enforce_action_contract(parent_snapshot, snapshot, action, state, notes, child_blocks=child_blocks)
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        if action_type == "reanchor_anticodon":
            if state is None:
                raise ValueError("reanchor_anticodon requires explicit AgentWorkState")
            snapshot = self._copy_snapshot(parent_snapshot)
            self._apply_reanchor(request, snapshot, state["ann_meta"], notes)
            child_blocks = self._sync_row_level_blocks(snapshot, state["blocks"])
            snapshot = self._enforce_action_contract(parent_snapshot, snapshot, action, state, notes, child_blocks=child_blocks)
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        if action_type in self._LOCAL_TEMPLATE_ACTIONS:
            if state is None:
                raise ValueError(f"{action_type} requires explicit AgentWorkState")
            snapshot, child_blocks = self._execute_local_template_switch(request, parent_snapshot, action, state, notes)
            snapshot = self._enforce_action_contract(parent_snapshot, snapshot, action, state, notes, child_blocks=child_blocks)
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot

        if state is None:
            raise ValueError(f"{action_type} requires explicit AgentWorkState")

        if action_type == "refine_boundary":
            snapshot, child_blocks = self._execute_boundary_refinement(request, parent_snapshot, action, state, notes)
        elif action_type in self._LOCAL_POSTCHECK_ACTIONS:
            snapshot, child_blocks = self._execute_region_postcheck(request, parent_snapshot, action, state, notes)
        elif action_type == "repair_variable_topology_locally":
            snapshot, child_blocks = self._execute_variable_topology_repair(request, parent_snapshot, action, state, notes)
        elif action_type == "repair_region_with_dp_only":
            snapshot, child_blocks = self._execute_region_dp_repair(
                request,
                parent_snapshot,
                action,
                state,
                notes,
                use_llm=False,
            )
        elif action_type == "route_to_truncated_candidate_mode":
            snapshot, child_blocks = self._execute_region_dp_repair(
                request,
                parent_snapshot,
                action,
                state,
                notes,
                use_llm=False,
            )
            self._apply_compaction(request, snapshot, notes)
            self._apply_reanchor(request, snapshot, state["ann_meta"], notes)
            child_blocks = self._sync_row_level_blocks(snapshot, child_blocks or state["blocks"])
        else:
            snapshot = self._copy_snapshot(parent_snapshot)
            child_blocks = deepcopy(state["blocks"])
            notes.append("unsupported_local_action_pass_through")
        snapshot = self._enforce_action_contract(parent_snapshot, snapshot, action, state, notes, child_blocks=child_blocks)
        self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
        return snapshot

    def _execute_coordinate_proposal(
        self,
        *,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        work_state: AgentWorkState | None,
    ) -> RootSnapshot:
        """Ask the model for a row, then judge it like any other candidate.

        Nothing here decides whether the proposal is right.  It builds a
        snapshot, and ``hard_guards`` plus the online ranker, which already
        reject a bad cmalign or DP candidate, decide that.
        """
        notes: list[str] = []
        snapshot = self._copy_snapshot(parent_snapshot)
        regions = self._declared_regions(action)
        observation_notes: list[str] = []
        prior_violations: list[str] = []
        if work_state is not None:
            latest_lookup = (
                dict(list(work_state.literature_findings or [])[-1] or {})
                if list(work_state.literature_findings or [])
                else {}
            )
            observation_notes = agent_lookup.as_prompt_notes(
                list(latest_lookup.get("citations") or [])
            )
            for attempt in list(work_state.coordinate_proposal_attempts or []):
                for code in list(dict(attempt or {}).get("violations") or []):
                    text = str(code or "")
                    if text and text not in prior_violations:
                        prior_violations.append(text)
        result = coordinate_proposal.propose_row(
            runtime_values_override=dict(request.runtime_values_override or {}),
            sequence_row=parent_snapshot.sequence_row,
            numbering_row=parent_snapshot.numbering_row,
            allowed_regions=regions,
            structure_pairs=list(
                dict(parent_snapshot.structure_view or {}).get("id_pairs") or []
            ),
            validation={
                "valid": bool(getattr(work_state, "current_valid", False)),
                "reasons": list(getattr(work_state, "current_reasons", []) or []),
                "issues": list(getattr(work_state, "issues", []) or []),
            },
            reference_notes=observation_notes,
            prior_violations=prior_violations,
        )
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        snapshot.raw_meta["coordinate_proposal"] = result.as_meta()
        if not result.labels:
            notes.append(f"coordinate_proposal_{result.status}")
            if result.error:
                notes.append(f"coordinate_proposal_error={result.error}")
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot
        snapshot.numbering_row = list(result.labels)
        self._render_csv(snapshot)
        blocks = self._sync_row_level_blocks(
            snapshot, list(dict(parent_snapshot.raw_meta or {}).get("agent_blocks") or [])
        )
        notes.append(f"coordinate_proposal_changed={len(result.changed)}")
        self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
        return self._enforce_action_contract(
            parent_snapshot,
            snapshot,
            action,
            {
                "cmp": {},
                "ann_meta": {},
                "blocks": blocks,
                "runtime_overrides": dict(request.runtime_values_override or {}),
            },
            notes,
            child_blocks=blocks,
        )

    def _execute_local_template_switch(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        state: Dict[str, Any],
        notes: list[str],
    ) -> tuple[RootSnapshot, List[Dict[str, Any]]]:
        template_name = self._resolve_template_name(request, parent_snapshot, action, state=state)
        if not template_name:
            snapshot = self._copy_snapshot(parent_snapshot)
            notes.append("template_switch_candidate_missing")
            return snapshot, deepcopy(state["blocks"])
        if template_name == parent_snapshot.template_name:
            notes.append("template_switch_fell_back_to_parent")
        cmp = self.root_builder.compare(request, template_name=template_name)
        if action.action_type == "route_to_archaeal_ncintron_mode":
            notes.append("archaeal_route_local_template_switch")
        elif action.action_type == "route_to_mitochondrial_model":
            notes.append("mitochondrial_route_local_template_switch")
        (
            cmp_local,
            ann_meta,
            warnings,
            runtime_overrides,
            global_struct_summary,
        ) = self.root_builder.prepare_compare_state(
            request=request,
            cmp=cmp,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
        )
        focus_regions = self._template_switch_regions(parent_cmp=state["cmp"], candidate_cmp=cmp_local)
        blocks, global_struct_summary = self._recompute_regions(
            cmp=cmp_local,
            ann_meta=ann_meta,
            existing_blocks=state["blocks"],
            regions=focus_regions,
        )
        notes.append(f"template_switch_regions={focus_regions}")
        notes.append("template_switch_candidate_is_deterministic")
        snapshot = self.root_builder.snapshot_from_components(
            request=request,
            cmp=cmp_local,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(state["blocks"]),
            warnings=warnings,
            runtime_overrides=runtime_overrides,
            global_struct_summary=global_struct_summary,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            snapshot_id=parent_snapshot.snapshot_id,
            parent_snapshot_id=parent_snapshot.parent_snapshot_id,
            source_action=action.action_type,
            root_source="local_template_switch",
            write_output=False,
        )
        return snapshot, blocks

    def _execute_acceptor_tail_reconciliation(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        notes: list[str],
    ) -> RootSnapshot:
        snapshot = self._copy_snapshot(parent_snapshot)
        occupied_labels: List[str] = []
        occupied_bases: List[str] = []
        occupied_columns: List[int] = []
        for column, (label, base) in enumerate(
            zip(parent_snapshot.numbering_row, parent_snapshot.sequence_row)
        ):
            normalized = str(base or "").strip().upper().replace("T", "U")
            if normalized not in {"A", "C", "G", "U", "N"}:
                continue
            occupied_columns.append(column)
            occupied_labels.append(str(label or "").strip())
            occupied_bases.append(normalized)

        domain_routing = self._scientific_tool_domain_routing(request, parent_snapshot)
        try:
            scan = run_trnascan_directly(
                request.normalized_target_seq(),
                seq_id="acceptor_tail_check",
                domain_hint=domain_routing["effective_domain_hint"],
                mitochondrial_subtype=request.mitochondrial_subtype,
            )
            result = reconcile_acceptor_tail(
                target_sequence=request.normalized_target_seq(),
                current_labels=occupied_labels,
                current_bases=occupied_bases,
                struct_file_sequence=scan.struct_file_sequence,
                secondary_structure=scan.secondary_structure,
                trna_type=scan.type,
            )
        except Exception as exc:
            result = {
                "resolved": False,
                "reason": "trnascan_acceptor_endpoint_tool_failed",
                "error_type": type(exc).__name__,
            }
            scan = None

        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        audit = {
            "schema_version": 1,
            "policy": "trnascan_acceptor_stem_endpoint_v1",
            "tool_name": "tRNAscan-SE",
            "role": "acceptor_stem_endpoint_and_discriminator_evidence",
            "domain_routing": domain_routing,
            "deterministic": True,
            "llm_used": False,
            "answer_free": True,
            "gold_coordinates_used": False,
            "status": "resolved" if bool(result.get("resolved")) else "unresolved",
            "result": dict(result),
        }
        if scan is not None:
            audit["tool_observation"] = {
                "isotype": str(scan.type or ""),
                "anticodon": str(scan.anticodon or ""),
                "structure_length": len(str(scan.secondary_structure or "")),
                "structure_sha256": hashlib.sha256(
                    (
                        str(scan.struct_file_sequence or "")
                        + "\n"
                        + str(scan.secondary_structure or "")
                    ).encode("utf-8")
                ).hexdigest(),
                "search_mode": str(getattr(scan, "search_mode", "") or ""),
                "domain_hint": str(getattr(scan, "domain_hint", "") or ""),
                "mitochondrial_subtype": str(
                    getattr(scan, "mitochondrial_subtype", "") or ""
                ),
                "command_options": list(getattr(scan, "command_options", ()) or ()),
                "executable": str(getattr(scan, "executable", "") or ""),
                "version": str(getattr(scan, "version", "") or ""),
            }
        snapshot.raw_meta["acceptor_tail_reconciliation"] = audit

        if not bool(result.get("resolved")):
            notes.append(
                "acceptor_tail_reconciliation_unresolved:"
                + str(result.get("reason") or "unknown")
            )
            return snapshot

        candidate_labels = [
            str(value or "").strip()
            for value in list(result.get("candidate_labels") or [])
        ]
        if len(candidate_labels) != len(occupied_columns):
            audit["status"] = "unresolved"
            audit["result"] = {
                "resolved": False,
                "reason": "candidate_and_occupied_column_count_mismatch",
            }
            notes.append("acceptor_tail_reconciliation_unresolved:column_count")
            return snapshot

        for residue_index, column in enumerate(occupied_columns):
            snapshot.numbering_row[column] = candidate_labels[residue_index]

        provenance = []
        for item in list(snapshot.raw_meta.get("position_provenance") or []):
            record = dict(item or {})
            try:
                residue_index = int(record.get("residue_index") or 0)
            except (TypeError, ValueError):
                residue_index = 0
            if 1 <= residue_index <= len(candidate_labels):
                new_label = candidate_labels[residue_index - 1]
                if new_label != str(record.get("label") or ""):
                    record["source_label"] = str(record.get("label") or "")
                    record["label"] = new_label
                    record["label_kind"] = (
                        "insertion" if "i" in new_label else "standard"
                    )
                    record["projection_rule"] = (
                        "trnascan_acceptor_stem_endpoint_v1"
                    )
            provenance.append(record)
        if provenance:
            snapshot.raw_meta["position_provenance"] = provenance

        # Keep the CM residue ledger synchronized with the rendered alignment.
        # The benchmark exports this ledger as scientific provenance, so
        # updating only numbering_row would leave the visible result repaired
        # while the audit trail still reported the parent labels.
        cm_numbering = dict(snapshot.raw_meta.get("cm_numbering") or {})
        cm_residues: List[Dict[str, Any]] = []
        for item in list(cm_numbering.get("residues") or []):
            record = dict(item or {})
            try:
                residue_index = int(record.get("residue_index") or 0)
            except (TypeError, ValueError):
                residue_index = 0
            if 1 <= residue_index <= len(candidate_labels):
                new_label = candidate_labels[residue_index - 1]
                if new_label != str(record.get("label") or ""):
                    record["source_label"] = str(record.get("label") or "")
                    record["label"] = new_label
                    record["label_kind"] = (
                        "insertion" if "i" in new_label else "standard"
                    )
                    record["projection_rule"] = (
                        "trnascan_acceptor_stem_endpoint_v1"
                    )
            cm_residues.append(record)
        if cm_residues:
            cm_numbering["residues"] = cm_residues
            snapshot.raw_meta["cm_numbering"] = cm_numbering

        region_evidence = dict(
            snapshot.raw_meta.get("structural_region_evidence") or {}
        )
        region_labels = dict(region_evidence.get("region_labels") or {})
        for region in (
            "Aminoacyl arm 5' end",
            "Aminoacyl arm 3' end",
        ):
            region_labels[region] = [
                label
                for label in candidate_labels
                if self._infer_region_for_label(label) == region
            ]
        region_evidence["region_labels"] = region_labels
        region_counts = dict(region_evidence.get("region_residue_counts") or {})
        for region in (
            "Aminoacyl arm 5' end",
            "Aminoacyl arm 3' end",
        ):
            region_counts[region] = len(region_labels[region])
        region_evidence["region_residue_counts"] = region_counts
        region_evidence["acceptor_endpoint_source"] = "tRNAscan-SE"
        snapshot.raw_meta["structural_region_evidence"] = region_evidence

        if bool(result.get("left_arm_reconciled")):
            preserved_pairs = []
            for pair in list(parent_snapshot.structure_view.get("id_pairs") or []):
                left_region = self._infer_region_for_label(
                    str((pair or {}).get("left_id") or "")
                )
                right_region = self._infer_region_for_label(
                    str((pair or {}).get("right_id") or "")
                )
                if {
                    left_region,
                    right_region,
                } == {
                    "Aminoacyl arm 5' end",
                    "Aminoacyl arm 3' end",
                }:
                    continue
                preserved_pairs.append(dict(pair or {}))
            acceptor_pairs = []
            for pair in list(result.get("acceptor_pairs") or []):
                left_index = int(pair.get("left_residue_index") or 0)
                right_index = int(pair.get("right_residue_index") or 0)
                if not (
                    1 <= left_index <= len(candidate_labels)
                    and 1 <= right_index <= len(candidate_labels)
                ):
                    continue
                acceptor_pairs.append(
                    {
                        "left_id": candidate_labels[left_index - 1],
                        "right_id": candidate_labels[right_index - 1],
                        "left_index": left_index,
                        "right_index": right_index,
                    }
                )
            snapshot.structure_view = deepcopy(parent_snapshot.structure_view)
            snapshot.structure_view.update(
                {
                    "annotation_source": (
                        "CM structure with tRNAscan-SE acceptor endpoint evidence"
                    ),
                    "residue_count": len(occupied_bases),
                    "pair_count": len(preserved_pairs) + len(acceptor_pairs),
                    "id_pairs": [*preserved_pairs, *acceptor_pairs],
                    "acceptor_hit_start_residue_index": result.get(
                        "hit_start_residue_index"
                    ),
                    "acceptor_hit_end_residue_index": result.get(
                        "hit_end_residue_index"
                    ),
                }
            )
            audit["structure_view_policy"] = (
                "replace_acceptor_pairs_and_preserve_parent_other_regions"
            )
        else:
            audit["structure_view_policy"] = (
                "preserve_parent_cm_pairs_when_5prime_extension_is_unresolved"
            )
        snapshot.root_source = "acceptor_tail_structure_reconciliation"
        self._render_csv(snapshot)
        notes.append("acceptor_tail_reconciliation_applied")
        notes.append(
            "acceptor_tail_changed_residues="
            + str(len(list(result.get("changed_residue_indices") or [])))
        )
        return snapshot

    def _execute_variable_boundary_reconciliation(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        notes: list[str],
    ) -> RootSnapshot:
        snapshot = self._copy_snapshot(parent_snapshot)
        occupied_labels: List[str] = []
        occupied_bases: List[str] = []
        occupied_columns: List[int] = []
        for column, (label, base) in enumerate(
            zip(parent_snapshot.numbering_row, parent_snapshot.sequence_row)
        ):
            normalized = str(base or "").strip().upper().replace("T", "U")
            if normalized not in {"A", "C", "G", "U", "N"}:
                continue
            occupied_columns.append(column)
            occupied_labels.append(str(label or "").strip())
            occupied_bases.append(normalized)

        domain_routing = self._scientific_tool_domain_routing(request, parent_snapshot)
        try:
            scan = run_trnascan_directly(
                request.normalized_target_seq(),
                seq_id="variable_boundary_check",
                domain_hint=domain_routing["effective_domain_hint"],
                mitochondrial_subtype=request.mitochondrial_subtype,
            )
            result = reconcile_variable_boundary(
                target_sequence=request.normalized_target_seq(),
                current_labels=occupied_labels,
                current_bases=occupied_bases,
                struct_file_sequence=scan.struct_file_sequence,
                secondary_structure=scan.secondary_structure,
            )
        except Exception as exc:
            result = {
                "resolved": False,
                "reason": "trnascan_variable_boundary_tool_failed",
                "error_type": type(exc).__name__,
            }
            scan = None

        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        resolved = bool(result.get("resolved"))
        resolution_mode = str(result.get("mode") or "")
        audit_status = "unresolved"
        if resolved:
            audit_status = (
                "structure_resolved_reference_pending"
                if resolution_mode == "long_variable_arm_from_independent_pairs"
                else "resolved"
            )
        audit = {
            "schema_version": 1,
            "policy": "trnascan_variable_boundary_projection_v1",
            "tool_name": "tRNAscan-SE",
            "role": "independent_variable_arm_and_46_48_boundary_evidence",
            "domain_routing": domain_routing,
            "deterministic": True,
            "llm_used": False,
            "answer_free": True,
            "gold_coordinates_used": False,
            "status": audit_status,
            "result": dict(result),
        }
        if scan is not None:
            audit["tool_observation"] = {
                "isotype": str(scan.type or ""),
                "anticodon": str(scan.anticodon or ""),
                "structure_length": len(str(scan.secondary_structure or "")),
                "structure_sha256": hashlib.sha256(
                    (
                        str(scan.struct_file_sequence or "")
                        + "\n"
                        + str(scan.secondary_structure or "")
                    ).encode("utf-8")
                ).hexdigest(),
                "search_mode": str(getattr(scan, "search_mode", "") or ""),
                "domain_hint": str(getattr(scan, "domain_hint", "") or ""),
                "mitochondrial_subtype": str(
                    getattr(scan, "mitochondrial_subtype", "") or ""
                ),
                "command_options": list(getattr(scan, "command_options", ()) or ()),
                "executable": str(getattr(scan, "executable", "") or ""),
                "version": str(getattr(scan, "version", "") or ""),
            }
        snapshot.raw_meta["variable_boundary_reconciliation"] = audit

        if not bool(result.get("resolved")):
            notes.append(
                "variable_boundary_reconciliation_unresolved:"
                + str(result.get("reason") or "unknown")
            )
            return snapshot

        candidate_labels = [
            str(value or "").strip()
            for value in list(result.get("candidate_labels") or [])
        ]
        if len(candidate_labels) != len(occupied_columns):
            audit["status"] = "unresolved"
            audit["result"] = {
                "resolved": False,
                "reason": "candidate_and_occupied_column_count_mismatch",
            }
            notes.append("variable_boundary_reconciliation_unresolved:column_count")
            return snapshot

        for residue_index, column in enumerate(occupied_columns):
            snapshot.numbering_row[column] = candidate_labels[residue_index]

        def _updated_residue_records(records: Any) -> List[Dict[str, Any]]:
            updated: List[Dict[str, Any]] = []
            for item in list(records or []):
                record = dict(item or {})
                try:
                    residue_index = int(record.get("residue_index") or 0)
                except (TypeError, ValueError):
                    residue_index = 0
                if 1 <= residue_index <= len(candidate_labels):
                    new_label = candidate_labels[residue_index - 1]
                    if new_label != str(record.get("label") or ""):
                        record["source_label"] = str(record.get("label") or "")
                        record["label"] = new_label
                        record["label_kind"] = label_kind(new_label)
                        record["projection_rule"] = (
                            "trnascan_variable_boundary_projection_v1"
                        )
                updated.append(record)
            return updated

        provenance = _updated_residue_records(
            snapshot.raw_meta.get("position_provenance")
        )
        if provenance:
            snapshot.raw_meta["position_provenance"] = provenance
        cm_numbering = dict(snapshot.raw_meta.get("cm_numbering") or {})
        cm_residues = _updated_residue_records(cm_numbering.get("residues"))
        if cm_residues:
            cm_numbering["residues"] = cm_residues
            snapshot.raw_meta["cm_numbering"] = cm_numbering

        region_evidence = dict(
            snapshot.raw_meta.get("structural_region_evidence") or {}
        )
        region_labels = dict(region_evidence.get("region_labels") or {})
        region_labels["Variable loop"] = [
            label
            for label in candidate_labels
            if self._infer_region_for_label(label) == "Variable loop"
        ]
        region_counts = dict(region_evidence.get("region_residue_counts") or {})
        region_counts["Variable loop"] = len(region_labels["Variable loop"])
        region_evidence["region_labels"] = region_labels
        region_evidence["region_residue_counts"] = region_counts
        region_evidence["variable_boundary_source"] = "tRNAscan-SE"
        snapshot.raw_meta["structural_region_evidence"] = region_evidence
        snapshot.raw_meta["variable_bridge_evidence"] = assess_variable_bridge(
            snapshot.numbering_row,
            snapshot.sequence_row,
            require_t_stem_anchor=True,
        )

        preserved_pairs: List[Dict[str, Any]] = []
        for pair in list(parent_snapshot.structure_view.get("id_pairs") or []):
            left_region = self._infer_region_for_label(
                str((pair or {}).get("left_id") or "")
            )
            right_region = self._infer_region_for_label(
                str((pair or {}).get("right_id") or "")
            )
            if "Variable loop" in {left_region, right_region}:
                continue
            preserved_pairs.append(dict(pair or {}))
        variable_pairs: List[Dict[str, Any]] = []
        for pair in list(result.get("variable_pairs") or []):
            left_index = int(pair.get("left_residue_index") or 0)
            right_index = int(pair.get("right_residue_index") or 0)
            if not (
                1 <= left_index <= len(candidate_labels)
                and 1 <= right_index <= len(candidate_labels)
            ):
                continue
            variable_pairs.append(
                {
                    "left_id": candidate_labels[left_index - 1],
                    "right_id": candidate_labels[right_index - 1],
                    "left_index": left_index,
                    "right_index": right_index,
                }
            )
        snapshot.structure_view = deepcopy(parent_snapshot.structure_view)
        snapshot.structure_view.update(
            {
                "annotation_source": (
                    "CM structure with tRNAscan-SE variable-boundary evidence"
                ),
                "residue_count": len(occupied_bases),
                "pair_count": len(preserved_pairs) + len(variable_pairs),
                "id_pairs": [*preserved_pairs, *variable_pairs],
                "variable_boundary_mode": result.get("mode"),
            }
        )
        audit["structure_view_policy"] = (
            "replace_variable_pairs_and_preserve_parent_other_regions"
        )
        snapshot.root_source = "variable_boundary_structure_reconciliation"
        self._render_csv(snapshot)
        notes.append("variable_boundary_reconciliation_applied")
        notes.append(
            "variable_boundary_changed_residues="
            + str(len(list(result.get("changed_residue_indices") or [])))
        )
        return snapshot

    def _execute_boundary_refinement(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        state: Dict[str, Any],
        notes: list[str],
    ) -> tuple[RootSnapshot, List[Dict[str, Any]]]:
        cmp = deepcopy(state["cmp"])
        runtime_overrides = dict(state["runtime_overrides"])
        focus_regions = self._focus_regions(action, state)
        maybe_refine_anticodon_boundaries(
            cmp,
            list(STANDARD_ALIGNMENT_REGIONS),
            runtime_values_override=runtime_overrides,
        )
        ann_meta = dict((cmp.get("annotation_meta") or {}))
        warnings = list(parent_snapshot.warnings)
        if ann_meta.get("boundary_resolved"):
            warnings.append(
                "Deterministic multi-evidence boundary refinement adjusted "
                "D/anticodon/variable cut points."
            )
        blocks, global_struct_summary = self._recompute_regions(
            cmp=cmp,
            ann_meta=ann_meta,
            existing_blocks=state["blocks"],
            regions=focus_regions,
        )
        notes.append(f"boundary_refined_regions={focus_regions}")
        notes.append("boundary_resolver=deterministic_multi_evidence_search")
        snapshot = self.root_builder.snapshot_from_components(
            request=request,
            cmp=cmp,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(state["blocks"]),
            warnings=warnings,
            runtime_overrides=runtime_overrides,
            global_struct_summary=global_struct_summary,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            snapshot_id=parent_snapshot.snapshot_id,
            parent_snapshot_id=parent_snapshot.parent_snapshot_id,
            source_action=action.action_type,
            root_source="local_boundary_refinement",
            write_output=False,
        )
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        snapshot.raw_meta["boundary_refinement_contract"] = {
            "schema_version": 1,
            "invocation": "explicit_agent_action",
            "resolver": str(ann_meta.get("boundary_resolver_mode") or "no_change"),
            "llm_used": False,
            "candidate_count": int(ann_meta.get("boundary_candidate_count") or 0),
            "scoring_policy": str(ann_meta.get("boundary_scoring_policy") or ""),
            "evidence_before": dict(ann_meta.get("boundary_evidence_before") or {}),
            "evidence_after": dict(ann_meta.get("boundary_evidence_after") or {}),
        }
        return snapshot, blocks

    def _execute_region_postcheck(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        state: Dict[str, Any],
        notes: list[str],
    ) -> tuple[RootSnapshot, List[Dict[str, Any]]]:
        cmp = deepcopy(state["cmp"])
        ann_meta = dict(state["ann_meta"])
        blocks = deepcopy(state["blocks"])
        runtime_overrides = dict(state["runtime_overrides"])
        focus_regions = self._focus_regions(action, state)
        if not focus_regions:
            notes.append("region_postcheck_missing_focus_regions")
            return self._copy_snapshot(parent_snapshot), deepcopy(state["blocks"])
        if action.action_type == "repair_region_with_template_soft_prior":
            soft_template = str(action.params.get("template_name") or self._best_soft_prior_template(parent_snapshot) or "")
            if soft_template:
                cmp = self.root_builder.compare(request, template_name=soft_template)
                ann_meta = dict((cmp.get("annotation_meta") or {}))
                blocks, _ = self._recompute_regions(
                    cmp=cmp,
                    ann_meta=ann_meta,
                    existing_blocks=blocks,
                    regions=focus_regions,
                )
                notes.append(f"template_soft_prior={soft_template}")
        runtime_overrides["AGENT_POSTCHECK_ONLY_REGIONS"] = list(focus_regions)
        runtime_overrides["LLM_POSTCHECK_MAX_WORKERS"] = 1
        blocks_before_postcheck = deepcopy(blocks)
        warnings = list(parent_snapshot.warnings)
        blocks = maybe_run_region_postcheck(
            blocks=blocks,
            blocks_before_postcheck=blocks_before_postcheck,
            use_llm=bool(request.use_llm),
            target_seq=request.target_seq,
            template_name=str(cmp.get("template_name") or parent_snapshot.template_name),
            llm_reasoning=bool(request.llm_reasoning),
            status_callback=None,
            runtime_overrides=runtime_overrides,
            warnings=warnings,
            validate_alignment_fn=validate_alignment,
            postcheck_alignment_fn=postcheck_alignment,
        )
        global_struct_summary = self._global_struct_summary(cmp=cmp, ann_meta=ann_meta)
        notes.append(f"region_postcheck_regions={focus_regions}")
        snapshot = self.root_builder.snapshot_from_components(
            request=request,
            cmp=cmp,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(state["blocks"]),
            warnings=warnings,
            runtime_overrides=runtime_overrides,
            global_struct_summary=global_struct_summary,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            snapshot_id=parent_snapshot.snapshot_id,
            parent_snapshot_id=parent_snapshot.parent_snapshot_id,
            source_action=action.action_type,
            root_source="local_region_postcheck",
            write_output=False,
        )
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        snapshot.raw_meta["regional_candidate_contract"] = (
            self._regional_candidate_contract(
                blocks=blocks,
                focus_regions=focus_regions,
                ann_meta=ann_meta,
            )
        )
        return snapshot, blocks

    def _execute_variable_topology_repair(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        state: Dict[str, Any],
        notes: list[str],
    ) -> tuple[RootSnapshot, List[Dict[str, Any]]]:
        cmp = deepcopy(state["cmp"])
        ann_meta = dict(state["ann_meta"])
        blocks = deepcopy(state["blocks"])
        runtime_overrides = dict(state["runtime_overrides"])

        changed = False
        for block in blocks:
            if str(block.get("region") or "") != "Variable loop":
                continue
            nums, templ, targ = [list(x) for x in list(block.get("lines") or [[], [], []])]
            rebuilt = rebuild_variable_block_topology(
                nums=nums,
                templ=templ,
                targ=targ,
                raw_struct=str((block.get("meta") or {}).get("input_struct", "") or ""),
            )
            if rebuilt is None:
                continue
            new_nums, new_templ, new_targ = rebuilt
            if [nums, templ, targ] == [new_nums, new_templ, new_targ]:
                continue
            block["lines"] = [new_nums, new_templ, new_targ]
            changed = True
            notes.append(
                "variable_topology_repaired="
                + ",".join(label for label, base in zip(new_nums, new_targ) if str(base or "").strip() != "-")
            )
            break

        if not changed:
            notes.append("variable_topology_repair_no_change")
            return self._copy_snapshot(parent_snapshot), deepcopy(state["blocks"])

        global_struct_summary = self._global_struct_summary(cmp=cmp, ann_meta=ann_meta)
        snapshot = self.root_builder.snapshot_from_components(
            request=request,
            cmp=cmp,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(state["blocks"]),
            warnings=list(parent_snapshot.warnings),
            runtime_overrides=runtime_overrides,
            global_struct_summary=global_struct_summary,
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            snapshot_id=parent_snapshot.snapshot_id,
            parent_snapshot_id=parent_snapshot.parent_snapshot_id,
            source_action=action.action_type,
            root_source="local_variable_topology",
            write_output=False,
        )
        return snapshot, blocks

    def _execute_region_dp_repair(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        state: Dict[str, Any],
        notes: list[str],
        *,
        use_llm: bool,
    ) -> tuple[RootSnapshot, List[Dict[str, Any]]]:
        cmp = deepcopy(state["cmp"])
        ann_meta = dict(state["ann_meta"])
        focus_regions = self._focus_regions(action, state)
        if not focus_regions:
            notes.append("region_dp_repair_missing_focus_regions")
            return self._copy_snapshot(parent_snapshot), deepcopy(state["blocks"])
        blocks, global_struct_summary = self._recompute_regions(
            cmp=cmp,
            ann_meta=ann_meta,
            existing_blocks=state["blocks"],
            regions=focus_regions,
        )
        notes.append(f"region_dp_recomputed={focus_regions}")
        snapshot = self.root_builder.snapshot_from_components(
            request=request,
            cmp=cmp,
            ann_meta=ann_meta,
            blocks=blocks,
            fallback_blocks=deepcopy(state["blocks"]),
            warnings=list(parent_snapshot.warnings),
            runtime_overrides=dict(state["runtime_overrides"]),
            global_struct_summary=global_struct_summary,
            use_llm=bool(use_llm),
            llm_reasoning=False,
            snapshot_id=parent_snapshot.snapshot_id,
            parent_snapshot_id=parent_snapshot.parent_snapshot_id,
            source_action=action.action_type,
            root_source="local_region_dp",
            write_output=False,
        )
        return snapshot, blocks

    @staticmethod
    def _restore_parent_state(
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        *,
        work_state: AgentWorkState | None = None,
    ) -> Dict[str, Any] | None:
        if work_state is not None and str(work_state.current_snapshot.snapshot_id or "") == str(parent_snapshot.snapshot_id or ""):
            cmp = deepcopy(dict(work_state.compare_state or {}))
            ann_meta = deepcopy(dict(work_state.annotation_state or {}))
            blocks = deepcopy(list(work_state.region_blocks or []))
            runtime_overrides = deepcopy(dict(work_state.runtime_state or request.runtime_values_override or {}))
            if cmp and blocks:
                return {
                    "cmp": cmp,
                    "ann_meta": ann_meta,
                    "blocks": blocks,
                    "runtime_overrides": runtime_overrides,
                }
        return None

    def _recompute_regions(
        self,
        *,
        cmp: Dict[str, Any],
        ann_meta: Mapping[str, Any],
        existing_blocks: List[Dict[str, Any]],
        regions: Iterable[str],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        region_set = {str(region) for region in regions if str(region)}
        existing_by_region = {str((blk or {}).get("region") or ""): deepcopy(blk) for blk in list(existing_blocks or [])}
        global_struct_summary = self._global_struct_summary(cmp=cmp, ann_meta=ann_meta)
        rebuilt: List[Dict[str, Any]] = []
        prev_anchor = None
        for region in STANDARD_ALIGNMENT_REGIONS:
            if region in region_set or region not in existing_by_region:
                block = self._build_region_block(
                    cmp=cmp,
                    region=region,
                    prev_anchor=prev_anchor,
                    global_struct_summary=global_struct_summary,
                )
            else:
                block = deepcopy(existing_by_region[region])
                block.setdefault("meta", {})
                block["meta"]["global_struct_summary"] = dict(global_struct_summary)
                block["meta"]["input_struct"] = str(((cmp.get("regions") or {}).get(region) or {}).get("input_struct", "") or "")
            if rebuilt:
                fix_boundary_fill(rebuilt[-1], block)
            rebuilt.append(block)
            prev_anchor = anchor_from_aligned(list(block.get("lines", [[], [], []])[0]))
        return rebuilt, global_struct_summary

    @staticmethod
    def _template_switch_regions(*, parent_cmp: Mapping[str, Any], candidate_cmp: Mapping[str, Any]) -> List[str]:
        parent_regions = dict((parent_cmp or {}).get("regions") or {})
        candidate_regions = dict((candidate_cmp or {}).get("regions") or {})
        changed: List[str] = []
        for region in STANDARD_ALIGNMENT_REGIONS:
            if BranchExecutor._region_signature(parent_regions.get(region)) != BranchExecutor._region_signature(candidate_regions.get(region)):
                changed.append(region)
        return changed or list(STANDARD_ALIGNMENT_REGIONS)

    @staticmethod
    def _region_signature(payload: Any) -> tuple:
        region = dict(payload or {})
        return (
            tuple(region.get("template_numbering") or []),
            str(region.get("template_seq", "") or ""),
            str(region.get("input_seq", "") or ""),
            str(region.get("input_struct", "") or ""),
        )

    @staticmethod
    def _build_region_block(
        *,
        cmp: Mapping[str, Any],
        region: str,
        prev_anchor: str | None,
        global_struct_summary: Mapping[str, Any],
    ) -> Dict[str, Any]:
        data = dict(((cmp.get("regions") or {}).get(region) or {}))
        tpl_num = list(data.get("template_numbering") or [])
        tpl_seq = str(data.get("template_seq", "") or "")
        inp_seq = str(data.get("input_seq", "") or "")
        inp_struct = str(data.get("input_struct", "") or "")
        out = align_with_dp(tpl_num, tpl_seq, inp_seq, region=region, prev_num=prev_anchor)
        nums = list(out.get("aligned_numbering", []))
        targ = list(out.get("aligned_sequence", []))
        templ = list(out.get("aligned_template", []))
        if not templ or len(templ) != len(nums):
            templ = ["-"] * len(nums)
        return {
            "region": region,
            "lines": [nums, templ, targ],
            "meta": {
                "input_struct": inp_struct,
                "global_struct_summary": dict(global_struct_summary),
            },
        }

    @staticmethod
    def _global_struct_summary(*, cmp: Mapping[str, Any], ann_meta: Mapping[str, Any]) -> Dict[str, Any]:
        empty_regions = list((ann_meta or {}).get("empty_regions") or [])
        return build_global_struct_summary(
            cmp=cmp,
            regions=list(STANDARD_ALIGNMENT_REGIONS),
            ann_meta=ann_meta,
            empty_regions=empty_regions,
            normalize_token=norm_rna_token,
            safe_int_value=safe_int_value,
        )

    @staticmethod
    def _regional_candidate_contract(
        *,
        blocks: List[Dict[str, Any]],
        focus_regions: List[str],
        ann_meta: Mapping[str, Any],
    ) -> Dict[str, Any]:
        attempted_regions: List[str] = []
        changed_regions: List[str] = []
        final_status_by_region: Dict[str, str] = {}
        for block in list(blocks or []):
            region = str((block or {}).get("region") or "")
            if region not in focus_regions:
                continue
            meta = (block or {}).get("meta") or {}
            trace = meta.get("llm_runtime_trace") if isinstance(meta, Mapping) else {}
            if not isinstance(trace, Mapping):
                continue
            final_status = str(trace.get("final_status") or "")
            final_status_by_region[region] = final_status
            if bool(trace.get("llm_attempted")):
                attempted_regions.append(region)
            if bool(trace.get("output_changed")):
                changed_regions.append(region)
        return {
            "schema_version": 1,
            "invocation": "explicit_agent_action",
            "role": "bounded_regional_candidate_generator",
            "candidate_status": "pending_shared_harness_evaluation",
            "llm_output_directly_authoritative": False,
            "focus_regions": list(focus_regions),
            "llm_attempted_regions": sorted(set(attempted_regions)),
            "changed_regions": sorted(set(changed_regions)),
            "final_status_by_region": final_status_by_region,
            "deterministic_validators": [
                "base_preservation",
                "sprinzl_label_legality_and_order",
                "anticodon_anchor_consistency",
                "secondary_structure_topology",
                "shared_online_ranker",
            ],
            "evidence_available": {
                "trnascan_anticodon": bool(
                    str((ann_meta or {}).get("trnascan_anticodon") or "")
                ),
                "secondary_structure": bool(
                    str((ann_meta or {}).get("secondary_structure") or "")
                ),
            },
        }

    def _enforce_action_contract(
        self,
        parent_snapshot: RootSnapshot,
        snapshot: RootSnapshot,
        action: CandidateAction,
        state: Mapping[str, Any],
        notes: list[str],
        *,
        child_blocks: List[Dict[str, Any]] | None = None,
    ) -> RootSnapshot:
        error = self._validate_action_contract(action)
        if error:
            notes.append(error)
            return self._copy_snapshot(parent_snapshot)
        allowed_regions = self._declared_regions(action)
        if not allowed_regions:
            return snapshot
        changed_regions = self._changed_regions(parent_snapshot, snapshot, state, child_blocks=child_blocks)
        unexpected = sorted(region for region in changed_regions if region not in allowed_regions)
        if not unexpected:
            return snapshot
        notes.append(
            "action_scope_rollback:"
            + f"allowed={','.join(allowed_regions)}"
            + f";changed={','.join(changed_regions)}"
        )
        return self._copy_snapshot(parent_snapshot)

    @staticmethod
    def _validate_action_contract(action: CandidateAction) -> str:
        if action.action_type in BranchExecutor._ABSTAIN_ACTIONS:
            return ""
        if not BranchExecutor._declared_regions(action):
            return f"action_contract_missing_regions:{action.action_type}"
        if not str(action.success_condition or "").strip():
            return f"action_contract_missing_success_condition:{action.action_type}"
        if not str(action.rollback_condition or "").strip():
            return f"action_contract_missing_rollback_condition:{action.action_type}"
        return ""

    @staticmethod
    def _declared_regions(action: CandidateAction) -> List[str]:
        regions = [str(x) for x in list(action.allowed_regions or []) if str(x)]
        if regions:
            return regions
        regions = [str(x) for x in list(action.params.get("focus_regions") or []) if str(x)]
        if regions:
            return regions
        if action.target_region:
            return [str(action.target_region)]
        return []

    def _changed_regions(
        self,
        parent_snapshot: RootSnapshot,
        snapshot: RootSnapshot,
        state: Mapping[str, Any],
        *,
        child_blocks: List[Dict[str, Any]] | None = None,
    ) -> List[str]:
        parent_blocks = list(state.get("blocks") or [])
        effective_child_blocks = list(child_blocks or [])
        changed = set(self._changed_block_regions(parent_blocks, effective_child_blocks))
        changed.update(self._changed_row_regions(parent_snapshot, snapshot, parent_blocks, effective_child_blocks))
        return sorted(changed)

    @classmethod
    def _changed_block_regions(
        cls,
        parent_blocks: List[Dict[str, Any]],
        child_blocks: List[Dict[str, Any]],
    ) -> List[str]:
        parent_map = {str((block or {}).get("region") or ""): cls._block_signature(block) for block in parent_blocks}
        child_map = {str((block or {}).get("region") or ""): cls._block_signature(block) for block in child_blocks}
        changed: List[str] = []
        for region in STANDARD_ALIGNMENT_REGIONS:
            if parent_map.get(region) != child_map.get(region):
                changed.append(region)
        return changed

    @staticmethod
    def _block_signature(block: Mapping[str, Any] | None) -> tuple:
        payload = dict(block or {})
        lines = list(payload.get("lines") or [[], [], []])
        return (
            tuple(lines[0] if len(lines) > 0 else []),
            tuple(lines[1] if len(lines) > 1 else []),
            tuple(lines[2] if len(lines) > 2 else []),
        )

    def _changed_row_regions(
        self,
        parent_snapshot: RootSnapshot,
        snapshot: RootSnapshot,
        parent_blocks: List[Dict[str, Any]],
        child_blocks: List[Dict[str, Any]],
    ) -> List[str]:
        parent_map = self._row_payload_map(parent_snapshot)
        child_map = self._row_payload_map(snapshot)
        parent_region_map = self._label_region_map(parent_blocks)
        child_region_map = self._label_region_map(child_blocks)
        changed: set[str] = set()
        for label in sorted(set(parent_map) | set(child_map)):
            if parent_map.get(label) == child_map.get(label):
                continue
            region = (
                parent_region_map.get(label)
                or child_region_map.get(label)
                or self._infer_region_for_label(label)
            )
            if region:
                changed.add(region)
        return sorted(changed)

    @staticmethod
    def _row_payload_map(snapshot: RootSnapshot) -> Dict[str, tuple[str, str]]:
        payload: Dict[str, tuple[str, str]] = {}
        for label, templ, targ in zip(snapshot.numbering_row, snapshot.template_row, snapshot.sequence_row):
            key = str(label or "")
            if not key:
                continue
            payload[key] = (str(templ or ""), str(targ or ""))
        return payload

    @staticmethod
    def _label_region_map(blocks: List[Dict[str, Any]]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for block in list(blocks or []):
            region = str((block or {}).get("region") or "")
            lines = list((block or {}).get("lines") or [[], [], []])
            labels = list(lines[0] if lines else [])
            for label in labels:
                key = str(label or "")
                if key:
                    mapping[key] = region
        return mapping

    def _sync_row_level_blocks(self, snapshot: RootSnapshot, parent_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        region_map = self._label_region_map(parent_blocks)
        meta_by_region = {
            region: deepcopy(
                dict(
                    (
                        next(
                            (blk for blk in parent_blocks if str((blk or {}).get("region") or "") == region),
                            {},
                        )
                        or {}
                    ).get("meta")
                    or {}
                )
            )
            for region in STANDARD_ALIGNMENT_REGIONS
        }
        rebuilt = {
            region: {
                "region": region,
                "lines": [[], [], []],
                "meta": meta_by_region[region],
            }
            for region in STANDARD_ALIGNMENT_REGIONS
        }
        for label, templ, targ in zip(snapshot.numbering_row, snapshot.template_row, snapshot.sequence_row):
            key = str(label or "")
            region = region_map.get(key) or self._infer_region_for_label(key)
            if not region:
                continue
            rebuilt[region]["lines"][0].append(key)
            rebuilt[region]["lines"][1].append(str(templ or ""))
            rebuilt[region]["lines"][2].append(str(targ or ""))
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        blocks = [rebuilt[region] for region in STANDARD_ALIGNMENT_REGIONS]
        snapshot.raw_meta["agent_blocks"] = blocks
        return blocks

    @staticmethod
    def _infer_region_for_label(label: str) -> str:
        text = str(label or "").strip()
        if not text:
            return ""
        if text.startswith("V"):
            return "Variable loop"
        if text.startswith("-1"):
            return "Aminoacyl arm 5' end"
        number_text = text
        while number_text and not number_text[-1].isdigit():
            number_text = number_text[:-1]
        if number_text.endswith("i"):
            number_text = number_text[:-1]
        if "i" in number_text:
            number_text = number_text.split("i", 1)[0]
        while number_text and not number_text[-1].isdigit():
            number_text = number_text[:-1]
        try:
            number = int(number_text)
        except (TypeError, ValueError):
            return ""
        if 1 <= number <= 7:
            return "Aminoacyl arm 5' end"
        if 8 <= number <= 26:
            return "D loop + D stem"
        if 27 <= number <= 43:
            return "Anticodon loop + Anticodon stem"
        if 44 <= number <= 48:
            return "Variable loop"
        if 49 <= number <= 65:
            return "T loop + T stem"
        if 66 <= number <= 76:
            return "Aminoacyl arm 3' end"
        return ""

    @staticmethod
    def _focus_regions(action: CandidateAction, state: Mapping[str, Any]) -> List[str]:
        focus_regions = [str(x) for x in list(action.params.get("focus_regions") or []) if str(x)]
        if focus_regions:
            return focus_regions
        allowed_regions = [str(x) for x in list(action.allowed_regions or []) if str(x)]
        if allowed_regions:
            return allowed_regions
        if action.target_region:
            return [str(action.target_region)]
        ann_meta = dict(state.get("ann_meta") or {})
        empty_regions = [str(x) for x in list(ann_meta.get("empty_regions") or []) if str(x)]
        if empty_regions:
            return empty_regions[:2]
        return []

    def _execute_specialized_route(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
    ) -> RootSnapshot:
        action_type = action.action_type
        notes: list[str] = []

        if action_type in {
            "route_to_degenerate_mt_handler",
            "route_to_pseudogene_filter",
            "route_to_out_of_scope_handler",
        }:
            snapshot = self._copy_snapshot(parent_snapshot)
            notes.append(self._mark_abstention(snapshot, action_type))
            self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
            return snapshot

        branch_request = AlignmentRequest(
            target_seq=request.target_seq,
            output_csv_path=request.output_csv_path,
            anticode=request.anticode,
            use_llm=False,
            llm_reasoning=False,
            template_name=parent_snapshot.template_name,
            input_type=request.input_type,
            domain_hint=request.domain_hint,
            cca_status=request.cca_status,
            intron_status=request.intron_status,
            return_meta=True,
            runtime_values_override=dict(request.runtime_values_override or {}),
            status_callback=None,
        )
        branch_request.runtime_values_override["AGENT_TEMPLATE_TOP_K"] = 12
        route_name = self._forced_route_for_action(action_type)
        strategy = "specialized_root_rebuild"

        if action_type == "route_to_mitochondrial_model":
            specialized_template = self._resolve_route_specific_template(
                request,
                parent_snapshot,
                keywords=("mitochond", "mito"),
                state={"ann_meta": dict((parent_snapshot.raw_meta or {}).get("annotation_meta") or {})},
            )
            if not specialized_template:
                snapshot = self._copy_snapshot(parent_snapshot)
                notes.append("mitochondrial_specialized_template_missing")
                self._set_specialized_status(
                    snapshot,
                    handler=action_type,
                    forced_route=route_name,
                    abstain_like=True,
                    strategy="specialized_template_missing",
                    details={"template_before": parent_snapshot.template_name},
                )
                self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
                return snapshot
            branch_request.template_name = specialized_template
            notes.append(f"mitochondrial_template={specialized_template}")
        elif action_type == "route_to_archaeal_ncintron_mode":
            specialized_template = self._resolve_route_specific_template(
                request,
                parent_snapshot,
                keywords=("archaea", "archaeal"),
                state={"ann_meta": dict((parent_snapshot.raw_meta or {}).get("annotation_meta") or {})},
            )
            if specialized_template:
                branch_request.template_name = specialized_template
                notes.append(f"archaeal_template={specialized_template}")
            else:
                notes.append("archaeal_specialized_template_missing_using_root_family")
            branch_request.runtime_values_override["AGENT_TEMPLATE_TOP_K"] = 14
            notes.append("archaeal_ncintron_dp_only")
        elif action_type == "route_to_truncated_candidate_mode":
            branch_request.runtime_values_override["AGENT_TEMPLATE_TOP_K"] = 8
            notes.append("weak_or_noncanonical_dp_only")

        snapshot = self.root_builder.build(branch_request)

        if action_type == "route_to_truncated_candidate_mode":
            self._apply_compaction(request, snapshot, notes)
            self._apply_reanchor(
                request,
                snapshot,
                dict(snapshot.raw_meta.get("annotation_meta") or {}),
                notes,
            )

        self._set_specialized_status(
            snapshot,
            handler=action_type,
            forced_route=route_name,
            abstain_like=False,
            strategy=strategy,
            details={
                "template_before": parent_snapshot.template_name,
                "template_after": snapshot.template_name,
                "template_changed": snapshot.template_name != parent_snapshot.template_name,
            },
        )
        self._finalize_snapshot(snapshot, parent_snapshot, action, branch_id, notes)
        return snapshot

    def _resolve_template_name(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        *,
        state: Mapping[str, Any],
    ) -> str:
        explicit = str(action.params.get("template_name") or "").strip()
        if explicit:
            return explicit

        route = str(action.params.get("route") or "").strip().lower()
        if action.action_type == "route_to_mitochondrial_model" or route == "mitochondrial":
            choice = self._pick_keyword_template(
                request.target_seq,
                parent_snapshot,
                keywords=("mito", "mitochond"),
                annotation_hints=state.get("ann_meta"),
            )
            return choice or parent_snapshot.template_name
        if action.action_type == "switch_template_domain_or_clade_specific" and route == "archaeal_ncintron":
            choice = self._pick_keyword_template(
                request.target_seq,
                parent_snapshot,
                keywords=("archaea", "archaeal"),
                annotation_hints=state.get("ann_meta"),
            )
            return choice or parent_snapshot.template_name
        return self._best_soft_prior_template(parent_snapshot) or parent_snapshot.template_name

    @classmethod
    def _best_soft_prior_template(cls, parent_snapshot: RootSnapshot) -> str:
        parent_family_rank = cls._selected_family_rank(parent_snapshot)
        selected_name = str(parent_snapshot.template_name or "")
        selected_item = next(
            (
                item
                for item in list(parent_snapshot.template_candidates or [])
                if str(item.get("seqname") or "") == selected_name
            ),
            {},
        )
        selected_rerank = float(selected_item.get("rerank_score", selected_item.get("score", 0.0)) or 0.0)
        selected_isotype_match = int(selected_item.get("isotype_match") or 0)
        selected_anticodon_match = int(selected_item.get("anticodon_match") or 0)
        improving = []
        evidence_improving = []
        for item in list(parent_snapshot.template_candidates or []):
            seqname = str(item.get("seqname") or "")
            if not seqname or seqname == parent_snapshot.template_name:
                continue
            family_rank = int(item.get("family_match_rank") or 0)
            if family_rank > parent_family_rank:
                improving.append(item)
                continue
            rerank_score = float(item.get("rerank_score", item.get("score", 0.0)) or 0.0)
            isotype_match = int(item.get("isotype_match") or 0)
            anticodon_match = int(item.get("anticodon_match") or 0)
            mismatch_drift_without_anticodon_gain = (
                selected_anticodon_match == 0
                and anticodon_match == 0
                and selected_isotype_match >= 1
                and isotype_match >= 1
            )
            if (
                not mismatch_drift_without_anticodon_gain and
                (
                isotype_match > selected_isotype_match
                or anticodon_match > selected_anticodon_match
                or rerank_score >= (selected_rerank + 0.75)
                or bool(item.get("family_rescue_selected"))
                )
            ):
                evidence_improving.append(item)
        if improving:
            improving.sort(key=cls._candidate_sort_key, reverse=True)
            return str(improving[0].get("seqname") or "")
        if evidence_improving:
            evidence_improving.sort(key=cls._candidate_sort_key, reverse=True)
            return str(evidence_improving[0].get("seqname") or "")
        return ""

    @classmethod
    def _pick_keyword_template(
        cls,
        target_seq: str,
        parent_snapshot: RootSnapshot,
        *,
        keywords: tuple[str, ...],
        annotation_hints: Mapping[str, Any] | None = None,
    ) -> str:
        lowered = tuple(str(k or "").lower() for k in keywords)
        parent_matches = [
            item
            for item in list(parent_snapshot.template_candidates or [])
            if str(item.get("seqname") or "") and any(key in str(item.get("seqname") or "").lower() for key in lowered)
        ]
        if parent_matches:
            parent_matches.sort(key=cls._candidate_sort_key, reverse=True)
            return str(parent_matches[0].get("seqname") or "")
        annotation_meta = dict(annotation_hints or {})
        candidates = find_top_matches(
            target_seq,
            preferred_isotype=str(annotation_meta.get("trnascan_isotype", "") or ""),
            preferred_anticodon=str(annotation_meta.get("trnascan_anticodon", "") or ""),
            limit=12,
        )
        candidates = [
            item
            for item in candidates
            if str(item.get("seqname") or "") and any(key in str(item.get("seqname") or "").lower() for key in lowered)
        ]
        if candidates:
            candidates.sort(key=cls._candidate_sort_key, reverse=True)
            return str(candidates[0].get("seqname") or "")
        return ""

    def _resolve_route_specific_template(
        self,
        request: AlignmentRequest,
        parent_snapshot: RootSnapshot,
        *,
        keywords: tuple[str, ...],
        state: Mapping[str, Any],
    ) -> str:
        choice = self._pick_keyword_template(
            request.target_seq,
            parent_snapshot,
            keywords=keywords,
            annotation_hints=state.get("ann_meta"),
        )
        if choice:
            return choice
        hints = dict(state.get("ann_meta") or {})
        source = str(hints.get("annotation_source", "") or "").lower()
        ranked = sorted(list(parent_snapshot.template_candidates or []), key=self._candidate_sort_key, reverse=True)
        for item in ranked:
            seqname = str(item.get("seqname") or "")
            if not seqname:
                continue
            text = seqname.lower()
            if any(key in text for key in keywords):
                return seqname
            if source and source in text:
                return seqname
        return ""

    @staticmethod
    def _candidate_sort_key(item: dict) -> tuple[int, int, int, float, float]:
        return (
            int(item.get("family_match_rank") or 0),
            int(bool(item.get("family_rescue_selected"))),
            int(item.get("anticodon_match") or 0),
            float(item.get("rerank_score", item.get("score", 0.0)) or 0.0),
            float(item.get("score", 0.0) or 0.0),
        )

    @staticmethod
    def _selected_family_rank(parent_snapshot: RootSnapshot) -> int:
        selected = str(parent_snapshot.template_name or "")
        for item in list(parent_snapshot.template_candidates or []):
            if str(item.get("seqname") or "") == selected:
                return int(item.get("family_match_rank") or 0)
        return 0

    @staticmethod
    def _copy_snapshot(snapshot: RootSnapshot) -> RootSnapshot:
        return RootSnapshot(
            template_name=str(snapshot.template_name),
            csv_content=str(snapshot.csv_content),
            warnings=list(snapshot.warnings),
            numbering_row=list(snapshot.numbering_row),
            template_row=list(snapshot.template_row),
            sequence_row=list(snapshot.sequence_row),
            structure_view=deepcopy(snapshot.structure_view),
            llm_trace_view=deepcopy(snapshot.llm_trace_view),
            raw_meta=deepcopy(snapshot.raw_meta),
            template_candidates=deepcopy(snapshot.template_candidates),
            template_score=snapshot.template_score,
            template_margin=snapshot.template_margin,
            root_source=str(snapshot.root_source),
            snapshot_id=str(snapshot.snapshot_id),
            parent_snapshot_id=snapshot.parent_snapshot_id,
            source_action=str(snapshot.source_action),
        )

    @staticmethod
    def _render_csv(snapshot: RootSnapshot) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(snapshot.numbering_row)
        writer.writerow(snapshot.template_row)
        writer.writerow(snapshot.sequence_row)
        snapshot.csv_content = buffer.getvalue()

    def _apply_compaction(self, request: AlignmentRequest, snapshot: RootSnapshot, notes: list[str]) -> None:
        backup = (
            list(snapshot.numbering_row),
            list(snapshot.template_row),
            list(snapshot.sequence_row),
        )
        changed = compact_and_prune_insertion_columns(
            snapshot.numbering_row,
            snapshot.sequence_row,
            template_row=snapshot.template_row,
        )
        if not changed:
            notes.append("compact_insertions_no_change")
            return
        if not validate_alignment(request.target_seq, snapshot.sequence_row):
            snapshot.numbering_row[:] = backup[0]
            snapshot.template_row[:] = backup[1]
            snapshot.sequence_row[:] = backup[2]
            notes.append("compact_insertions_rollback")
            return
        self._render_csv(snapshot)
        notes.append("compact_insertions_applied")

    def _apply_reanchor(
        self,
        request: AlignmentRequest,
        snapshot: RootSnapshot,
        ann_meta: Mapping[str, Any],
        notes: list[str],
    ) -> None:
        ann_meta = dict(ann_meta or {})
        changed = maybe_reanchor_anticodon_slots(
            num_row=snapshot.numbering_row,
            seq_row=snapshot.sequence_row,
            ann_meta=ann_meta,
            use_llm=bool(request.use_llm),
            target_seq=request.target_seq,
            validate_alignment_fn=validate_alignment,
            norm_rna_token_fn=norm_rna_token,
        )
        if not changed:
            notes.append("reanchor_anticodon_no_change")
            return
        self._render_csv(snapshot)
        notes.append("reanchor_anticodon_applied")

    @staticmethod
    def _forced_route_for_action(action_type: str) -> str:
        return {
            "route_to_mitochondrial_model": "mitochondrial",
            "route_to_archaeal_ncintron_mode": "archaeal_ncintron",
            "route_to_truncated_candidate_mode": "weak_or_noncanonical",
            "route_to_degenerate_mt_handler": "mitochondrial",
            "route_to_pseudogene_filter": "weak_or_noncanonical",
            "route_to_out_of_scope_handler": "out_of_scope",
        }.get(action_type, "")

    @classmethod
    def _mark_abstention(cls, snapshot: RootSnapshot, action_type: str) -> str:
        note = {
            "route_to_degenerate_mt_handler": "degenerate_mt_handler_requested",
            "route_to_pseudogene_filter": "pseudogene_filter_requested",
            "route_to_out_of_scope_handler": "out_of_scope_handler_requested",
        }.get(action_type, "special_route_handler_requested")
        snapshot.warnings = list(snapshot.warnings) + [note]
        cls._set_specialized_status(
            snapshot,
            handler=action_type,
            forced_route=cls._forced_route_for_action(action_type) or "out_of_scope",
            abstain_like=True,
            strategy="explicit_specialized_abstain",
        )
        return note

    @staticmethod
    def _set_specialized_status(
        snapshot: RootSnapshot,
        *,
        handler: str,
        forced_route: str,
        abstain_like: bool,
        strategy: str,
        details: dict | None = None,
    ) -> None:
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        payload = {
            "handler": handler,
            "abstain_like": bool(abstain_like),
            "forced_route": forced_route,
            "strategy": strategy,
            "in_canonical_benchmark": forced_route == "canonical" and not abstain_like,
            "out_of_scope": forced_route == "out_of_scope" or abstain_like,
        }
        if details:
            payload.update(dict(details))
        snapshot.raw_meta["agent_specialized_status"] = payload

    @staticmethod
    def _branch_compare_payload(snapshot: RootSnapshot, parent_snapshot: RootSnapshot) -> dict:
        parent_structural = dict((parent_snapshot.raw_meta or {}).get("agent_structural_rank") or {})
        parent_family_rank = BranchExecutor._selected_family_rank(parent_snapshot)
        branch_family_rank = BranchExecutor._selected_family_rank(snapshot)
        return {
            "parent_template_name": str(parent_snapshot.template_name or ""),
            "template_changed": str(snapshot.template_name or "") != str(parent_snapshot.template_name or ""),
            "parent_structural_score": float(parent_structural.get("structural_score") or 0.0),
            "parent_family_rank": int(parent_family_rank),
            "branch_family_rank": int(branch_family_rank),
            "family_rank_delta": int(branch_family_rank - parent_family_rank),
        }

    def _finalize_snapshot(
        self,
        snapshot: RootSnapshot,
        parent_snapshot: RootSnapshot,
        action: CandidateAction,
        branch_id: str,
        notes: list[str],
    ) -> None:
        snapshot.snapshot_id = branch_id
        snapshot.parent_snapshot_id = parent_snapshot.snapshot_id
        snapshot.source_action = action.action_type
        snapshot.raw_meta = dict(snapshot.raw_meta or {})
        if action.action_type.startswith("route_to_") and "agent_specialized_status" not in snapshot.raw_meta:
            self._set_specialized_status(
                snapshot,
                handler=action.action_type,
                forced_route=self._forced_route_for_action(action.action_type),
                abstain_like=action.action_type in {
                    "route_to_degenerate_mt_handler",
                    "route_to_pseudogene_filter",
                    "route_to_out_of_scope_handler",
                },
                strategy="route_action_finalize_default",
            )
        snapshot.raw_meta["agent_branch"] = {
            "branch_id": branch_id,
            "parent_snapshot_id": parent_snapshot.snapshot_id,
            "action_id": action.action_id,
            "action_type": action.action_type,
            "specialist": action.specialist,
            "scope": action.scope,
            "target_region": action.target_region,
            "params": dict(action.params or {}),
            "reason": action.reason,
            "allowed_regions": list(action.allowed_regions or []),
            "success_condition": str(action.success_condition or ""),
            "rollback_condition": str(action.rollback_condition or ""),
            "notes": list(notes),
            "budget_cost": {
                "llm_calls_reserved": int(action.budget_cost.llm_calls_reserved),
                "expected_wall_time_sec": int(action.budget_cost.expected_wall_time_sec),
            },
        }
        for contract_key in (
            "boundary_refinement_contract",
            "regional_candidate_contract",
        ):
            contract = snapshot.raw_meta.get(contract_key)
            if not isinstance(contract, dict):
                continue
            contract["parent_snapshot_id"] = str(parent_snapshot.snapshot_id or "")
            contract["parent_fingerprint"] = self._snapshot_fingerprint(parent_snapshot)
            contract["candidate_fingerprint"] = self._snapshot_fingerprint(snapshot)
        snapshot.raw_meta["agent_branch_compare"] = self._branch_compare_payload(snapshot, parent_snapshot)

    @staticmethod
    def _snapshot_fingerprint(snapshot: RootSnapshot) -> str:
        raw_meta = dict(snapshot.raw_meta or {})
        annotation = dict(
            raw_meta.get("agent_ann_meta")
            or raw_meta.get("annotation_meta")
            or {}
        )
        blocks = []
        for block in list(raw_meta.get("agent_blocks") or []):
            lines = list((block or {}).get("lines") or [[], [], []])
            meta = (block or {}).get("meta") or {}
            blocks.append(
                {
                    "region": str((block or {}).get("region") or ""),
                    "numbering": list(lines[0] if len(lines) > 0 else []),
                    "template": list(lines[1] if len(lines) > 1 else []),
                    "sequence": list(lines[2] if len(lines) > 2 else []),
                    "input_struct": str(
                        meta.get("input_struct") if isinstance(meta, Mapping) else ""
                    ),
                }
            )
        payload = {
            "template_name": str(snapshot.template_name or ""),
            "numbering_row": list(snapshot.numbering_row or []),
            "template_row": list(snapshot.template_row or []),
            "sequence_row": list(snapshot.sequence_row or []),
            "blocks": blocks,
            "annotation": {
                "annotation_source": str(annotation.get("annotation_source") or ""),
                "trnascan_anticodon": str(annotation.get("trnascan_anticodon") or ""),
                "trnascan_anticodon_start": annotation.get("trnascan_anticodon_start"),
                "trnascan_anticodon_end": annotation.get("trnascan_anticodon_end"),
                "secondary_structure": str(annotation.get("secondary_structure") or ""),
            },
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["BranchExecutor"]
