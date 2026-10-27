from __future__ import annotations

import json

import pytest

from backend.domain.gold_curation import service


def _trace() -> dict:
    return {
        "seqname": "example",
        "status": "ok",
        "model": "model-x",
        "provider": "deepseek",
        "method_backend": "agent",
        "agent_planner_policy": "llm",
        "template_name": "bact-num",
        "agent": {
            "route": "canonical",
            "disposition": "investigate",
            "difficulty_tier": "moderate",
            "hard_guard_passed": False,
            "route_confidence": 0.0,
        },
        "agent_trace": {
            "trace_events": [
                {"stage": "input_classified", "input_type": "mature", "input_contract": {"prompt": "p" * 4000}},
                {"stage": "tool_observation", "tool": "align_numbering_cm", "bit_score": 67.15, "average_posterior": 0.999},
                {"stage": "difficulty_assessed", "difficulty_score": 0.68, "tier": "moderate"},
                {"stage": "route_assessed", "confidence": 0.0, "route": "canonical"},
                {"stage": "action_failed", "action_id": "specialist:1:x", "error": "Annotation failed", "work_state": {"rows": ["r" * 4000]}},
                {"stage": "candidate_disposition", "disposition": "rejected", "reasons": ["cm_domain_calibration_abstained"]},
            ],
            "work_state_summary": {
                "action_history": [
                    {
                        "action_id": "specialist:1:x",
                        "accepted": False,
                        "score_after": float("-inf"),
                        "notes": ["tool_error:ValueError:" + "z" * 600],
                    }
                ]
            },
        },
        "guard_challenge_audit": {
            "schema_version": "guard_challenge_v1",
            "baseline_valid": False,
            "baseline_reasons": ["cm_domain_calibration_abstained"],
            "challenges": [],
            "skipped_reason": "baseline_candidate_not_accepted_by_hard_guards",
        },
        "llm_usage": {
            "calls": [
                {
                    "source": "agent_planner",
                    "model": "model-x",
                    "provider": "deepseek",
                    "status_code": 200,
                    "elapsed_sec": 28.58,
                    "usage_flat": {"prompt_tokens": 1681, "completion_tokens": 318, "total_tokens": 1999},
                }
            ]
        },
        "scientific_provenance": {
            "numbering_backend": "cm",
            "cm_portfolio": {
                "calibration": {
                    "status": "calibrated",
                    "disposition": "investigate",
                    "predicted_group": "bact",
                    "confidence": 0.417,
                    "probability_margin": 0.093,
                    "training_protocol": {"notes": "t" * 4000},
                }
            },
        },
        "legality_reasons": ["cm_domain_calibration_abstained"],
        "warnings": ["cm_portfolio_low_raw_score_margin"],
    }


_ROW = {
    "run_id": "r",
    "seqname": "example",
    "status": "ok",
    "error": "",
    "route": "canonical",
    "disposition": "investigate",
    "legality_passed": False,
    "base_preserved": True,
    "out_of_scope": False,
    "in_canonical_benchmark": True,
    "planner_decision_count": 3,
    "planner_llm_call_count": 2,
    "agent_action_count": 1,
    "cm_tool_call_count": 2,
    "llm_api_call_count": 2,
    "technical_issue_count": 0,
    "guard_challenge_count": None,
    "guard_challenge_rejected_count": None,
    "llm_prompt_tokens": 3370,
    "llm_completion_tokens": 373,
    "llm_total_tokens": 3743,
    "llm_prompt_cache_hit_tokens": 0,
    "llm_prompt_cache_miss_tokens": 0,
    "elapsed_sec": 105.2163,
    "agent_stop_reason": "rejected_by_shared_hard_guards",
    "template_name": "bact-num",
    "method_backend": "agent",
    "agent_planner_policy": "llm",
    "planner_sources": "harness_fast_path,llm",
    "started_at": "2026-10-20 16:45:00",
    "finished_at": "2026-10-20 16:46:45",
}


class _Store:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def case_row(self, seqname: str, *, run_id=None) -> dict | None:
        return self._row


_MISSING = object()


def _mount(monkeypatch, *, row: dict | None = _ROW, trace: object = _MISSING) -> None:
    stored = _trace() if trace is _MISSING else trace
    monkeypatch.setattr(service, "_store_for", lambda _run_id: _Store(row))
    monkeypatch.setattr(service, "_load_case_json", lambda _run_id, _seqname: stored)


def test_process_view_recounts_the_stored_derivation(monkeypatch):
    _mount(monkeypatch)
    payload = service.load_case_process("example", run_id="r")
    assert payload["available"] is True
    assert [step["stage"] for step in payload["steps"]] == [
        "input_classified",
        "tool_observation",
        "difficulty_assessed",
        "route_assessed",
        "action_failed",
        "candidate_disposition",
    ]
    assert payload["counts"]["steps"] == 6
    assert payload["counts"]["agent_actions"] == 1
    assert payload["tokens"]["total"] == 3743
    assert payload["stop_reason"] == "rejected_by_shared_hard_guards"
    assert payload["planner_sources"] == ["harness_fast_path", "llm"]
    assert payload["steps"][4]["state"] == "failed"
    assert payload["steps"][5]["state"] == "warning"
    assert payload["llm_calls"][0]["total_tokens"] == 1999


def test_process_view_keeps_prompt_blobs_out_of_the_page(monkeypatch):
    _mount(monkeypatch)
    encoded = json.dumps(service.load_case_process("example", run_id="r"))
    assert "input_contract" not in encoded
    assert "work_state" not in encoded
    assert "training_protocol" not in encoded
    # -inf marks an unscored action, and JSON has no literal for it.
    assert "Infinity" not in encoded
    assert "NaN" not in encoded


def test_process_view_reports_an_indexed_case_without_a_trace(monkeypatch):
    _mount(monkeypatch, trace={})
    payload = service.load_case_process("example", run_id="r")
    assert payload["available"] is False
    assert payload["steps"] == []
    assert payload["disposition"] == "investigate"
    assert payload["legality_passed"] is False


def test_process_view_rejects_an_unknown_case(monkeypatch):
    _mount(monkeypatch, row=None, trace=None)
    with pytest.raises(KeyError):
        service.load_case_process("missing|XX0000001", run_id="r")
