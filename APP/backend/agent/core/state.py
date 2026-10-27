# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ==============================================================================
# Domain Models for Agent Search
# These classes strictly follow the JSON schema in AGENT_REFACTOR_GOALS.md
# ==============================================================================

@dataclass
class TemplateCandidate:
    template_name: str
    source: str
    rank: int
    similarity: float

@dataclass
class AnnotationContext:
    annotation_source: str
    empty_count: int
    empty_regions: List[str]
    boundary_resolved: bool

@dataclass
class BranchConfig:
    template_name: str
    use_llm: bool
    llm_reasoning: bool
    runtime_overrides: Dict[str, Any]

@dataclass
class AgentBudget:
    max_branches: int = 8
    max_llm_calls: int = 16
    max_evaluations: int = 16
    max_wall_time_sec: int = 120

@dataclass
class ActionBudgetCost:
    llm_calls_reserved: int
    expected_wall_time_sec: int

@dataclass
class AgentAction:
    action_id: str
    branch_id: str
    parent_branch_id: str
    action_type: str
    params: Dict[str, Any]
    reason: str
    budget_cost: ActionBudgetCost

@dataclass
class DeltaScore:
    pair_f1: float = 0.0
    map_acc: float = 0.0
    gap_f1: float = 0.0

@dataclass
class AgentEvaluation:
    branch_id: str
    hard_valid: bool = False
    pair_f1: float = 0.0
    map_acc: float = 0.0
    gap_f1: float = 0.0
    delta_vs_baseline: DeltaScore = field(default_factory=DeltaScore)
    delta_vs_best: DeltaScore = field(default_factory=DeltaScore)
    reject_reasons: List[str] = field(default_factory=list)
    accept_reasons: List[str] = field(default_factory=list)
    selected_as_best: bool = False

@dataclass
class ObservationContext:
    warning_count: int
    rollback_triggered: bool
    llm_regions_touched: List[str]

@dataclass
class TraceEvent:
    trace_id: str
    case_id: str
    event_index: int
    timestamp: str  # ISO format string
    branch_id: str
    parent_branch_id: str
    stage: str
    action_ref: Optional[str]
    summary: str
    observation: ObservationContext
    evaluation_ref: Optional[str]
    termination_hint: Optional[str]

@dataclass
class AlignmentAgentState:
    case_id: str
    target_seq: str
    anticodon_hint: str
    template_candidates: List[TemplateCandidate]
    
    baseline_result: Optional[AgentEvaluation]
    annotation_context: AnnotationContext
    
    current_branch_id: str
    current_config: BranchConfig
    
    action_history: List[AgentAction] = field(default_factory=list)
    best_result_so_far: Optional[AgentEvaluation] = None
    best_score_so_far: Optional[float] = None
    
    budget: AgentBudget = field(default_factory=AgentBudget)
    trace_id: str = ""
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)

__all__ = [
    "TemplateCandidate",
    "AnnotationContext",
    "BranchConfig",
    "AgentBudget",
    "ActionBudgetCost",
    "AgentAction",
    "DeltaScore",
    "AgentEvaluation",
    "ObservationContext",
    "TraceEvent",
    "AlignmentAgentState"
]
