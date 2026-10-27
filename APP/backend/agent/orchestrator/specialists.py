from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from ..contracts import CandidateAction
from .context_engineering import SpecialistContext
from backend.domain.alignment.engine import STANDARD_ALIGNMENT_REGIONS
from ..memory.retrieval import MEMORY_ACTION_ALLOWLIST


@dataclass(slots=True)
class ProposedAction:
    specialist: str
    action: CandidateAction


class CMNumberingSpecialist:
    name = "cm_numbering_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if (
            not context.cm_tool_enabled
            or not context.canonical_eligible
            or context.cm_evidence_present
            or context.input_type != "mature"
            or context.intron_status == "present"
        ):
            return []
        return [
            CandidateAction(
                action_type="align_numbering_cm",
                specialist=self.name,
                scope="snapshot",
                params={
                    "selection_mode": "portfolio",
                    "domain_hint": context.domain_hint,
                },
                reason="cm_numbering_specialist:structure_homology_candidate",
                budget_cost=_budget(llm_calls=0, wall=1),
                allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                success_condition="produce_base_preserving_monotonic_structure_homology_candidate",
                rollback_condition="rollback_if_shared_hard_guards_fail_or_candidate_does_not_improve",
            )
        ]


class LegacyReferenceSpecialist:
    name = "legacy_reference_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if (
            context.numbering_backend != "cmalign"
            or not context.canonical_eligible
            or not context.validation_reasons
        ):
            return []
        return [
            CandidateAction(
                action_type="align_legacy_reference",
                specialist=self.name,
                scope="snapshot",
                reason="legacy_reference_specialist:cm_candidate_not_accepted",
                budget_cost=_budget(llm_calls=0, wall=4),
                allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                success_condition="legacy_reference_passes_shared_hard_guards",
                rollback_condition="retain_cm_evidence_and_abstain_if_reference_is_invalid",
            )
        ]


class VariableBoundarySpecialist:
    name = "variable_boundary_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        reason_codes = {
            str(reason or "").split(":", 1)[0]
            for reason in list(context.validation_reasons or [])
        }
        if (
            context.numbering_backend != "cmalign"
            or not context.canonical_eligible
            or not reason_codes.intersection(
                {
                    "variable_bridge_sprinzl_convention_violation",
                    "variable_bridge_structure_review_required",
                    "variable_singleton_occupied",
                }
            )
        ):
            return []
        return [
            CandidateAction(
                action_type="reconcile_variable_boundary_with_structure",
                specialist=self.name,
                scope="region",
                target_region="Variable loop",
                reason=(
                    "variable_boundary_specialist:request_independent_"
                    "trnascan_structure_projection"
                ),
                budget_cost=_budget(llm_calls=0, wall=2),
                allowed_regions=["Variable loop"],
                success_condition=(
                    "independent_structure_confirms_a_supported_variable_"
                    "boundary_and_candidate_passes_shared_hard_guards"
                ),
                rollback_condition=(
                    "retain_cm_observation_and_review_when_structure_or_"
                    "v_dialect_placement_is_ambiguous"
                ),
            )
        ]


