from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from backend.domain.alignment import llm_postcheck
from backend.domain.alignment.engine import STANDARD_ALIGNMENT_REGIONS, boundary_refinement


RUNNER_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmark"
    / "scripts"
    / "run_numbering_benchmark.py"
)
SPEC = importlib.util.spec_from_file_location("component_ablation_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def _block(region: str = "D loop + D stem") -> dict:
    return {
        "region": region,
        "lines": [
            ["10", "11", "12"],
            ["A", "U", "G"],
            ["A", "-", "G"],
        ],
        "meta": {},
    }


def test_component_flags_enter_benchmark_runtime_contract() -> None:
    args = SimpleNamespace(
        provider="mimo",
        model="mimo-v2.5-pro",
        llm_global_max_workers=4,
        llm_max_inflight=2,
        agent_canonical_root_strategy="agent_toolbox",
        agent_disable_legacy_fallback=False,
        agent_enable_llm_postcheck=False,
        agent_disable_deterministic_boundary_refinement=True,
        agent_enable_planning=False,
        agent_enable_cm_tool=False,
        agent_enable_cm_root_recovery=False,
        agent_soft_risk_review_local_coordinate_ambiguity=True,
        agent_soft_risk_review_acceptor_tail=True,
        agent_local_ambiguity_reference_top_k=3,
        agent_local_ambiguity_min_reference_support=9,
        agent_soft_risk_min_local_resolution_score_gain=0.25,
    )

    overrides = RUNNER.build_runtime_overrides(args)

    assert overrides["AGENT_ENABLE_LLM_POSTCHECK"] is False
    assert overrides["AGENT_ENABLE_DETERMINISTIC_BOUNDARY_REFINEMENT"] is False
    assert overrides["AGENT_CANONICAL_ROOT_STRATEGY"] == "agent_toolbox"
    assert overrides["AGENT_LLM_TRIAGE_MODE"] == "uncertain_only"
    assert overrides["AGENT_SOFT_RISK_MIN_CM_POSTERIOR"] == 0.995
    assert overrides["AGENT_SOFT_RISK_REVIEW_INSERTIONS"] is True
    assert overrides["AGENT_SOFT_RISK_REVIEW_BROAD_CM"] is True
    assert overrides["AGENT_SOFT_RISK_REVIEW_LOCAL_COORDINATE_AMBIGUITY"] is True
    assert overrides["AGENT_SOFT_RISK_REVIEW_ACCEPTOR_TAIL"] is True
    assert overrides["AGENT_LOCAL_AMBIGUITY_REFERENCE_TOP_K"] == 3
    assert overrides["AGENT_LOCAL_AMBIGUITY_MIN_REFERENCE_SUPPORT"] == 3
    assert overrides["AGENT_SOFT_RISK_MIN_LOCAL_RESOLUTION_SCORE_GAIN"] == 0.25
    assert overrides["AGENT_SOFT_RISK_MIN_CROSS_BACKEND_SCORE_GAIN"] == 51.0
    assert overrides["AGENT_MEMORY_MODE"] == "off"


def test_endpoint_provenance_records_endpoint_without_credentials() -> None:
    provenance = RUNNER.build_llm_endpoint_provenance(
        {
            "LLM_PROVIDER": "mimo",
            "MIMO_MODEL": "mimo-v2.5-pro",
            "MIMO_BASE_URL": "https://name:secret@example.test/v1?token=hidden",
            "MIMO_API_KEY": "not-for-manifest",
            "MIMO_API_KEYS": "",
            "MIMO_TOKEN_PLAN_API_KEY": "",
            "LLM_RETRIES": 4,
            "LLM_BACKOFF_BASE": 0.5,
            "LLM_MIN_REQUEST_INTERVAL": 0.0,
            "LLM_HARD_BUDGET": 30.0,
            "LLM_CONNECT_TIMEOUT": 8.0,
            "LLM_READ_TIMEOUT": 20.0,
            "LLM_MAX_TOKENS": 256 * 1024,
            "AGENT_PLANNER_MAX_TOKENS": 256 * 1024,
        }
    )

    assert provenance == {
        "provider": "mimo",
        "mode": "openai",
        "model": "mimo-v2.5-pro",
        "base_url": "https://example.test/v1",
        "completion_url": "https://example.test/v1/chat/completions",
        "credential_endpoints": ["https://example.test/v1"],
        "api_key_configured": True,
        "credential_pool_size": 1,
        "verify_ssl": True,
        "request_parameters": {
            "max_completion_tokens": 256 * 1024,
            "stream": True,
        },
        "request_policy": {
            "credential_rotation_http_statuses": [401, 402, 403, 429],
            "retryable_http_statuses": [
                429,
                500,
                502,
                503,
                504,
                520,
                521,
                522,
                523,
                524,
            ],
            "max_retries": 4,
            "backoff_base_s": 0.5,
            "min_request_interval_s": 0.0,
            "hard_budget_s": 30.0,
            "connect_timeout_s": 8.0,
            "read_timeout_s": 20.0,
        },
    }
    assert "secret" not in str(provenance)
    assert "hidden" not in str(provenance)
    assert "not-for-manifest" not in str(provenance)


def test_postcheck_component_flag_bypasses_handlers(monkeypatch) -> None:
    called = False

    def _handler(block: dict) -> dict:
        nonlocal called
        called = True
        return block

    monkeypatch.setitem(
        llm_postcheck._REGION_HANDLERS,
        "D loop + D stem",
        _handler,
    )

    out = llm_postcheck.postcheck_alignment(
        [_block()],
        use_llm=True,
        runtime_values_override={"AGENT_ENABLE_LLM_POSTCHECK": False},
    )

    trace = out[0]["meta"]["llm_runtime_trace"]
    assert called is False
    assert trace["raw_status"] == "skipped_agent_llm_postcheck_disabled"
    assert trace["final_status"] == "pass_through_disabled"
    assert trace["llm_attempted"] is False


def test_boundary_component_uses_deterministic_multi_evidence_search() -> None:
    current_lengths = [7, 18, 15, 6, 17, 7]
    template_lengths = [7, 17, 17, 5, 17, 7]
    raw_seq = "A" * sum(current_lengths)
    regions = {}
    left = 0
    for region, current_len, template_len in zip(
        STANDARD_ALIGNMENT_REGIONS,
        current_lengths,
        template_lengths,
    ):
        regions[region] = {
            "input_seq": raw_seq[left : left + current_len],
            "template_seq": "A" * template_len,
            "input_struct": "." * current_len,
        }
        left += current_len
    cmp = {
        "raw_input_seq": raw_seq,
        "template_name": "synthetic",
        "regions": regions,
        "annotation_meta": {"secondary_structure": "." * len(raw_seq)},
    }

    boundary_refinement.maybe_refine_anticodon_boundaries(
        cmp,
        list(STANDARD_ALIGNMENT_REGIONS),
    )

    meta = cmp["annotation_meta"]
    assert meta["boundary_resolved"] is True
    assert meta["boundary_after_lengths"] == template_lengths
    assert meta["boundary_resolver_mode"] == "deterministic_multi_evidence_search"
    assert meta["boundary_llm_enabled"] is False
    assert meta["boundary_candidate_count"] == 25
    assert meta["boundary_evidence_before"]["score_key"] > meta["boundary_evidence_after"]["score_key"]
