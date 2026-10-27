# -*- coding: utf-8 -*-
"""
Phase 1: Branch Executor
Runs a single alignment branch by invoking the existing pipeline
with branch-local configuration overrides.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .state import (
    AgentAction,
    AgentEvaluation,
    AlignmentAgentState,
    BranchConfig,
    DeltaScore,
    ObservationContext,
    TraceEvent,
)
from .evaluator import ObjectiveEvaluator

logger = logging.getLogger(__name__)


@dataclass
class BranchExecutionResult:
    """Raw output from running one branch through the pipeline."""
    branch_id: str
    template_name_used: str
    csv_raw: str
    warnings: List[str]
    wall_time_sec: float
    error: Optional[str] = None

    # Parsed from csv_raw for evaluation
    aligned_numbering: List[str] = field(default_factory=list)
    aligned_template: List[str] = field(default_factory=list)
    aligned_sequence: List[str] = field(default_factory=list)


def _parse_csv_alignment(csv_raw: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse pipeline CSV output into (numbering_row, template_row, sequence_row).
    The pipeline outputs a 3-row CSV: numbering, template, target sequence.
    """
    numbering: List[str] = []
    template: List[str] = []
    sequence: List[str] = []
    try:
        reader = csv.reader(io.StringIO(csv_raw))
        rows = list(reader)
        if len(rows) >= 3:
            numbering = rows[0]
            template = rows[1]
            sequence = rows[2]  # third row = target aligned
    except Exception as e:
        logger.warning(f"Failed to parse CSV alignment output: {e}")
    return numbering, template, sequence


def execute_branch(
    state: AlignmentAgentState,
    action: AgentAction,
    config: BranchConfig,
) -> BranchExecutionResult:
    """
    Execute a single alignment branch by calling perform_full_alignment
    with branch-specific overrides.

    This is the only place where the Agent touches the real pipeline.
    The LRU cache is bypassed by using a unique (never-cached) call path.
    """
    result = BranchExecutionResult(
        branch_id=action.branch_id,
        template_name_used=config.template_name,
        csv_raw="",
        warnings=[],
        wall_time_sec=0.0,
    )

    t0 = time.time()
    try:
        # Import at call time to avoid circular imports and allow isolation
        from backend.domain.alignment.align import perform_full_alignment

        template_name_out, csv_raw, warnings = perform_full_alignment(
            target_seq=state.target_seq,
            output_csv_path="/dev/null",
            anticode=state.anticodon_hint,
            use_llm=config.use_llm,
            llm_reasoning=config.llm_reasoning,
            template_name=config.template_name or None,
            runtime_values_override=config.runtime_overrides,
        )
        result.template_name_used = template_name_out
        result.csv_raw = csv_raw
        result.warnings = list(warnings or [])

        # Parse the CSV into structured arrays for the evaluator
        numbering, template, sequence = _parse_csv_alignment(csv_raw)
        result.aligned_numbering = numbering
        result.aligned_template = template
        result.aligned_sequence = sequence

    except Exception as e:
        result.error = str(e)
        logger.error(f"Branch {action.branch_id} execution failed: {e}", exc_info=True)
    finally:
        result.wall_time_sec = round(time.time() - t0, 2)

    return result


__all__ = ["BranchExecutionResult", "execute_branch"]