class TemplateSpecialist:
    name = "template_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        actions: List[CandidateAction] = []
        selected_family_rank = int(context.selected_family_match_rank or 0)
        selected_item = next(
            (item for item in list(context.top_templates or []) if item.get("seqname") == context.selected_template),
            None,
        )
        selected_candidate_rank = int((selected_item or {}).get("rank") or 999)
        selected_rerank = float(
            (selected_item or {}).get("rerank_score")
            or (selected_item or {}).get("score")
            or 0.0
        )
        selected_isotype_match = int((selected_item or {}).get("isotype_match") or 0)
        selected_anticodon_match = int((selected_item or {}).get("anticodon_match") or 0)
        candidates = sorted(
            list(context.top_templates or []),
            key=lambda item: (
                int(item.get("family_match_rank") or 0),
                int(bool(item.get("family_rescue_selected"))),
                float(item.get("rerank_score") or item.get("score") or 0.0),
                -int(item.get("rank") or 999),
            ),
            reverse=True,
        )
        selected_rank = int(context.selected_family_match_rank or 0)
        for item in candidates:
            if item.get("seqname") == context.selected_template:
                continue
            rank = int(item.get("rank") or 999)
            family_rank = int(item.get("family_match_rank") or 0)
            family_improves = family_rank > selected_family_rank
            candidate_rerank = float(item.get("rerank_score") or item.get("score") or 0.0)
            candidate_isotype_match = int(item.get("isotype_match") or 0)
            candidate_anticodon_match = int(item.get("anticodon_match") or 0)
            family_rescue_selected = bool(item.get("family_rescue_selected"))
            same_family_drift = not family_improves
            if same_family_drift:
                mismatch_drift_without_anticodon_gain = (
                    bool(context.preferred_anticodon)
                    and selected_anticodon_match == 0
                    and candidate_anticodon_match == 0
                    and selected_isotype_match >= 1
                    and candidate_isotype_match >= 1
                )
                improves_family_evidence = (
                    candidate_isotype_match > selected_isotype_match
                    or candidate_anticodon_match > selected_anticodon_match
                )
                materially_better_retrieval = candidate_rerank >= (selected_rerank + 0.75)
                if mismatch_drift_without_anticodon_gain and not family_rescue_selected and not materially_better_retrieval:
                    continue
            if rank > 3 and not family_improves and not bool(item.get("family_rescue_selected")):
                continue
            action_type = "switch_template_topk"
            reason = "template_specialist:topk_alternative"
            if family_improves or family_rank >= 2:
                action_type = "switch_template_family_constrained"
                reason = "template_specialist:family_constrained_alternative"
            actions.append(
                CandidateAction(
                    action_type=action_type,
                    specialist=self.name,
                    params={
                        "template_name": item.get("seqname"),
                        "candidate_rank": rank,
                        "candidate_family_match_rank": family_rank,
                        "candidate_family_match_label": item.get("family_match_label"),
                        "candidate_rerank_score": candidate_rerank,
                    },
                    reason=reason,
                    budget_cost=_budget(llm_calls=0, wall=3),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="switch_template_locally_and_recompute_only_affected_regions",
                    rollback_condition="rollback_if_template_switch_worsens_validity_or_breaks_scope",
                )
            )
            if family_improves and rank <= 5:
                actions.append(
                    CandidateAction(
                        action_type="switch_template_topk",
                        specialist=self.name,
                        params={
                            "template_name": item.get("seqname"),
                            "candidate_rank": rank,
                            "candidate_family_match_rank": family_rank,
                        },
                        reason="template_specialist:family_improvement_probe",
                        budget_cost=_budget(llm_calls=0, wall=3),
                        allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                        success_condition="switch_template_locally_and_recompute_only_affected_regions",
                        rollback_condition="rollback_if_template_switch_worsens_validity_or_breaks_scope",
                    )
                )
        if context.route in {"mitochondrial", "archaeal_ncintron"}:
            actions.append(
                CandidateAction(
                    action_type="switch_template_domain_or_clade_specific",
                    specialist=self.name,
                    params={"route": context.route},
                    reason="template_specialist:route_specific_template_search",
                    budget_cost=_budget(llm_calls=0, wall=4),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="switch_template_locally_to_route_specific_family",
                    rollback_condition="rollback_if_route_specific_switch_breaks_scope_or_validity",
                )
            )
        return actions[:5]


