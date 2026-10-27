from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .diagnostics import DiagnosticsReport, DifficultyReport, RouteReport
from .snapshot import RootSnapshot


@dataclass(slots=True)
class SearchResult:
    """
    Result contract for the Agent v2 orchestrator.
    """

    root_snapshot: RootSnapshot
    best_snapshot: RootSnapshot
    diagnostics: DiagnosticsReport
    difficulty: DifficultyReport
    route: RouteReport
    root_diagnostics: DiagnosticsReport
    root_difficulty: DifficultyReport
    root_route: RouteReport
    expanded: bool = False
    trace_events: List[Dict[str, Any]] = field(default_factory=list)
    branch_summaries: List[Dict[str, Any]] = field(default_factory=list)
    context_summary: Dict[str, Any] = field(default_factory=dict)
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)
    work_state_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AlignmentResponse:
    template_name: str
    csv_content: str
    warnings: List[str]
    meta: Dict[str, Any] = field(default_factory=dict)


__all__ = ["SearchResult", "AlignmentResponse"]
