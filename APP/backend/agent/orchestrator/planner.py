from __future__ import annotations

from typing import List, Tuple

from ..contracts import AgentWorkState, AlignmentRequest, CandidateAction, DiagnosticsReport, DifficultyReport, RootSnapshot, RouteReport
from .context_engineering import ContextEngineeringLayer, SpecialistContext
from .memory_advisor import MemoryAdvisor
from .specialists import (
    BoundarySpecialist,
    CMNumberingSpecialist,
    ConsistencySpecialist,
    CoordinateProposalSpecialist,
    LegacyReferenceSpecialist,
    LiteratureSpecialist,
    MemorySpecialist,
    ProposedAction,
    RegionRepairSpecialist,
    RecoverySpecialist,
    SpecializedRouteSpecialist,
    TemplateSpecialist,
    VariableBoundarySpecialist,
    dedupe_actions,
)


class AgentPlannerV2:
    """
    Constrained scientific-tool planner:
    - context engineering first
    - specialist ensemble proposes actions
    - optional memory recall injects prior successful actions
    - final action list is deduped and bounded
    """

    def __init__(
        self,
        context_layer: ContextEngineeringLayer | None = None,
        memory_advisor: MemoryAdvisor | None = None,
        template_specialist: TemplateSpecialist | None = None,
        boundary_specialist: BoundarySpecialist | None = None,
        region_repair_specialist: RegionRepairSpecialist | None = None,
        recovery_specialist: RecoverySpecialist | None = None,
        consistency_specialist: ConsistencySpecialist | None = None,
        route_specialist: SpecializedRouteSpecialist | None = None,
        memory_specialist: MemorySpecialist | None = None,
        cm_numbering_specialist: CMNumberingSpecialist | None = None,
        legacy_reference_specialist: LegacyReferenceSpecialist | None = None,
        variable_boundary_specialist: VariableBoundarySpecialist | None = None,
        literature_specialist: LiteratureSpecialist | None = None,
    coordinate_proposal_specialist: CoordinateProposalSpecialist | None = None,
    ) -> None:
        self.context_layer = context_layer or ContextEngineeringLayer()
        self.memory_advisor = memory_advisor or MemoryAdvisor()
        self.template_specialist = template_specialist or TemplateSpecialist()
        self.boundary_specialist = boundary_specialist or BoundarySpecialist()
        self.region_repair_specialist = region_repair_specialist or RegionRepairSpecialist()
        self.recovery_specialist = recovery_specialist or RecoverySpecialist()
        self.consistency_specialist = consistency_specialist or ConsistencySpecialist()
        self.route_specialist = route_specialist or SpecializedRouteSpecialist()
        self.memory_specialist = memory_specialist or MemorySpecialist()
        self.cm_numbering_specialist = cm_numbering_specialist or CMNumberingSpecialist()
        self.legacy_reference_specialist = (
            legacy_reference_specialist or LegacyReferenceSpecialist()
        )
        self.variable_boundary_specialist = (
            variable_boundary_specialist or VariableBoundarySpecialist()
        )
        self.literature_specialist = literature_specialist or LiteratureSpecialist()
        self.coordinate_proposal_specialist = (
            coordinate_proposal_specialist or CoordinateProposalSpecialist()
        )

    def plan(
        self,
        request: AlignmentRequest,
        root_snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        difficulty: DifficultyReport,
        route: RouteReport,
        guard_reasons: List[str] | None = None,
    ) -> Tuple[SpecialistContext, List[dict], List[CandidateAction]]:
        context = self.context_layer.build(
            request,
            root_snapshot,
            diagnostics,
            difficulty,
            route,
            guard_reasons=guard_reasons,
        )
        selected_family_rank = int(diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        top_family_rank = int(diagnostics.raw_signals.get("top_family_match_rank") or selected_family_rank)
        family_rank_gap = int(diagnostics.raw_signals.get("family_rank_gap") or 0)
        template_ambiguity = bool(
            diagnostics.template_margin is not None
            and diagnostics.template_margin <= 0.25
            and top_family_rank > selected_family_rank
            and top_family_rank >= 2
        )
        should_expand = (
            context.difficulty_tier in {"moderate", "hard"}
            or diagnostics.warning_count > 0
            or diagnostics.boundary_resolved
            or family_rank_gap > 0
            or (selected_family_rank <= 1 and diagnostics.trnascan_hit)
            or template_ambiguity
            or route.route != "canonical"
            or bool(list(guard_reasons or []))
        )
        if should_expand or route.route != "canonical":
            memory_hits = self.memory_advisor.retrieve(
                context,
                request.runtime_values_override,
            )
        else:
            self.memory_advisor.mark_not_consulted(request.runtime_values_override)
            memory_hits = []
        memory_audit = dict(self.memory_advisor.last_audit)
        context.memory_mode = str(memory_audit.get("mode") or "off")
        context.memory_status = str(memory_audit.get("status") or "disabled")
        context.memory_snapshot_sha256 = str(memory_audit.get("sha256") or "")
        context.memory_hit_count = len(memory_hits)
        if not should_expand and not route.out_of_scope:
            return context, memory_hits, []
        if context.numbering_backend == "cmalign":
            cm_route_proposals = [
                ProposedAction(self.variable_boundary_specialist.name, action)
                for action in self.variable_boundary_specialist.propose(context)
            ]
            cm_route_proposals.extend(
                [
                    ProposedAction(self.legacy_reference_specialist.name, action)
                    for action in self.legacy_reference_specialist.propose(context)
                ]
            )
            cm_route_proposals.extend(
                ProposedAction(self.memory_specialist.name, action)
                for action in self.memory_specialist.propose(context, memory_hits)
            )
            cm_route_proposals.extend(
                ProposedAction(self.literature_specialist.name, action)
                for action in self.literature_specialist.propose(context)
            )
            cm_route_proposals.extend(
                ProposedAction(self.coordinate_proposal_specialist.name, action)
                for action in self.coordinate_proposal_specialist.propose(context)
            )
            ranked = sorted(
                dedupe_actions(cm_route_proposals),
                key=lambda item: self._priority(item.action),
            )
            actions = [item.action for item in self._reserve_tail_actions(ranked, 4)]
            for idx, action in enumerate(actions, start=1):
                if not action.action_id:
                    action.action_id = (
                        f"{action.specialist or self.legacy_reference_specialist.name}:{idx}:"
                        f"{action.action_type}"
                    )
            return context, memory_hits, actions

        proposed: List[ProposedAction] = []
        if not route.out_of_scope:
            proposed.extend(
                ProposedAction(self.cm_numbering_specialist.name, action)
                for action in self.cm_numbering_specialist.propose(context)
            )
            proposed.extend(
                ProposedAction(self.template_specialist.name, action)
                for action in self.template_specialist.propose(context)
            )
            proposed.extend(
                ProposedAction(self.boundary_specialist.name, action)
                for action in self.boundary_specialist.propose(context)
            )
            proposed.extend(
                ProposedAction(self.region_repair_specialist.name, action)
                for action in self.region_repair_specialist.propose(context)
            )
            proposed.extend(
                ProposedAction(self.recovery_specialist.name, action)
                for action in self.recovery_specialist.propose(context)
            )
            proposed.extend(
                ProposedAction(self.consistency_specialist.name, action)
                for action in self.consistency_specialist.propose(context)
            )
        proposed.extend(
            ProposedAction(self.route_specialist.name, action)
            for action in self.route_specialist.propose(context)
        )
        proposed.extend(
            ProposedAction(self.memory_specialist.name, action)
            for action in self.memory_specialist.propose(context, memory_hits)
        )
        proposed.extend(
            ProposedAction(self.literature_specialist.name, action)
            for action in self.literature_specialist.propose(context)
        )
        proposed.extend(
            ProposedAction(self.coordinate_proposal_specialist.name, action)
            for action in self.coordinate_proposal_specialist.propose(context)
        )

        deduped = dedupe_actions(proposed)
        deduped.sort(key=lambda item: self._priority(item.action))
        action_limit = 8 if context.difficulty_tier == "hard" else 5
        if guard_reasons:
            action_limit = max(action_limit, 8)
        if route.out_of_scope:
            action_limit = min(action_limit, 2)
        deduped = self._reserve_tail_actions(deduped, action_limit)
        actions: List[CandidateAction] = []
        for idx, item in enumerate(deduped[:action_limit], start=1):
            action = item.action
            if not action.action_id:
                action.action_id = f"{item.specialist}:{idx}:{action.action_type}"
            if not action.specialist:
                action.specialist = item.specialist
            if action.scope == "snapshot" and action.target_region:
                action.scope = "region"
            actions.append(action)
        return context, memory_hits, actions

    def plan_from_work_state(
        self,
        request: AlignmentRequest,
        work_state: AgentWorkState,
    ) -> Tuple[SpecialistContext, List[dict], List[CandidateAction]]:
        return self.plan(
            request=request,
            root_snapshot=work_state.current_snapshot,
            diagnostics=work_state.current_diagnostics,
            difficulty=work_state.current_difficulty,
            route=work_state.current_route,
            guard_reasons=list(work_state.current_reasons or []),
        )

    @staticmethod
    def _reserve_tail_actions(
        ranked: List["ProposedAction"], action_limit: int
    ) -> List["ProposedAction"]:
        """Keep the tail actions inside a bounded list without promoting them.

        Both tail actions sort last on purpose: the reference lookup changes
        nothing about the candidate, and a coordinate proposal is only worth
        offering once the deterministic repairs have had their turn.  A plain
        truncation would drop exactly those two first, so they are pulled out,
        the remaining slots are filled in priority order, and they go back.
        """
        tail_types = ("search_literature", "propose_coordinate_revision")
        reserved_indexes = [
            index
            for index, item in enumerate(ranked)
            if item.action.action_type in tail_types and index >= action_limit
        ]
        if not reserved_indexes:
            return ranked[:action_limit]
        reserved = [ranked[index] for index in reserved_indexes]
        reserved.sort(key=lambda item: tail_types.index(item.action.action_type))
        kept = [
            item
            for index, item in enumerate(ranked)
            if index not in set(reserved_indexes)
        ]
        return kept[: max(0, action_limit - len(reserved))] + reserved

    @staticmethod
    def _priority(action: CandidateAction) -> tuple[int, int, int, str]:
        bucket = {
            "align_numbering_cm": 0,
            "align_legacy_reference": 0,
            "reconcile_variable_boundary_with_structure": 0,
            "repair_variable_topology_locally": 1,
            "refine_boundary": 1,
            "switch_postcheck_mode": 1,
            "repair_region_with_structure_first_policy": 2,
            "repair_region_with_template_soft_prior": 2,
            "repair_region_with_dp_only": 2,
            "switch_template_topk": 3,
            "switch_template_family_constrained": 3,
            "switch_template_domain_or_clade_specific": 3,
            "compact_insertions": 4,
            "reanchor_anticodon": 4,
            "route_to_mitochondrial_model": 5,
            "route_to_archaeal_ncintron_mode": 5,
            "route_to_degenerate_mt_handler": 5,
            "route_to_pseudogene_filter": 5,
            "route_to_truncated_candidate_mode": 5,
            "route_to_out_of_scope_handler": 5,
            "search_literature": 6,
            "propose_coordinate_revision": 7,
        }.get(action.action_type, 9)
        return (
            bucket,
            int(action.budget_cost.llm_calls_reserved),
            int(action.budget_cost.expected_wall_time_sec),
            action.action_type,
        )


__all__ = ["AgentPlannerV2"]
