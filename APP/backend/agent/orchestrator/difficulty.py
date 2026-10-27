from __future__ import annotations

from typing import List

from ..contracts import DiagnosticsReport, DifficultyReport, RootSnapshot


class DifficultyAssessor:
    """
    Lightweight online difficulty heuristic for candidate triage.
    It intentionally uses only signals already exposed by the current pipeline.
    """

    def build_diagnostics(self, snapshot: RootSnapshot) -> DiagnosticsReport:
        meta = dict(snapshot.raw_meta or {})
        annotation_meta = dict(meta.get("annotation_meta") or {})
        warning_types = [self._classify_warning(x) for x in snapshot.warnings]
        template_candidates = list(meta.get("template_candidates") or [])

        template_score = self._as_float(meta.get("template_score"))
        template_margin = self._as_float(meta.get("template_margin"))
        preferred_isotype = str(annotation_meta.get("trnascan_isotype", "") or "").strip()
        preferred_anticodon = str(annotation_meta.get("trnascan_anticodon", "") or "").strip().upper().replace("T", "U")
        selected_candidate = self._select_template_candidate(snapshot, template_candidates)
        selected_family_rank = int((selected_candidate or {}).get("family_match_rank") or 0)
        top_family_rank = max(
            [selected_family_rank] + [int(item.get("family_match_rank") or 0) for item in template_candidates]
        )
        family_supported_candidates = sum(
            1 for item in template_candidates if int(item.get("family_match_rank") or 0) >= 1
        )
        strong_family_candidates = sum(
            1 for item in template_candidates if int(item.get("family_match_rank") or 0) >= 2
        )
        return DiagnosticsReport(
            warning_count=len(snapshot.warnings),
            warning_types=warning_types,
            empty_regions=list(annotation_meta.get("empty_regions") or []),
            trnascan_hit=bool(annotation_meta.get("trnascan_hit", True)),
            boundary_resolved=bool(annotation_meta.get("boundary_resolved", False)),
            segmentation_status=str(annotation_meta.get("segmentation_status", "") or ""),
            template_score=template_score,
            template_margin=template_margin,
            raw_signals={
                "warning_count": len(snapshot.warnings),
                "row_lengths": [
                    len(snapshot.numbering_row),
                    len(snapshot.template_row),
                    len(snapshot.sequence_row),
                ],
                "preferred_isotype": preferred_isotype,
                "preferred_anticodon": preferred_anticodon,
                "selected_template_isotype": str((selected_candidate or {}).get("template_isotype", "") or meta.get("template_isotype") or ""),
                "selected_template_anticodon": str((selected_candidate or {}).get("template_anticodon", "") or meta.get("template_anticodon") or ""),
                "selected_family_match_rank": selected_family_rank,
                "selected_family_match_label": str((selected_candidate or {}).get("family_match_label", meta.get("template_family_match_label", "none")) or "none"),
                "selected_isotype_match": int((selected_candidate or {}).get("isotype_match") or 0),
                "selected_anticodon_match": int((selected_candidate or {}).get("anticodon_match") or 0),
                "selected_family_rescue_selected": bool((selected_candidate or {}).get("family_rescue_selected", meta.get("template_family_rescue_selected", False))),
                "top_family_match_rank": top_family_rank,
                "family_rank_gap": max(0, top_family_rank - selected_family_rank),
                "family_supported_candidates": family_supported_candidates,
                "strong_family_candidates": strong_family_candidates,
                "top_template_names": [str(item.get("seqname") or "") for item in template_candidates[:5]],
            },
        )

    def assess(self, diagnostics: DiagnosticsReport, snapshot: RootSnapshot) -> DifficultyReport:
        score = 0.0
        reasons: List[str] = []

        if diagnostics.warning_count >= 3:
            score += 0.35
            reasons.append("warning_count_high")
        elif diagnostics.warning_count > 0:
            score += 0.15
            reasons.append("warning_count_nonzero")

        if diagnostics.empty_regions:
            score += 0.25
            reasons.append("empty_regions_present")

        if not diagnostics.trnascan_hit:
            score += 0.25
            reasons.append("trnascan_missing")

        if diagnostics.boundary_resolved:
            score += 0.15
            reasons.append("boundary_was_refined")

        selected_family_rank = int(diagnostics.raw_signals.get("selected_family_match_rank") or 0)
        top_family_rank = int(diagnostics.raw_signals.get("top_family_match_rank") or selected_family_rank)
        strong_family_candidates = int(diagnostics.raw_signals.get("strong_family_candidates") or 0)
        family_rank_gap = int(diagnostics.raw_signals.get("family_rank_gap") or 0)
        if diagnostics.trnascan_hit:
            if selected_family_rank == 0:
                score += 0.35
                reasons.append("template_family_mismatch")
            elif selected_family_rank == 1:
                score += 0.12
                reasons.append("template_partial_family_match")
            if family_rank_gap > 0:
                score += 0.28 if top_family_rank >= 2 and selected_family_rank <= 1 else 0.15
                reasons.append("better_family_candidate_available")
            if strong_family_candidates == 0:
                score += 0.18
                reasons.append("no_strong_family_candidates")

        row_lengths = diagnostics.raw_signals.get("row_lengths") or []
        if len(set(row_lengths)) > 1:
            score += 0.4
            reasons.append("row_length_inconsistent")

        if not snapshot.numbering_row or not snapshot.sequence_row:
            score += 0.5
            reasons.append("csv_rows_missing")

        if (
            diagnostics.template_margin is not None
            and diagnostics.template_margin <= 0.25
            and top_family_rank > selected_family_rank
            and top_family_rank >= 2
        ):
            score += 0.12
            reasons.append("template_tie_under_family_hints")

        hard_case = score >= 0.5
        if score >= 0.75:
            tier = "hard"
        elif score >= 0.3:
            tier = "moderate"
        else:
            tier = "easy"

        return DifficultyReport(
            difficulty_score=round(score, 4),
            tier=tier,
            hard_case=hard_case,
            reasons=reasons,
        )

    @staticmethod
    def _classify_warning(message: str) -> str:
        text = str(message or "").strip().lower()
        if "anticodon" in text:
            return "anticodon"
        if "boundary" in text:
            return "boundary"
        if "rollback" in text:
            return "rollback"
        if "llm" in text:
            return "llm"
        if "insertion" in text:
            return "insertion"
        return "generic"

    @staticmethod
    def _as_float(value) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _select_template_candidate(snapshot: RootSnapshot, template_candidates: list[dict]) -> dict | None:
        selected_name = str(snapshot.template_name or "")
        for item in template_candidates:
            if str(item.get("seqname") or "") == selected_name:
                return item
        if template_candidates:
            return template_candidates[0]
        return None


__all__ = ["DifficultyAssessor"]
