from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Mapping

from ..contracts import AlignmentRequest, DiagnosticsReport, DifficultyReport, RootSnapshot, RouteReport


@dataclass(slots=True)
class SpecialistContext:
    """
    Narrow context bundle shared with specialist planners.
    """

    target_length: int
    anticodon_hint: str
    use_llm: bool
    llm_reasoning: bool
    selected_template: str
    regional_llm_candidate_enabled: bool = False
    deterministic_boundary_enabled: bool = True
    template_score: float | None = None
    template_margin: float | None = None
    preferred_isotype: str = ""
    preferred_anticodon: str = ""
    selected_template_isotype: str = ""
    selected_template_anticodon: str = ""
    selected_family_match_rank: int = 0
    top_family_match_rank: int = 0
    strong_family_candidates: int = 0
    top_templates: List[Dict[str, Any]] = field(default_factory=list)
    warning_types: List[str] = field(default_factory=list)
    empty_regions: List[str] = field(default_factory=list)
    boundary_resolved: bool = False
    segmentation_status: str = ""
    difficulty_tier: str = "easy"
    difficulty_score: float = 0.0
    route: str = "canonical"
    route_confidence: float = 0.0
    route_reasons: List[str] = field(default_factory=list)
    failure_regions: List[str] = field(default_factory=list)
    # Regions an assessor found ambiguous on an otherwise legal row.  A CM
    # candidate can pass every hard guard and still be wrong, and these three
    # checks are what says so before anything is edited.
    disputed_regions: List[str] = field(default_factory=list)
    canonical_eligible: bool = True
    cm_tool_enabled: bool = False
    input_type: str = "unknown"
    domain_hint: str = ""
    intron_status: str = "unknown"
    numbering_backend: str = "legacy"
    validation_reasons: List[str] = field(default_factory=list)
    cm_evidence_present: bool = False
    literature_tool_enabled: bool = False
    coordinate_proposal_enabled: bool = False
    memory_mode: str = "off"
    memory_status: str = "disabled"
    memory_snapshot_sha256: str = ""
    memory_hit_count: int = 0

    def summary(self) -> Dict[str, Any]:
        return {
            "target_length": self.target_length,
            "anticodon_hint": self.anticodon_hint,
            "use_llm": self.use_llm,
            "llm_reasoning": self.llm_reasoning,
            "regional_llm_candidate_enabled": self.regional_llm_candidate_enabled,
            "deterministic_boundary_enabled": self.deterministic_boundary_enabled,
            "selected_template": self.selected_template,
            "template_score": self.template_score,
            "template_margin": self.template_margin,
            "preferred_isotype": self.preferred_isotype,
            "preferred_anticodon": self.preferred_anticodon,
            "selected_template_isotype": self.selected_template_isotype,
            "selected_template_anticodon": self.selected_template_anticodon,
            "selected_family_match_rank": self.selected_family_match_rank,
            "top_family_match_rank": self.top_family_match_rank,
            "strong_family_candidates": self.strong_family_candidates,
            "top_templates": list(self.top_templates),
            "warning_types": list(self.warning_types),
            "empty_regions": list(self.empty_regions),
            "boundary_resolved": self.boundary_resolved,
            "segmentation_status": self.segmentation_status,
            "difficulty_tier": self.difficulty_tier,
            "difficulty_score": self.difficulty_score,
            "route": self.route,
            "route_confidence": self.route_confidence,
            "route_reasons": list(self.route_reasons),
            "failure_regions": list(self.failure_regions),
            "disputed_regions": list(self.disputed_regions),
            "canonical_eligible": self.canonical_eligible,
            "cm_tool_enabled": self.cm_tool_enabled,
            "input_type": self.input_type,
            "domain_hint": self.domain_hint,
            "intron_status": self.intron_status,
            "numbering_backend": self.numbering_backend,
            "validation_reasons": list(self.validation_reasons),
            "cm_evidence_present": self.cm_evidence_present,
            "literature_tool_enabled": self.literature_tool_enabled,
            "coordinate_proposal_enabled": self.coordinate_proposal_enabled,
            "memory_mode": self.memory_mode,
            "memory_status": self.memory_status,
            "memory_snapshot_sha256": self.memory_snapshot_sha256,
            "memory_hit_count": self.memory_hit_count,
        }


