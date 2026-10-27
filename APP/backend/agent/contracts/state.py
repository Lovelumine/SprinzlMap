from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .diagnostics import DiagnosticsReport, DifficultyReport, RouteReport
from .snapshot import RootSnapshot


@dataclass(slots=True)
class SearchBudget:
    max_action_steps: int = 8
    used_action_steps: int = 0
    max_planner_calls: int = 6
    used_planner_calls: int = 0
    max_template_switches: int = 2
    used_template_switches: int = 0
    max_region_postchecks: int = 2
    used_region_postchecks: int = 0
    consecutive_no_gain: int = 0
    repeated_region_edits: Dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ActionRecord:
    action_id: str
    action_type: str
    context_signature: Dict[str, Any] = field(default_factory=dict)
    target_regions: List[str] = field(default_factory=list)
    accepted: bool = False
    score_before: float = 0.0
    score_after: float = 0.0
    valid_before: bool = False
    valid_after: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentWorkState:
    input_sequence: str
    request_flags: Dict[str, Any]
    current_snapshot: RootSnapshot
    rows: Dict[str, List[str]]
    selected_template: Dict[str, Any]
    template_candidates: List[Dict[str, Any]]
    region_blocks: List[Dict[str, Any]]
    seams: List[Dict[str, Any]]
    compare_state: Dict[str, Any]
    annotation_state: Dict[str, Any]
    runtime_state: Dict[str, Any]
    global_struct_state: Dict[str, Any]
    current_diagnostics: DiagnosticsReport
    current_difficulty: DifficultyReport
    current_route: RouteReport
    current_valid: bool
    current_reasons: List[str]
    current_score: float
    validation: Dict[str, Any]
    ranking: Dict[str, Any]
    best_snapshot: RootSnapshot
    best_rows: Dict[str, List[str]]
    best_selected_template: Dict[str, Any]
    best_template_candidates: List[Dict[str, Any]]
    best_region_blocks: List[Dict[str, Any]]
    best_seams: List[Dict[str, Any]]
    best_compare_state: Dict[str, Any]
    best_annotation_state: Dict[str, Any]
    best_runtime_state: Dict[str, Any]
    best_global_struct_state: Dict[str, Any]
    best_diagnostics: DiagnosticsReport
    best_difficulty: DifficultyReport
    best_route: RouteReport
    best_valid: bool
    best_reasons: List[str]
    best_score: float
    best_validation: Dict[str, Any]
    best_ranking: Dict[str, Any]
    budget: SearchBudget = field(default_factory=SearchBudget)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    action_history: List[ActionRecord] = field(default_factory=list)
    attempted_signatures: List[str] = field(default_factory=list)
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)
    literature_findings: List[Dict[str, Any]] = field(default_factory=list)
    # Each entry is one row the model proposed and the guards rejected, with the
    # reason codes.  The next proposal is told which rules it just broke, which
    # is the only way a guarded model can do better on its second attempt.
    coordinate_proposal_attempts: List[Dict[str, Any]] = field(default_factory=list)
    # How many times the Harness has refused a punt.  A case that is sent to
    # review without any repair attempted says the Agent gave up, not that the
    # evidence ran out, so one repair round is required before escalation; it
    # is capped so a model that insists on escalating still gets its way.
    escalation_deferrals: int = 0
    stop_reason: str = ""

    @classmethod
    def from_root(
        cls,
        *,
        input_sequence: str,
        request_flags: Dict[str, Any],
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        difficulty: DifficultyReport,
        route: RouteReport,
        valid: bool,
        reasons: List[str],
        score: float,
        budget: SearchBudget | None = None,
        issues: List[Dict[str, Any]] | None = None,
    ) -> "AgentWorkState":
        payload = cls._snapshot_payload(
            snapshot=snapshot,
            diagnostics=diagnostics,
            valid=bool(valid),
            reasons=list(reasons or []),
            score=float(score),
        )
        return cls(
            input_sequence=input_sequence,
            request_flags=dict(request_flags or {}),
            current_snapshot=snapshot,
            rows=payload["rows"],
            selected_template=payload["selected_template"],
            template_candidates=payload["template_candidates"],
            region_blocks=payload["region_blocks"],
            seams=payload["seams"],
            compare_state=payload["compare_state"],
            annotation_state=payload["annotation_state"],
            runtime_state=payload["runtime_state"],
            global_struct_state=payload["global_struct_state"],
            current_diagnostics=diagnostics,
            current_difficulty=difficulty,
            current_route=route,
            current_valid=bool(valid),
            current_reasons=list(reasons or []),
            current_score=float(score),
            validation=payload["validation"],
            ranking=payload["ranking"],
            best_snapshot=snapshot,
            best_rows=deepcopy(payload["rows"]),
            best_selected_template=deepcopy(payload["selected_template"]),
            best_template_candidates=deepcopy(payload["template_candidates"]),
            best_region_blocks=deepcopy(payload["region_blocks"]),
            best_seams=deepcopy(payload["seams"]),
            best_compare_state=deepcopy(payload["compare_state"]),
            best_annotation_state=deepcopy(payload["annotation_state"]),
            best_runtime_state=deepcopy(payload["runtime_state"]),
            best_global_struct_state=deepcopy(payload["global_struct_state"]),
            best_diagnostics=diagnostics,
            best_difficulty=difficulty,
            best_route=route,
            best_valid=bool(valid),
            best_reasons=list(reasons or []),
            best_score=float(score),
            best_validation=deepcopy(payload["validation"]),
            best_ranking=deepcopy(payload["ranking"]),
            budget=budget or SearchBudget(),
            issues=list(issues or []),
        )

    def sync_current(
        self,
        *,
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        valid: bool,
        reasons: List[str],
        score: float,
    ) -> None:
        payload = self._snapshot_payload(
            snapshot=snapshot,
            diagnostics=diagnostics,
            valid=bool(valid),
            reasons=list(reasons or []),
            score=float(score),
        )
        self.current_snapshot = snapshot
        self.rows = payload["rows"]
        self.selected_template = payload["selected_template"]
        self.template_candidates = payload["template_candidates"]
        self.region_blocks = payload["region_blocks"]
        self.seams = payload["seams"]
        self.compare_state = payload["compare_state"]
        self.annotation_state = payload["annotation_state"]
        self.runtime_state = payload["runtime_state"]
        self.global_struct_state = payload["global_struct_state"]
        self.validation = payload["validation"]
        self.ranking = payload["ranking"]

    def sync_best(
        self,
        *,
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        valid: bool,
        reasons: List[str],
        score: float,
    ) -> None:
        payload = self._snapshot_payload(
            snapshot=snapshot,
            diagnostics=diagnostics,
            valid=bool(valid),
            reasons=list(reasons or []),
            score=float(score),
        )
        self.best_snapshot = snapshot
        self.best_rows = payload["rows"]
        self.best_selected_template = payload["selected_template"]
        self.best_template_candidates = payload["template_candidates"]
        self.best_region_blocks = payload["region_blocks"]
        self.best_seams = payload["seams"]
        self.best_compare_state = payload["compare_state"]
        self.best_annotation_state = payload["annotation_state"]
        self.best_runtime_state = payload["runtime_state"]
        self.best_global_struct_state = payload["global_struct_state"]
        self.best_validation = payload["validation"]
        self.best_ranking = payload["ranking"]

    def summary(self) -> Dict[str, Any]:
        return {
            "current_snapshot_id": str(self.current_snapshot.snapshot_id or ""),
            "current_template": str(self.current_snapshot.template_name or ""),
            "current_score": float(self.current_score),
            "current_valid": bool(self.current_valid),
            "current_route": str(self.current_route.route or ""),
            "best_snapshot_id": str(self.best_snapshot.snapshot_id or ""),
            "best_template": str(self.best_snapshot.template_name or ""),
            "best_score": float(self.best_score),
            "best_valid": bool(self.best_valid),
            "current_row_count": len(self.rows.get("numbering") or []),
            "current_region_block_count": len(self.region_blocks),
            "current_template_candidate_count": len(self.template_candidates),
            "used_action_steps": int(self.budget.used_action_steps),
            "max_action_steps": int(self.budget.max_action_steps),
            "used_planner_calls": int(self.budget.used_planner_calls),
            "max_planner_calls": int(self.budget.max_planner_calls),
            "used_template_switches": int(self.budget.used_template_switches),
            "max_template_switches": int(self.budget.max_template_switches),
            "used_region_postchecks": int(self.budget.used_region_postchecks),
            "max_region_postchecks": int(self.budget.max_region_postchecks),
            "consecutive_no_gain": int(self.budget.consecutive_no_gain),
            "issue_count": len(self.issues),
            "literature_lookup_count": len(self.literature_findings),
            "coordinate_proposal_count": len(self.coordinate_proposal_attempts),
            "escalation_deferrals": int(self.escalation_deferrals),
            "attempted_actions": len(self.action_history),
            "stop_reason": str(self.stop_reason or ""),
            "action_history": [
                {
                    "action_id": str(item.action_id or ""),
                    "action_type": str(item.action_type or ""),
                    "context_signature": deepcopy(item.context_signature),
                    "target_regions": list(item.target_regions or []),
                    "accepted": bool(item.accepted),
                    "score_before": float(item.score_before),
                    "score_after": float(item.score_after),
                    "valid_before": bool(item.valid_before),
                    "valid_after": bool(item.valid_after),
                    "notes": list(item.notes or []),
                }
                for item in self.action_history
            ],
        }

    @staticmethod
    def _snapshot_payload(
        *,
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        valid: bool,
        reasons: List[str],
        score: float,
    ) -> Dict[str, Any]:
        raw_meta = dict(snapshot.raw_meta or {})
        region_blocks = deepcopy(list(raw_meta.get("agent_blocks") or []))
        template_candidates = deepcopy(list(snapshot.template_candidates or raw_meta.get("template_candidates") or []))
        selected_item = next(
            (
                item
                for item in template_candidates
                if str(item.get("seqname") or "") == str(snapshot.template_name or "")
            ),
            {},
        )
        seams: List[Dict[str, Any]] = []
        regions = [str((blk or {}).get("region") or "") for blk in region_blocks if str((blk or {}).get("region") or "")]
        for left, right in zip(regions, regions[1:]):
            seams.append({"left_region": left, "right_region": right})
        return {
            "rows": {
                "numbering": list(snapshot.numbering_row or []),
                "template": list(snapshot.template_row or []),
                "sequence": list(snapshot.sequence_row or []),
            },
            "selected_template": {
                "template_name": str(snapshot.template_name or ""),
                "template_score": snapshot.template_score if snapshot.template_score is not None else diagnostics.template_score,
                "template_margin": snapshot.template_margin if snapshot.template_margin is not None else diagnostics.template_margin,
                "family_match_rank": int(selected_item.get("family_match_rank") or 0),
                "family_match_label": str(selected_item.get("family_match_label") or ""),
                "template_isotype": str(selected_item.get("template_isotype") or ""),
                "template_anticodon": str(selected_item.get("template_anticodon") or ""),
            },
            "template_candidates": template_candidates,
            "region_blocks": region_blocks,
            "seams": seams,
            "compare_state": deepcopy(dict(raw_meta.get("agent_cmp") or {})),
            "annotation_state": deepcopy(dict(raw_meta.get("agent_ann_meta") or raw_meta.get("annotation_meta") or {})),
            "runtime_state": deepcopy(dict(raw_meta.get("agent_runtime_overrides") or {})),
            "global_struct_state": deepcopy(dict(raw_meta.get("agent_global_struct_summary") or {})),
            "validation": {
                "valid": bool(valid),
                "reasons": list(reasons or []),
            },
            "ranking": {
                "score": float(score),
                "template_score": snapshot.template_score if snapshot.template_score is not None else diagnostics.template_score,
                "template_margin": snapshot.template_margin if snapshot.template_margin is not None else diagnostics.template_margin,
            },
        }


__all__ = ["SearchBudget", "ActionRecord", "AgentWorkState"]