class BoundarySpecialist:
    name = "boundary_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if not (
            context.boundary_resolved
            or "boundary" in context.warning_types
            or "Anticodon loop + Anticodon stem" in context.failure_regions
            or "Variable loop" in context.failure_regions
        ):
            return []
        actions: List[CandidateAction] = []
        if context.deterministic_boundary_enabled:
            actions.append(
                CandidateAction(
                    action_type="refine_boundary",
                    target_region="Anticodon loop + Anticodon stem",
                    specialist=self.name,
                    params={
                        "focus_regions": [
                            "D loop + D stem",
                            "Anticodon loop + Anticodon stem",
                            "Variable loop",
                        ]
                    },
                    reason="boundary_specialist:targeted_boundary_refinement",
                    budget_cost=_budget(llm_calls=0, wall=2),
                    allowed_regions=[
                        "D loop + D stem",
                        "Anticodon loop + Anticodon stem",
                        "Variable loop",
                    ],
                    success_condition="refine_target_boundary_without_spilling_into_unrelated_regions",
                    rollback_condition="rollback_if_boundary_refinement_breaks_scope_or_validation",
                )
            )
        if context.llm_reasoning and context.regional_llm_candidate_enabled:
            actions.append(
                CandidateAction(
                    action_type="switch_postcheck_mode",
                    target_region="Anticodon loop + Anticodon stem",
                    specialist=self.name,
                    params={"focus_regions": ["Anticodon loop + Anticodon stem"]},
                    reason="boundary_specialist:reasoning_boundary_branch",
                    budget_cost=_budget(llm_calls=1, wall=8),
                    allowed_regions=["Anticodon loop + Anticodon stem"],
                    success_condition="postcheck_target_boundary_region_only",
                    rollback_condition="rollback_if_boundary_postcheck_breaks_scope_or_validation",
                )
            )
        return actions


class RecoverySpecialist:
    name = "recovery_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if not context.use_llm:
            return []
        if context.difficulty_tier == "easy" and not context.warning_types:
            return []
        focus_regions = list(context.failure_regions or [])
        if not focus_regions and "boundary" in context.warning_types:
            focus_regions = ["D loop + D stem", "Anticodon loop + Anticodon stem", "Variable loop"]
        target_region = focus_regions[0] if focus_regions else None
        return [
            CandidateAction(
                action_type="repair_region_with_dp_only",
                scope="region" if target_region else "snapshot",
                target_region=target_region,
                specialist=self.name,
                params={"focus_regions": focus_regions[:3]},
                reason="recovery_specialist:dp_only_recovery",
                budget_cost=_budget(llm_calls=0, wall=4),
                allowed_regions=focus_regions[:3],
                success_condition="recover_target_region_without_breaking_base_preservation",
                rollback_condition="rollback_if_base_preservation_or_hard_guards_fail",
            )
        ]


class RegionRepairSpecialist:
    name = "region_repair_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        actions: List[CandidateAction] = []
        if not context.use_llm:
            return actions
        regions = list(context.failure_regions or [])
        for region in regions[:2]:
            focus_regions = [region]
            if region == "Variable loop" and "T loop + T stem" in regions:
                focus_regions = ["Variable loop", "T loop + T stem"]
            elif region == "Variable loop" and "Anticodon loop + Anticodon stem" in regions:
                focus_regions = ["Anticodon loop + Anticodon stem", "Variable loop"]
            if region == "Variable loop":
                actions.append(
                    CandidateAction(
                        action_type="repair_variable_topology_locally",
                        specialist=self.name,
                        scope="region",
                        target_region=region,
                        params={"focus_regions": focus_regions},
                        reason="region_repair_specialist:variable_topology_local_repair",
                        budget_cost=_budget(llm_calls=0, wall=2),
                        allowed_regions=focus_regions,
                        success_condition="repair_variable_topology_without_cross_region_mutation",
                        rollback_condition="rollback_if_variable_topology_repair_breaks_scope_or_validation",
                    )
                )
            if context.regional_llm_candidate_enabled:
                actions.append(
                    CandidateAction(
                        action_type="repair_region_with_structure_first_policy",
                        specialist=self.name,
                        scope="region",
                        target_region=region,
                        params={"focus_regions": focus_regions},
                        reason=f"region_repair_specialist:structure_first:{region}",
                        budget_cost=_budget(llm_calls=1, wall=10),
                        allowed_regions=focus_regions,
                        success_condition="postcheck_only_requested_regions_with_structure_first_policy",
                        rollback_condition="rollback_if_region_postcheck_breaks_scope_or_validation",
                    )
                )
            if context.regional_llm_candidate_enabled and region in {
                "D loop + D stem",
                "Anticodon loop + Anticodon stem",
                "Variable loop",
                "T loop + T stem",
            }:
                actions.append(
                    CandidateAction(
                        action_type="repair_region_with_template_soft_prior",
                        specialist=self.name,
                        scope="region",
                        target_region=region,
                        params={"focus_regions": focus_regions},
                        reason=f"region_repair_specialist:template_soft_prior:{region}",
                        budget_cost=_budget(llm_calls=1, wall=10),
                        allowed_regions=focus_regions,
                        success_condition="postcheck_only_requested_regions_with_template_soft_prior",
                        rollback_condition="rollback_if_region_postcheck_breaks_scope_or_validation",
                    )
                )
        return actions[:4]


