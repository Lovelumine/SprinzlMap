from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class RootSnapshot:
    """
    Immutable root candidate produced by a primary scientific numbering tool.
    """

    template_name: str
    csv_content: str
    warnings: List[str] = field(default_factory=list)
    numbering_row: List[str] = field(default_factory=list)
    template_row: List[str] = field(default_factory=list)
    sequence_row: List[str] = field(default_factory=list)
    structure_view: Dict[str, Any] = field(default_factory=dict)
    llm_trace_view: Dict[str, Any] = field(default_factory=dict)
    raw_meta: Dict[str, Any] = field(default_factory=dict)
    template_candidates: List[Dict[str, Any]] = field(default_factory=list)
    template_score: float | None = None
    template_margin: float | None = None
    root_source: str = "standard_pipeline"
    snapshot_id: str = "root"
    parent_snapshot_id: Optional[str] = None
    source_action: str = "root_snapshot"


@dataclass(slots=True)
class AlignmentSnapshot:
    """
    Branch-local snapshot used by future Agent search.
    """

    branch_id: str
    parent_branch_id: Optional[str]
    root_snapshot: RootSnapshot
    notes: List[str] = field(default_factory=list)
    action_trace: List[Dict[str, Any]] = field(default_factory=list)


__all__ = ["RootSnapshot", "AlignmentSnapshot"]
