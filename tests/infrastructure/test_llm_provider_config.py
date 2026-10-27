from __future__ import annotations

import pytest

from backend.infra.llm.provider_config import (
    DEFAULT_MIMO_BASE_URL,
    DEFAULT_MIMO_TOKEN_PLAN_BASE_URL,
    DEFAULT_MIMO_MODEL,
    MAX_COMPLETION_TOKENS,
    apply_openai_provider_extras,
    load_provider_runtime_config,
    openai_chat_completions_url,
)
from backend.domain.alignment.postcheck.common import (
    build_chat_request,
    provider_fallback_model,
)


@pytest.mark.parametrize("model", [
    "deepseek-v4-flash", "deepseekv4-flash-guangdong", "DeepSeek_V4_Flash",
])
def test_deepseek_v4_aliases_send_explicit_thinking_policy(model):
    from backend.infra.llm.json_client import StructuredJsonLLMClient

    _, payload = StructuredJsonLLMClient._request_payload(
        cfg={"mode": "openai", "provider": "deepseek",
             "base_url": "https://example.invalid/v1", "max_tokens": 4096,
             "deepseek_thinking": "disabled", "streaming": True},
        messages=[{"role": "user", "content": "Return a JSON decision"}],
        model_name=model, max_tokens=512,
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 512
    assert payload["model"] == model


def test_deepseek_v3_keeps_its_existing_request_parameters():
    payload = {"model": "deepseek-chat"}
    apply_openai_provider_extras(payload, {"provider": "deepseek", "deepseek_thinking": "disabled"})
    assert "thinking" not in payload


def test_deepseek_v4_defaults_enable_thinking_and_allow_256k_output():
    from backend.infra.llm.json_client import StructuredJsonLLMClient

    cfg = load_provider_runtime_config(
        {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_MODEL": "deepseekv4-flash-guangdong",
            "DEEPSEEK_BASE_URL": "https://example.invalid/v1",
            "DEEPSEEK_API_KEY": "test-key",
        },
        include_max_tokens=True,
    )
    assert MAX_COMPLETION_TOKENS == 256 * 1024
    assert cfg["max_tokens"] == MAX_COMPLETION_TOKENS
    assert cfg["deepseek_thinking"] == "enabled"

    _, payload = StructuredJsonLLMClient._request_payload(
        cfg=cfg,
        messages=[{"role": "user", "content": "Return a JSON decision"}],
        model_name="deepseekv4-flash-guangdong",
        max_tokens=MAX_COMPLETION_TOKENS,
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["max_tokens"] == MAX_COMPLETION_TOKENS


def test_mimo_defaults_use_official_pay_as_you_go_api():
    cfg = load_provider_runtime_config(
        {
            "LLM_PROVIDER": "mimo",
            "MIMO_API_KEY": "test-key",
            "MIMO_BASE_URL": DEFAULT_MIMO_BASE_URL,
            "MIMO_MODEL": DEFAULT_MIMO_MODEL,
        },
        include_postcheck=False,
    )

    assert DEFAULT_MIMO_BASE_URL == "https://api.xiaomimimo.com/v1"
    assert DEFAULT_MIMO_MODEL == "mimo-v2.6-pro"
    assert cfg["base_url"] == DEFAULT_MIMO_BASE_URL
    assert cfg["model_name"] == DEFAULT_MIMO_MODEL
    assert openai_chat_completions_url(cfg["base_url"]) == (
        "https://api.xiaomimimo.com/v1/chat/completions"
    )


def test_mimo_payload_uses_official_completion_parameters():
    payload = {
        "model": "mimo-v2.5-pro",
        "temperature": 0.0,
        "max_tokens": 512,
    }

    result = apply_openai_provider_extras(payload, {"provider": "mimo"})

    assert result["temperature"] == 1.0
    assert result["top_p"] == 0.95
    assert result["max_completion_tokens"] == 512
    assert "max_tokens" not in result


def test_mimo_credentials_are_deduplicated_in_declared_order():
    cfg = load_provider_runtime_config(
        {
            "LLM_PROVIDER": "mimo",
            "MIMO_BASE_URL": DEFAULT_MIMO_BASE_URL,
            "MIMO_API_KEYS": "pool-a,pool-b\npool-a",
            "MIMO_API_KEY": "legacy-c",
            "MIMO_TOKEN_PLAN_API_KEY": "plan-d",
        },
        include_postcheck=False,
    )

    assert cfg["api_key"] == "pool-a"
    assert cfg["api_keys"] == ["pool-a", "pool-b", "legacy-c", "plan-d"]
    assert [item["credential_type"] for item in cfg["credentials"]] == [
        "standard",
        "standard",
        "standard",
        "token_plan",
    ]
    assert all(
        item["base_url"] == DEFAULT_MIMO_BASE_URL
        for item in cfg["credentials"][:-1]
    )
    assert cfg["credentials"][-1]["base_url"] == DEFAULT_MIMO_TOKEN_PLAN_BASE_URL


def test_postcheck_request_normalizes_versioned_and_unversioned_base_paths():
    for base_url in (
        "https://api.xiaomimimo.com",
        "https://api.xiaomimimo.com/v1",
    ):
        url, _ = build_chat_request(
            cfg={
                "mode": "openai",
                "provider": "mimo",
                "base_url": base_url,
                "max_tokens": 8192,
                "streaming": False,
            },
            messages=[{"role": "user", "content": "test"}],
            model_name="mimo-v2.5-pro",
        )

        assert url == "https://api.xiaomimimo.com/v1/chat/completions"


def test_postcheck_fallback_model_cannot_cross_provider_boundary():
    assert provider_fallback_model(
        {"provider": "mimo", "model_name": "mimo-v2.5-pro"},
        "deepseek-chat",
    ) == "mimo-v2.5-pro"
    assert provider_fallback_model(
        {"provider": "deepseek", "model_name": "deepseek-reasoner"},
        "deepseek-chat",
    ) == "deepseek-chat"
