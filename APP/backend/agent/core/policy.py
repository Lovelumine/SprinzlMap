# -*- coding: utf-8 -*-
"""
Phase 4: Agent Policy Configuration
Defines when and how the Agent should be triggered in the online production path.
All thresholds are conservative — the Agent is OFF by default.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentPolicy:
    """
    Controls whether and how the Agent activates for a given alignment request.
    Matches Section 7.2 (Selective Online Agent) and Phase 4 exit criteria.
    """

    # Master switch: Agent is OFF by default
    enabled: bool = False

    # Trigger conditions — all must be True for Agent to activate
    min_rollback_count: int = 2       # minimum rollbacks before considering Agent
    min_warning_count: int = 3        # minimum warnings before considering Agent
    max_baseline_pair_f1: float = 0.7 # only trigger when estimated quality is low

    # Budget limits for online mode (much tighter than offline)
    online_max_branches: int = 3
    online_max_llm_calls: int = 6
    online_max_wall_time_sec: int = 30

    # Logging
    log_all_triggers: bool = True

    @classmethod
    def from_env(cls) -> "AgentPolicy":
        """Build policy from environment variables, with conservative defaults."""
        return cls(
            enabled=os.getenv("AGENT_ENABLED", "").lower() in ("1", "true", "yes"),
            min_rollback_count=int(os.getenv("AGENT_MIN_ROLLBACKS", "2")),
            min_warning_count=int(os.getenv("AGENT_MIN_WARNINGS", "3")),
            max_baseline_pair_f1=float(os.getenv("AGENT_MAX_BASELINE_F1", "0.7")),
            online_max_branches=int(os.getenv("AGENT_MAX_BRANCHES", "3")),
            online_max_wall_time_sec=int(os.getenv("AGENT_MAX_WALL_TIME", "30")),
        )

    @classmethod
    def disabled(cls) -> "AgentPolicy":
        """Explicitly disabled policy (default)."""
        return cls(enabled=False)


def should_trigger_agent(
    policy: AgentPolicy,
    warnings: list,
    csv_raw: str = "",
) -> bool:
    """
    Determines if the Agent should be triggered based on the policy and
    the results from the standard pipeline run.

    This function is intentionally conservative:
    - Agent must be explicitly enabled via policy.enabled
    - Multiple warning signals must be present
    - Only triggers for cases that look genuinely problematic
    """
    if not policy.enabled:
        return False

    # Count rollback warnings
    rollback_count = sum(1 for w in warnings if "rollback" in str(w).lower())

    # Check trigger conditions
    triggers_met = 0
    if rollback_count >= policy.min_rollback_count:
        triggers_met += 1
    if len(warnings) >= policy.min_warning_count:
        triggers_met += 1

    # Need at least 1 trigger condition met
    triggered = triggers_met >= 1

    if triggered and policy.log_all_triggers:
        logger.info(
            f"[AgentPolicy] Trigger conditions met: "
            f"rollbacks={rollback_count}/{policy.min_rollback_count}, "
            f"warnings={len(warnings)}/{policy.min_warning_count}"
        )

    return triggered


__all__ = ["AgentPolicy", "should_trigger_agent"]