class ConsistencySpecialist:
    name = "consistency_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        actions: List[CandidateAction] = []
        if "anticodon" in context.warning_types or "Anticodon loop + Anticodon stem" in context.failure_regions:
            actions.append(
                CandidateAction(
                    action_type="reanchor_anticodon",
                    specialist=self.name,
                    reason="consistency_specialist:anticodon_reanchor",
                    budget_cost=_budget(llm_calls=0, wall=1),
                    allowed_regions=["Anticodon loop + Anticodon stem"],
                    success_condition="reanchor_only_anticodon_region_columns",
                    rollback_condition="rollback_if_reanchor_breaks_scope_or_validation",
                )
            )
        if (
            "insertion" in context.warning_types
            or "rollback" in context.warning_types
            or context.difficulty_tier in {"moderate", "hard"}
        ):
            actions.append(
                CandidateAction(
                    action_type="compact_insertions",
                    specialist=self.name,
                    reason="consistency_specialist:compact_insertions",
                    budget_cost=_budget(llm_calls=0, wall=1),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="compact_only_insertion_columns_without_reordering_core_bases",
                    rollback_condition="rollback_if_compaction_breaks_scope_or_base_preservation",
                )
            )
        return actions


class LiteratureSpecialist:
    """Propose the reference lookup, never a numbering change.

    The Agent reads a short literature note when the open question is about the
    biology of a region, not about which alignment method produced the row.  It
    is last in the planner priority so a deterministic repair is always offered
    before it, and the fixed-policy ablation therefore never selects it first.
    """

    name = "literature_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        # Deciding that a biological question needs a reference is the Agent's
        # judgement, so a run without an Agent policy does not gain the action.
        if not context.literature_tool_enabled or not context.use_llm:
            return []
        regions = [str(item) for item in list(context.failure_regions or []) if str(item)]
        uncertain = bool(
            regions
            or list(context.validation_reasons or [])
            or list(context.warning_types or [])
            or context.difficulty_tier in {"moderate", "hard"}
        )
        if not uncertain:
            return []
        return [
            CandidateAction(
                action_type="search_literature",
                specialist=self.name,
                scope="snapshot",
                params={"conflict_regions": regions[:3]},
                reason="literature_specialist:regional_reference_evidence",
                budget_cost=_budget(llm_calls=0, wall=3),
                allowed_regions=regions[:3] or list(STANDARD_ALIGNMENT_REGIONS),
                success_condition="return_reference_notes_without_changing_the_candidate",
                rollback_condition="no_candidate_change_to_roll_back",
            )
        ]


