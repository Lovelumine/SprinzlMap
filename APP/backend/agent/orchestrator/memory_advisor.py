from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from backend.domain.alignment.runtime_helpers import (
    load_runtime_values,
    runtime_bool,
    runtime_str,
)

from ..memory.retrieval import PatternRetriever
from ..memory.store import DEFAULT_MEMORY_PATH, MemoryIntegrityError, PatternStore
from .context_engineering import (
    SpecialistContext,
    answer_free_context_signature,
)


class MemoryAdvisor:
    """Read verified development experience without exposing case answers."""

    def __init__(self) -> None:
        self._stores: Dict[Tuple[str, str, bool], PatternStore] = {}
        self.last_audit: Dict[str, Any] = {
            "mode": "off",
            "status": "disabled",
            "hit_count": 0,
        }

    def mark_not_consulted(
        self,
        runtime_values_override: Mapping[str, Any] | None = None,
    ) -> None:
        runtime_values = load_runtime_values(dict(runtime_values_override or {}))
        self.last_audit = {
            "mode": runtime_str(
                "AGENT_MEMORY_MODE",
                "off",
                runtime_values=runtime_values,
            ).strip().lower(),
            "status": "not_consulted",
            "hit_count": 0,
        }

    def retrieve(
        self,
        context: SpecialistContext,
        runtime_values_override: Mapping[str, Any] | None = None,
    ) -> List[Dict[str, object]]:
        runtime_values = load_runtime_values(dict(runtime_values_override or {}))
        mode = runtime_str(
            "AGENT_MEMORY_MODE",
            "off",
            runtime_values=runtime_values,
        ).strip().lower()
        path = runtime_str(
            "AGENT_MEMORY_PATH",
            DEFAULT_MEMORY_PATH,
            runtime_values=runtime_values,
        )
        require_frozen = runtime_bool(
            "AGENT_MEMORY_REQUIRE_FROZEN",
            True,
            runtime_values=runtime_values,
        )
        if mode == "off":
            self.last_audit = {
                "mode": "off",
                "status": "disabled",
                "hit_count": 0,
            }
            return []
        if mode != "read_only":
            self.last_audit = {
                "mode": mode,
                "status": "invalid_mode",
                "hit_count": 0,
            }
            return []

        store_key = (path, mode, bool(require_frozen))
        store = self._stores.get(store_key)
        if store is None:
            store = PatternStore(
                path,
                mode=mode,
                require_frozen=require_frozen,
            )
            self._stores[store_key] = store
        retriever = PatternRetriever(store)
        signature = self._trigger_signature(context)
        try:
            patterns = retriever.retrieve(
                trigger_signature=signature,
                failure_regions=context.failure_regions,
                top_k=3,
                min_confidence=0.6,
            )
        except MemoryIntegrityError as exc:
            self.last_audit = {
                "mode": mode,
                "status": "rejected",
                "reason": str(exc),
                "hit_count": 0,
                "path": path,
            }
            return []

        hits: List[Dict[str, object]] = []
        for pattern in patterns:
            hits.append(
                {
                    "pattern_id": pattern.pattern_id,
                    "pattern_type": pattern.pattern_type,
                    "regions": list(pattern.regions),
                    "historical_gain": pattern.historical_gain,
                    "confidence": pattern.confidence,
                    "verified_trials": pattern.verified_trials,
                    "source_case_count": pattern.source_case_count,
                    "recommended_actions": list(pattern.recommended_actions),
                }
            )
        self.last_audit = {
            **store.snapshot_audit,
            "mode": mode,
            "status": "ready",
            "hit_count": len(hits),
        }
        return hits

    @staticmethod
    def _trigger_signature(context: SpecialistContext) -> Dict[str, Any]:
        return answer_free_context_signature(context)


__all__ = ["MemoryAdvisor"]
