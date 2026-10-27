from __future__ import annotations

from ..contracts import DiagnosticsReport, RootSnapshot, RouteReport


class SpecializedRouteAssessor:
    """
    Conservative specialized handling classifier.
    It is intentionally conservative: when subtype evidence is weak, stay on the
    canonical path and report the uncertainty instead of over-routing.
    """

    def assess(self, snapshot: RootSnapshot, diagnostics: DiagnosticsReport) -> RouteReport:
        meta = dict(snapshot.raw_meta or {})
        annotation_meta = dict(meta.get("annotation_meta") or {})
        special_meta = dict(meta.get("agent_specialized_status") or {})
        template_name = str(snapshot.template_name or "").lower()
        annotation_source = str(annotation_meta.get("annotation_source", "") or "").lower()
        segmentation_errors = str(annotation_meta.get("segmentation_errors", "") or "").lower()

        reasons: list[str] = []
        route = "canonical"
        confidence = 0.0
        in_canonical_benchmark = True
        out_of_scope = False

        forced_route = str(special_meta.get("forced_route", "") or "")
        if forced_route:
            route = forced_route
            confidence = 0.92
            in_canonical_benchmark = bool(special_meta.get("in_canonical_benchmark", forced_route == "canonical"))
            out_of_scope = bool(special_meta.get("out_of_scope", forced_route == "out_of_scope"))
            reasons.append(f"forced_route:{forced_route}")

        handler = str(special_meta.get("handler", "") or "")
        if handler == "route_to_out_of_scope_handler":
            route = "out_of_scope"
            confidence = 1.0
            in_canonical_benchmark = False
            out_of_scope = True
            reasons.append("explicit_out_of_scope_handler")
        elif handler == "route_to_pseudogene_filter":
            route = "weak_or_noncanonical"
            confidence = 0.95
            in_canonical_benchmark = False
            out_of_scope = True
            reasons.append("explicit_pseudogene_filter")
        elif handler == "route_to_degenerate_mt_handler":
            route = "mitochondrial"
            confidence = 0.95
            in_canonical_benchmark = False
            out_of_scope = True
            reasons.append("explicit_degenerate_mt_handler")

        if not forced_route and ("mito" in template_name or "mitochond" in template_name or "mito" in annotation_source):
            route = "mitochondrial"
            confidence = 0.8
            in_canonical_benchmark = False
            reasons.append("mitochondrial_signal")

        if not forced_route and ("intron" in segmentation_errors or "bhb" in segmentation_errors):
            route = "archaeal_ncintron"
            confidence = max(confidence, 0.75)
            in_canonical_benchmark = False
            reasons.append("noncanonical_intron_signal")

        if not forced_route and not diagnostics.trnascan_hit and diagnostics.empty_regions:
            route = "weak_or_noncanonical"
            confidence = max(confidence, 0.7)
            in_canonical_benchmark = False
            reasons.append("missing_trnascan_and_empty_regions")

        if not forced_route and (len(diagnostics.empty_regions) > 1 or not snapshot.numbering_row or not snapshot.sequence_row):
            route = "out_of_scope"
            confidence = max(confidence, 0.9)
            in_canonical_benchmark = False
            out_of_scope = True
            reasons.append("cannot_form_stable_root_snapshot")

        return RouteReport(
            route=route,
            confidence=round(confidence, 4),
            in_canonical_benchmark=in_canonical_benchmark,
            out_of_scope=out_of_scope,
            reasons=reasons,
        )


__all__ = ["SpecializedRouteAssessor"]
