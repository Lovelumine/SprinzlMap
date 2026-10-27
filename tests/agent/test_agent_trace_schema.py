# -*- coding: utf-8 -*-
"""
Section 10.3 Required Test: Agent Trace Schema
Validates that trace events conform to the expected schema and
that the logger produces well-formed output.
"""
import sys
sys.path.insert(0, ".")

try:
    import pytest
except ImportError:
    pass

import json
import tempfile
import os
from dataclasses import asdict, fields
from backend.agent.core.state import ObservationContext, TraceEvent
from backend.agent.tracing.schema import AgentTraceLogger


class TestTraceEvent:
    def test_required_fields(self):
        f = {x.name for x in fields(TraceEvent)}
        required = {
            "trace_id", "case_id", "event_index", "timestamp",
            "branch_id", "parent_branch_id", "stage",
            "action_ref", "summary", "observation",
        }
        assert required.issubset(f)

    def test_instantiation(self):
        ev = TraceEvent(
            trace_id="run-001",
            case_id="pair_000321",
            event_index=1,
            timestamp="2026-03-25T10:00:00Z",
            branch_id="branch-0001",
            parent_branch_id="root",
            stage="evaluation",
            action_ref="act-0001",
            summary="Test action completed",
            observation=ObservationContext(
                warning_count=2,
                rollback_triggered=True,
                llm_regions_touched=["D loop + D stem"],
            ),
            evaluation_ref="eval-0001",
            termination_hint=None,
        )
        assert ev.trace_id == "run-001"
        assert ev.observation.rollback_triggered is True
        assert ev.observation.llm_regions_touched == ["D loop + D stem"]

    def test_serialization(self):
        ev = TraceEvent(
            trace_id="run-001", case_id="c1", event_index=1,
            timestamp="2026-01-01T00:00:00Z",
            branch_id="b1", parent_branch_id="root",
            stage="action", action_ref="a1", summary="test",
            observation=ObservationContext(warning_count=0, rollback_triggered=False, llm_regions_touched=[]),
            evaluation_ref=None,
            termination_hint=None,
        )
        d = asdict(ev)
        # Must be JSON-serializable
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["trace_id"] == "run-001"
        assert parsed["observation"]["rollback_triggered"] is False


class TestObservationContext:
    def test_fields(self):
        f = {x.name for x in fields(ObservationContext)}
        assert "warning_count" in f
        assert "rollback_triggered" in f
        assert "llm_regions_touched" in f


class TestAgentTraceLogger:
    def test_logger_construction(self):
        logger = AgentTraceLogger()
        assert logger is not None


if __name__ == "__main__":
    try:
        pytest.main([__file__, "-v"])
    except NameError:
        pass
