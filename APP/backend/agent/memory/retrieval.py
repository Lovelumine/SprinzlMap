from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Sequence

from .store import PatternEntry, PatternStore, VERIFIED_SPLIT


MEMORY_ACTION_ALLOWLIST = frozenset(
    {
        "align_numbering_cm",
        "align_legacy_reference",
        "refine_boundary",
        "repair_variable_topology_locally",
        "repair_region_with_dp_only",
        "reanchor_anticodon",
        "compact_insertions",
    }
)


def _normalized_values(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value if str(item)}
    if value in (None, ""):
        return set()
    return {str(value)}


def _safe_actions(actions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    safe: List[Dict[str, Any]] = []
    for raw in actions:
        action_type = str(raw.get("action_type") or "").strip()
        if action_type not in MEMORY_ACTION_ALLOWLIST:
            continue
        params = dict(raw.get("params") or {})
        focus_regions = [
            str(item)
            for item in list(params.get("focus_regions") or [])
            if str(item)
        ]
        safe_params = {"focus_regions": focus_regions} if focus_regions else {}
        safe.append(
            {
                "action_type": action_type,
                "params": safe_params,
                "allowed_regions": [
                    str(item)
                    for item in list(raw.get("allowed_regions") or [])
                    if str(item)
                ],
                "success_condition": str(raw.get("success_condition") or ""),
                "rollback_condition": str(raw.get("rollback_condition") or ""),
            }
        )
    return safe


class PatternRetriever:
    """Retrieve frozen, answer-free development experience by evidence state."""

    def __init__(self, store: PatternStore) -> None:
        self.store = store

    def retrieve(
        self,
        *,
        trigger_signature: Mapping[str, Any],
        failure_regions: Sequence[str] = (),
        top_k: int = 3,
        min_confidence: float = 0.6,
    ) -> List[PatternEntry]:
        scored: List[tuple[float, PatternEntry]] = []
        for pattern in self.store.load():
            if pattern.confidence < min_confidence:
                continue
            score = self._compute_relevance(
                pattern,
                trigger_signature=trigger_signature,
                failure_regions=failure_regions,
            )
            if score > 0:
                scored.append((score * float(pattern.confidence), pattern))
        scored.sort(key=lambda item: (-item[0], item[1].pattern_id))
        return [pattern for _, pattern in scored[: max(0, int(top_k))]]

    @staticmethod
    def _compute_relevance(
        pattern: PatternEntry,
        *,
        trigger_signature: Mapping[str, Any],
        failure_regions: Sequence[str],
    ) -> float:
        weighted_fields = {
            "route": 0.20,
            "difficulty_tier": 0.15,
            "numbering_backend": 0.15,
            "domain_group": 0.10,
            "length_bin": 0.05,
            "input_type": 0.05,
            "intron_status": 0.05,
            "cm_confidence_bin": 0.10,
            "warning_types": 0.05,
            "validation_reason_codes": 0.05,
        }
        score = 0.0
        compared = 0.0
        for field, weight in weighted_fields.items():
            expected = _normalized_values(pattern.trigger_signature.get(field))
            observed = _normalized_values(trigger_signature.get(field))
            if not expected:
                continue
            compared += weight
            if expected & observed:
                score += weight
        expected_regions = set(pattern.regions)
        observed_regions = set(failure_regions)
        if expected_regions:
            compared += 0.15
            if expected_regions & observed_regions:
                score += 0.15 * (
                    len(expected_regions & observed_regions) / len(expected_regions)
                )
        if compared <= 0:
            return 0.0
        return min(1.0, score / compared)

    def record_verified_pattern(
        self,
        *,
        trigger_signature: Mapping[str, Any],
        regions: Sequence[str],
        recommended_actions: Sequence[Mapping[str, Any]],
        score_gain: float,
        dataset_id: str,
        verification_source: str = "gold_and_harness",
        verified_successes: int = 1,
        verified_trials: int = 1,
        source_case_count: int = 1,
    ) -> PatternEntry:
        actions = _safe_actions(recommended_actions)
        if not actions:
            raise ValueError("no Harness-allowed memory action")
        canonical = json.dumps(
            {
                "trigger_signature": dict(trigger_signature),
                "regions": sorted({str(item) for item in regions if str(item)}),
                "actions": actions,
                "dataset_id": str(dataset_id or ""),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        pattern_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        trials = max(1, int(verified_trials))
        successes = max(1, min(int(verified_successes), trials))
        entry = PatternEntry(
            pattern_id=pattern_id,
            trigger_signature=dict(trigger_signature),
            regions=sorted({str(item) for item in regions if str(item)}),
            recommended_actions=actions,
            verified_successes=successes,
            verified_trials=trials,
            mean_score_gain=float(score_gain),
            confidence=successes / trials,
            dataset_id=str(dataset_id or ""),
            split_name=VERIFIED_SPLIT,
            verification_source=str(verification_source or ""),
            source_case_count=max(1, int(source_case_count)),
        )
        self.store.append(entry)
        return entry


__all__ = ["MEMORY_ACTION_ALLOWLIST", "PatternRetriever"]
