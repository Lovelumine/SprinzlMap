from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class DiagnosticsReport:
    """
    Structured online diagnostics extracted from the root snapshot.
    """

    warning_count: int = 0
    warning_types: List[str] = field(default_factory=list)
    empty_regions: List[str] = field(default_factory=list)
    trnascan_hit: bool = True
    boundary_resolved: bool = False
    segmentation_status: str = ""
    template_score: float | None = None
    template_margin: float | None = None
    raw_signals: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DifficultyReport:
    """
    Online difficulty assessment. No gold labels are used here.
    """

    difficulty_score: float
    tier: str
    hard_case: bool
    reasons: List[str] = field(default_factory=list)


@dataclass(slots=True)
class RouteReport:
    """
    Specialized handling route assessment.
    """

    route: str = "canonical"
    confidence: float = 0.0
    in_canonical_benchmark: bool = True
    out_of_scope: bool = False
    reasons: List[str] = field(default_factory=list)


__all__ = ["DiagnosticsReport", "DifficultyReport", "RouteReport"]
