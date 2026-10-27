# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any, Dict

from ..core.state import TraceEvent

logger = logging.getLogger(__name__)

class AgentTraceLogger:
    """
    Handles logging of Agent TraceEvents to disk in JSONL format.
    Matches the requirements of 9.5 Trace / Replay Layer in Phase 0.
    """
    
    def __init__(self, log_dir: str = "logs/agent_traces"):
        self.log_dir = log_dir
        # Ensure dir exists in a real implementation
        
    def log_event(self, event: TraceEvent, run_id: str) -> None:
        """
        Appends the TraceEvent to logs/agent_traces/<run_id>/<case_id>.jsonl
        """
        # MVP: Output trace via logger for debugging
        event_dict = asdict(event)
        logger.debug(f"[AgentTrace] EVENT: {json.dumps(event_dict, ensure_ascii=False)}")
        
__all__ = ["AgentTraceLogger"]