def answer_free_context_signature(
    context: SpecialistContext | Mapping[str, Any],
) -> Dict[str, Any]:
    """Reduce an action's pre-execution context to non-identifying evidence."""

    raw = context.summary() if isinstance(context, SpecialistContext) else dict(context)
    target_length = max(0, int(raw.get("target_length") or 0))
    length_bin = str(raw.get("length_bin") or "")
    if not length_bin:
        lower = (target_length // 10) * 10
        length_bin = f"{lower}-{lower + 9}"
    return {
        "length_bin": length_bin,
        "difficulty_tier": str(raw.get("difficulty_tier") or ""),
        "route": str(raw.get("route") or ""),
        "numbering_backend": str(raw.get("numbering_backend") or ""),
        "domain_group": str(
            raw.get("domain_group") or raw.get("domain_hint") or "unknown"
        ),
        "input_type": str(raw.get("input_type") or "unknown"),
        "intron_status": str(raw.get("intron_status") or "unknown"),
        "cm_evidence_present": bool(raw.get("cm_evidence_present")),
        "warning_types": sorted(
            {str(item) for item in list(raw.get("warning_types") or []) if str(item)}
        ),
        "failure_regions": sorted(
            {str(item) for item in list(raw.get("failure_regions") or []) if str(item)}
        ),
        "validation_reason_codes": sorted(
            {
                str(item).split(":", 1)[0]
                for item in list(
                    raw.get("validation_reason_codes")
                    or raw.get("validation_reasons")
                    or []
                )
                if str(item)
            }
        ),
    }


class ContextEngineeringLayer:
    """
    Build the narrow context each specialist actually needs instead of sharing a
    single dirty state object.
    """

    def build(
        self,
        request: AlignmentRequest,
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        difficulty: DifficultyReport,
        route: RouteReport,
        guard_reasons: List[str] | None = None,
    ) -> SpecialistContext:
        failure_regions = self._infer_failure_regions(snapshot, diagnostics, guard_reasons or [])
        top_templates = [
            {
                "seqname": item.get("seqname"),
                "rank": item.get("rank"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
                "metadata_bonus": item.get("metadata_bonus"),
                "anticodon_match": item.get("anticodon_match"),
                "isotype_match": item.get("isotype_match"),
                "template_isotype": item.get("template_isotype"),
                "template_anticodon": item.get("template_anticodon"),
                "family_match_rank": item.get("family_match_rank"),
                "family_match_label": item.get("family_match_label"),
                "family_rescue_selected": item.get("family_rescue_selected"),
            }
            for item in list(snapshot.template_candidates or [])[:5]
        ]
        return SpecialistContext(
            target_length=len(request.normalized_target_seq()),
            anticodon_hint=str(request.anticode or ""),
            use_llm=bool(request.use_llm),
            llm_reasoning=bool(request.llm_reasoning),
            selected_template=snapshot.template_name,
            regional_llm_candidate_enabled=self._runtime_flag(
                request,
                "AGENT_ENABLE_LLM_POSTCHECK",
                default=False,
            ),
            deterministic_boundary_enabled=self._runtime_flag(
                request,
                "AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT",
                default=True,
            ),
            template_score=diagnostics.template_score,
            template_margin=diagnostics.template_margin,
            preferred_isotype=str(diagnostics.raw_signals.get("preferred_isotype") or ""),
            preferred_anticodon=str(diagnostics.raw_signals.get("preferred_anticodon") or ""),
            selected_template_isotype=str(diagnostics.raw_signals.get("selected_template_isotype") or ""),
            selected_template_anticodon=str(diagnostics.raw_signals.get("selected_template_anticodon") or ""),
            selected_family_match_rank=int(diagnostics.raw_signals.get("selected_family_match_rank") or 0),
            top_family_match_rank=int(diagnostics.raw_signals.get("top_family_match_rank") or 0),
            strong_family_candidates=int(diagnostics.raw_signals.get("strong_family_candidates") or 0),
            top_templates=top_templates,
            warning_types=list(diagnostics.warning_types),
            empty_regions=list(diagnostics.empty_regions),
            boundary_resolved=bool(diagnostics.boundary_resolved),
            segmentation_status=str(diagnostics.segmentation_status),
            difficulty_tier=str(difficulty.tier),
            difficulty_score=float(difficulty.difficulty_score),
            route=str(route.route),
            route_confidence=float(route.confidence),
            route_reasons=list(route.reasons),
            failure_regions=failure_regions,
            disputed_regions=self._disputed_regions(snapshot),
            canonical_eligible=bool(route.in_canonical_benchmark and not route.out_of_scope),
            cm_tool_enabled=self._cm_tool_enabled(request),
            input_type=str(request.input_type or "unknown"),
            domain_hint=str(request.domain_hint or ""),
            intron_status=str(request.intron_status or "unknown"),
            numbering_backend=str((snapshot.raw_meta or {}).get("numbering_backend") or "legacy"),
            validation_reasons=list(guard_reasons or []),
            cm_evidence_present=bool(
                (snapshot.raw_meta or {}).get("numbering_backend") == "cmalign"
                or (snapshot.raw_meta or {}).get("cm_tool")
            ),
            literature_tool_enabled=self._runtime_flag(
                request,
                "AGENT_ENABLE_LITERATURE_TOOL",
                default=True,
            ),
            coordinate_proposal_enabled=self._runtime_flag(
                request,
                "AGENT_ENABLE_COORDINATE_PROPOSAL",
                default=False,
            ),
        )

    @staticmethod
    def _flag_enabled(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _runtime_flag(
        cls,
        request: AlignmentRequest,
        key: str,
        *,
        default: bool,
    ) -> bool:
        values = dict(request.runtime_values_override or {})
        if key not in values:
            return bool(default)
        value = values.get(key)
        if isinstance(value, bool):
            return value
        return cls._flag_enabled(value)

    @classmethod
    def _cm_tool_enabled(cls, request: AlignmentRequest) -> bool:
        values = dict(request.runtime_values_override or {})
        strategy = str(
            values.get("AGENT_CANONICAL_ROOT_STRATEGY") or "agent_toolbox"
        ).strip().lower()
        return (
            bool(request.use_llm) and strategy == "agent_toolbox"
        ) or cls._flag_enabled(values.get("AGENT_ENABLE_CM_TOOL"))

    @staticmethod
    def _disputed_regions(snapshot: RootSnapshot) -> List[str]:
        """Regions an assessor questions on a row that already passes the guards.

        These are the same three checks the soft-risk review schedules, asked
        here so a specialist can see that a legal candidate is still disputed.
        """
        from ..evaluation.acceptor_tail_reconciliation import (
            assess_acceptor_tail_ambiguity,
        )
        from ..evaluation.local_coordinate_ambiguity import (
            assess_local_coordinate_ambiguity,
        )
        from ..evaluation.variable_boundary_reconciliation import (
            assess_variable_boundary_ambiguity,
        )

        regions: List[str] = []
        for assess in (
            assess_acceptor_tail_ambiguity,
            assess_variable_boundary_ambiguity,
            assess_local_coordinate_ambiguity,
        ):
            try:
                evidence = assess(snapshot)
            except Exception:  # an assessor must never break planning
                continue
            if not evidence:
                continue
            region = str(dict(evidence).get("region") or "").strip()
            if region and region not in regions:
                regions.append(region)
        return sorted(regions)

    @staticmethod
    def _infer_failure_regions(
        snapshot: RootSnapshot,
        diagnostics: DiagnosticsReport,
        guard_reasons: List[str],
    ) -> List[str]:
        regions: List[str] = []
        for item in diagnostics.empty_regions:
            if item not in regions:
                regions.append(item)
        for warning in diagnostics.warning_types:
            if warning == "boundary":
                regions.extend(["D loop + D stem", "Anticodon loop + Anticodon stem", "Variable loop"])
            elif warning == "anticodon":
                regions.append("Anticodon loop + Anticodon stem")
            elif warning == "insertion":
                regions.extend(["Variable loop", "T loop + T stem"])
        for reason in list(guard_reasons or []):
            text = str(reason or "")
            if not text:
                continue
            if "variable" in text or text.startswith("V"):
                regions.append("Variable loop")
                regions.extend(ContextEngineeringLayer._extra_regions_for_variable_reason(text))
            elif "anticodon" in text or any(token in text for token in ("34", "35", "36")):
                regions.append("Anticodon loop + Anticodon stem")
            elif "acceptor" in text:
                regions.append("Aminoacyl arm")
            elif "d_stem" in text or text.startswith("d_optional"):
                regions.append("D loop + D stem")
            elif "t_stem" in text:
                regions.append("T loop + T stem")
            elif text.startswith("cca") or any(token in text for token in ("74", "75", "76")):
                regions.append("Aminoacyl arm 3' end")
            elif "minus1" in text or text.startswith("-1"):
                regions.append("Aminoacyl arm 5' end")
        seen = set()
        out: List[str] = []
        for region in regions:
            if region in seen:
                continue
            seen.add(region)
            out.append(region)
        return out

    @staticmethod
    def _extra_regions_for_variable_reason(text: str) -> List[str]:
        if not text.startswith("variable_topology_"):
            return []
        labels = [part for part in re.split(r"[:\-]", text) if part]
        extras: List[str] = []
        if any(re.match(r"^(46|47|47i\d+|48|49|50|51|52|53|54|55|56|57|58|59|60|61|62|63|64|65)$", label) for label in labels):
            extras.append("T loop + T stem")
        if any(re.match(r"^(40|41|42|43|44|45)$", label) for label in labels):
            extras.append("Anticodon loop + Anticodon stem")
        return extras


__all__ = ["ContextEngineeringLayer", "SpecialistContext"]