class CoordinateProposalSpecialist:
    """Let the model rewrite the flagged part of a row, under the same guards.

    Every other action here hands the work to a deterministic tool, so a model
    that knows the Sprinzl rules still cannot apply them.  This action is the
    one place where it can: it proposes labels for the regions already flagged,
    and the candidate then faces base preservation, ordering, anticodon anchor
    and CCA checks like any other.  It is offered last, so a deterministic
    repair is always tried first, and a run without an Agent policy never sees
    it.
    """

    name = "coordinate_proposal_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if not context.coordinate_proposal_enabled or not context.use_llm:
            return []
        # A row that fails a guard has regions that must move; a row that passes
        # one but is ambiguous on the acceptor end or the variable bridge still
        # has regions in question.  The proposal is only offered for those, so a
        # candidate nobody disputes is never rewritten.
        regions: List[str] = []
        for region in (
            *list(context.failure_regions or []),
            *list(context.disputed_regions or []),
            *list(context.empty_regions or []),
        ):
            text = str(region or "").strip()
            if text and text not in regions:
                regions.append(text)
        if not regions and not list(context.validation_reasons or []):
            return []
        return [
            CandidateAction(
                action_type="propose_coordinate_revision",
                specialist=self.name,
                scope="snapshot",
                params={"focus_regions": regions[:4], "disputed": bool(
                    not context.failure_regions and context.disputed_regions)},
                reason=(
                    "coordinate_proposal_specialist:model_rewrites_flagged_"
                    "regions_under_harness_guards"
                ),
                budget_cost=_budget(llm_calls=1, wall=8),
                allowed_regions=regions[:4] or list(STANDARD_ALIGNMENT_REGIONS),
                success_condition=(
                    "proposed_row_keeps_every_input_base_and_scores_higher"
                ),
                rollback_condition="retain_parent_when_guards_or_score_reject",
            )
        ]


class SpecializedRouteSpecialist:
    name = "route_specialist"

    def propose(self, context: SpecialistContext) -> List[CandidateAction]:
        if context.route == "mitochondrial":
            return [
                CandidateAction(
                    action_type="route_to_mitochondrial_model",
                    specialist=self.name,
                    reason="route_specialist:mitochondrial_route",
                    budget_cost=_budget(llm_calls=0, wall=6),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="switch_to_mitochondrial_route_without_full_rebuild_outside_agent_loop",
                    rollback_condition="rollback_if_mitochondrial_route_breaks_scope_or_validation",
                ),
                CandidateAction(
                    action_type="route_to_degenerate_mt_handler",
                    specialist=self.name,
                    reason="route_specialist:degenerate_mitochondrial_fallback",
                    budget_cost=_budget(llm_calls=0, wall=1),
                )
            ]
        if context.route == "archaeal_ncintron":
            return [
                CandidateAction(
                    action_type="route_to_archaeal_ncintron_mode",
                    specialist=self.name,
                    reason="route_specialist:archaeal_ncintron_route",
                    budget_cost=_budget(llm_calls=0, wall=4),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="switch_to_archaeal_route_without_full_rebuild_outside_agent_loop",
                    rollback_condition="rollback_if_archaeal_route_breaks_scope_or_validation",
                )
            ]
        if context.route == "weak_or_noncanonical":
            return [
                CandidateAction(
                    action_type="route_to_truncated_candidate_mode",
                    specialist=self.name,
                    reason="route_specialist:truncated_candidate_route",
                    budget_cost=_budget(llm_calls=0, wall=3),
                    allowed_regions=list(STANDARD_ALIGNMENT_REGIONS),
                    success_condition="repair_weak_candidate_locally_without_full_rebuild",
                    rollback_condition="rollback_if_truncated_candidate_route_breaks_scope_or_validation",
                ),
                CandidateAction(
                    action_type="route_to_pseudogene_filter",
                    specialist=self.name,
                    reason="route_specialist:pseudogene_filter_route",
                    budget_cost=_budget(llm_calls=0, wall=1),
                ),
            ]
        if context.route == "out_of_scope":
            return [
                CandidateAction(
                    action_type="route_to_out_of_scope_handler",
                    specialist=self.name,
                    reason="route_specialist:out_of_scope",
                    budget_cost=_budget(llm_calls=0, wall=1),
                )
            ]
        return []


