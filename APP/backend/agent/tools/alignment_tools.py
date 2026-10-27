# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Any, Dict

from ..core.state import AgentAction, ActionBudgetCost

logger = logging.getLogger(__name__)

class AlignmentToolRegistry:
    """
    Registry for validated, typed actions that the Agent Planner can propose.
    Maps to `8.3 Action` from AGENT_REFACTOR_GOALS.md.
    """
    
    @staticmethod
    def create_action_select_template(
        action_id: str, branch_id: str, parent_branch_id: str, 
        template_name: str, rank: int, similarity: float
    ) -> AgentAction:
        return AgentAction(
            action_id=action_id,
            branch_id=branch_id,
            parent_branch_id=parent_branch_id,
            action_type="select_template",
            params={"template_name": template_name, "rank": rank, "similarity": similarity},
            reason=f"Trying alternative template ranked {rank} with similarity {similarity:.2f}",
            budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=2)
        )

    @staticmethod
    def create_action_adjust_boundary_policy(
        action_id: str, branch_id: str, parent_branch_id: str, 
        preset_name: str, reason: str
    ) -> AgentAction:
        return AgentAction(
            action_id=action_id,
            branch_id=branch_id,
            parent_branch_id=parent_branch_id,
            action_type="adjust_boundary_policy",
            params={"preset_name": preset_name},
            reason=reason,
            budget_cost=ActionBudgetCost(llm_calls_reserved=0, expected_wall_time_sec=1)
        )

    @staticmethod
    def create_action_swap_prompt_variant(
        action_id: str, branch_id: str, parent_branch_id: str, 
        region: str, prompt_id: str, reason: str
    ) -> AgentAction:
        return AgentAction(
            action_id=action_id,
            branch_id=branch_id,
            parent_branch_id=parent_branch_id,
            action_type="swap_prompt_variant",
            params={"region": region, "prompt_id": prompt_id},
            reason=reason,
            budget_cost=ActionBudgetCost(llm_calls_reserved=2, expected_wall_time_sec=8)
        )

__all__ = ["AlignmentToolRegistry"]
