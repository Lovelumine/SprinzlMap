"""Publish bounded, factual execution events without prompts or private reasoning."""
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from ..contracts import AlignmentRequest


def emit_progress(request: AlignmentRequest, event: Dict[str, Any]) -> None:
    if request.status_callback is None:
        return
    stage = str(event.get("stage") or "working")
    decision = event.get("agent_decision") or event
    source = str(decision.get("planner_source") or "")
    public = {"stage": stage, "at": datetime.now(timezone.utc).isoformat(),
              "source": "agent" if source == "llm" else "system" if stage.startswith("llm_") else "tool" if stage.startswith("tool") or stage == "action_executed" else "rule"}
    for key in ("tool", "tool_name", "action_type", "action_id", "phase", "disposition",
                "valid", "candidate_valid", "accepted_as_current", "bit_score", "average_posterior",
                "candidate_score", "selected_group", "error_type", "attempt", "retry_in_seconds"):
        if key in event and event[key] is not None:
            public[key] = event[key]
    for key in ("decision", "action_id", "reason_code", "planner_source", "llm_called"):
        if key in decision:
            public[key] = decision[key]
    if decision.get("fallback_reason"):
        public["fallback"] = True
    reasons = event.get("candidate_reasons") or event.get("reasons") or []
    public["reasons"] = [str(x.get("code", "")) if isinstance(x, dict) else str(x) for x in reasons][:8]
    try:
        request.status_callback({"stage": stage, "process_event": public})
    except Exception:
        # Browser disconnects or rendering must never alter scientific execution.
        pass


def record_trace(request: AlignmentRequest, trace: List[Dict[str, Any]], event: Dict[str, Any]) -> None:
    trace.append(event)
    emit_progress(request, event)


def extend_trace(request: AlignmentRequest, trace: List[Dict[str, Any]], events: Iterable[Dict[str, Any]]) -> None:
    for event in events:
        record_trace(request, trace, event)