class MemorySpecialist:
    name = "memory_specialist"

    def propose(self, context: SpecialistContext, memory_hits: List[Dict[str, object]]) -> List[CandidateAction]:
        actions: List[CandidateAction] = []
        for hit in memory_hits:
            for rec in list(hit.get("recommended_actions") or []):
                action_type = str((rec or {}).get("action_type") or "").strip()
                if action_type not in MEMORY_ACTION_ALLOWLIST:
                    continue
                actions.append(
                    _with_default_contract(
                        CandidateAction(
                        action_type=action_type,
                        specialist=self.name,
                        params=dict((rec or {}).get("params") or {}),
                        reason=(
                            f"verified_memory_prior:{hit.get('pattern_id')}:"
                            f"confidence={float(hit.get('confidence') or 0.0):.3f}:"
                            f"trials={int(hit.get('verified_trials') or 0)}"
                        ),
                        budget_cost=_budget(llm_calls=0, wall=2),
                        allowed_regions=list((rec or {}).get("allowed_regions") or []),
                        success_condition=str((rec or {}).get("success_condition") or ""),
                        rollback_condition=str((rec or {}).get("rollback_condition") or ""),
                        )
                    )
                )
        return actions[:2]


def _budget(llm_calls: int, wall: int):
    from ..contracts import ActionBudgetCost

    return ActionBudgetCost(llm_calls_reserved=int(llm_calls), expected_wall_time_sec=int(wall))


def _freeze_value(value: Any):
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze_value(v) for v in value))
    return value


def _with_default_contract(action: CandidateAction) -> CandidateAction:
    if action.action_type in {
        "align_numbering_cm",
        "align_legacy_reference",
        "switch_template_topk",
        "switch_template_family_constrained",
        "switch_template_domain_or_clade_specific",
        "route_to_mitochondrial_model",
        "route_to_archaeal_ncintron_mode",
        "route_to_truncated_candidate_mode",
        "compact_insertions",
    } and not action.allowed_regions:
        action.allowed_regions = list(STANDARD_ALIGNMENT_REGIONS)
    if action.action_type == "reanchor_anticodon" and not action.allowed_regions:
        action.allowed_regions = ["Anticodon loop + Anticodon stem"]
    if not action.allowed_regions:
        focus_regions = [str(x) for x in list((action.params or {}).get("focus_regions") or []) if str(x)]
        if focus_regions:
            action.allowed_regions = focus_regions
        elif action.target_region:
            action.allowed_regions = [str(action.target_region)]
    if not action.success_condition:
        action.success_condition = f"{action.action_type}:improve_target_without_scope_leak"
    if not action.rollback_condition:
        action.rollback_condition = f"{action.action_type}:rollback_on_scope_or_validation_failure"
    return action


def dedupe_actions(actions: Iterable[ProposedAction]) -> List[ProposedAction]:
    seen = set()
    deduped: List[ProposedAction] = []
    for item in actions:
        key = (
            item.action.action_type,
            _freeze_value(item.action.params),
            item.action.target_region,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = [
    "BoundarySpecialist",
    "CMNumberingSpecialist",
    "ConsistencySpecialist",
    "LegacyReferenceSpecialist",
    "LiteratureSpecialist",
    "CoordinateProposalSpecialist",
    "MemorySpecialist",
    "ProposedAction",
    "RegionRepairSpecialist",
    "RecoverySpecialist",
    "SpecializedRouteSpecialist",
    "TemplateSpecialist",
    "VariableBoundarySpecialist",
    "dedupe_actions",
]
