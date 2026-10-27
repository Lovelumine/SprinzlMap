from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ActionBudgetCost:
    llm_calls_reserved: int = 0
    expected_wall_time_sec: int = 0


@dataclass(slots=True)
class CandidateAction:
    """
    Future v2 typed action contract.
    """

    action_type: str
    action_id: str = ""
    specialist: str = ""
    scope: str = "snapshot"
    target_region: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    budget_cost: ActionBudgetCost = field(default_factory=ActionBudgetCost)
    allowed_regions: List[str] = field(default_factory=list)
    success_condition: str = ""
    rollback_condition: str = ""


@dataclass(slots=True)
class ActionExecutionTrace:
    action_type: str
    status: str
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


__all__ = ["ActionBudgetCost", "CandidateAction", "ActionExecutionTrace"]
