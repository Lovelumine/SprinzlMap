# -*- coding: utf-8 -*-
"""
Shared LLM provider/runtime configuration helpers.

This module exists to keep provider-specific parsing out of domain logic.
Alignment components should depend on this thin infrastructure layer instead of
reimplementing:

- provider normalization
- base URL/model/API key lookup
- OpenAI-compatible chat completions URL construction
- provider-specific temperature quirks
- provider-specific OpenAI-compatible request extras
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from backend.domain.alignment.runtime_helpers import (
    load_runtime_values,
    runtime_bool,
    runtime_float,
    runtime_int,
    runtime_raw,
    runtime_str,
)


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:32b"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.5-highspeed"
DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_TOKEN_PLAN_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MIMO_MODEL = "mimo-v2.6-pro"

# Hard ceiling for completion tokens. 256K = 256 * 1024; provider gateways may
# impose a lower per-request output cap, which would surface as an HTTP 4xx.
MAX_COMPLETION_TOKENS = 256 * 1024

SUPPORTED_PROVIDERS = {"ollama", "deepseek", "minimax", "mimo"}


def _credential_pool(*values: Any) -> List[str]:
    pool: List[str] = []
    seen = set()
    for raw in values:
        for value in re.split(r"[,\n\r]+", str(raw or "")):
            key = value.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            pool.append(key)
    return pool


def normalize_provider(raw: Any, default: str = "ollama") -> str:
    provider = str(raw or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        return default
    return provider


def openai_chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def openai_temperature(provider_or_cfg: Any, default: float = 0.0) -> float:
    provider = provider_or_cfg
    if isinstance(provider_or_cfg, dict):
        provider = provider_or_cfg.get("provider")
    if str(provider or "").lower() == "minimax":
        return max(0.01, float(default))
    return float(default)


def normalize_deepseek_thinking(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"enabled", "enable", "on", "true", "1", "yes", "y"}:
        return "enabled"
    if text in {"disabled", "disable", "off", "false", "0", "no", "n"}:
        return "disabled"
    return ""


def _runtime_extra_body(runtime_values: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse LLM_EXTRA_BODY into request-body fields, ignoring malformed input."""
    raw = runtime_str("LLM_EXTRA_BODY", "", runtime_values=runtime_values)
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def apply_openai_provider_extras(
    payload: Dict[str, Any],
    provider_or_cfg: Any,
    *,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    provider = provider_or_cfg
    cfg: Dict[str, Any] = {}
    if isinstance(provider_or_cfg, dict):
        cfg = provider_or_cfg
        provider = cfg.get("provider")

    normalized_provider = str(provider or "").lower()
    if normalized_provider == "mimo":
        payload["temperature"] = 1.0
        payload["top_p"] = 0.95
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        return payload

    if normalized_provider != "deepseek":
        return payload

    model = str(model_name or payload.get("model") or cfg.get("model_name") or "").lower()
    # Gateways may spell the same family "deepseekv4-flash-guangdong".
    if "deepseekv4" not in model.replace("-", "").replace("_", ""):
        return payload

    # Thinking is enabled by default for DeepSeek V4-family models. Callers may
    # still opt out explicitly with DEEPSEEK_THINKING=disabled.
    thinking = normalize_deepseek_thinking(
        cfg.get("deepseek_thinking")
        if isinstance(cfg, dict) and "deepseek_thinking" in cfg
        else runtime_str("DEEPSEEK_THINKING", "enabled")
    )
    if thinking:
        payload["thinking"] = {"type": thinking}
    return payload


def load_provider_runtime_config(
    runtime_values_override: Optional[Dict[str, Any]] = None,
    *,
    default_provider: str = "ollama",
    include_postcheck: bool = True,
    include_max_tokens: bool = True,
) -> Dict[str, Any]:
    runtime_values = load_runtime_values(runtime_values_override)

    provider = normalize_provider(
        runtime_str("LLM_PROVIDER", default_provider, runtime_values=runtime_values),
        default=default_provider,
    )

    if provider == "deepseek":
        mode = "openai"
        base_url = runtime_str(
            "DEEPSEEK_BASE_URL",
            DEFAULT_DEEPSEEK_BASE_URL,
            runtime_values=runtime_values,
        ).rstrip("/")
        model_name = runtime_str(
            "DEEPSEEK_MODEL",
            DEFAULT_DEEPSEEK_MODEL,
            runtime_values=runtime_values,
        )
        api_key = runtime_raw("DEEPSEEK_API_KEY", runtime_values) or ""
    elif provider == "minimax":
        mode = "openai"
        base_url = runtime_str(
            "MINIMAX_BASE_URL",
            DEFAULT_MINIMAX_BASE_URL,
            runtime_values=runtime_values,
        ).rstrip("/")
        model_name = runtime_str(
            "MINIMAX_MODEL",
            DEFAULT_MINIMAX_MODEL,
            runtime_values=runtime_values,
        )
        api_key = runtime_raw("MINIMAX_API_KEY", runtime_values) or ""
    elif provider == "mimo":
        mode = "openai"
        base_url = runtime_str(
            "MIMO_BASE_URL",
            DEFAULT_MIMO_BASE_URL,
            runtime_values=runtime_values,
        ).rstrip("/")
        model_name = runtime_str(
            "MIMO_MODEL",
            DEFAULT_MIMO_MODEL,
            runtime_values=runtime_values,
        )
        primary_api_keys = _credential_pool(
            runtime_raw("MIMO_API_KEYS", runtime_values),
            runtime_raw("MIMO_API_KEY", runtime_values),
        )
        credentials = [
            {"base_url": base_url, "api_key": key, "credential_type": "standard"}
            for key in primary_api_keys
        ]
        token_plan_base_url = runtime_str(
            "MIMO_TOKEN_PLAN_BASE_URL",
            DEFAULT_MIMO_TOKEN_PLAN_BASE_URL,
            runtime_values=runtime_values,
        ).rstrip("/")
        token_plan_keys = _credential_pool(
            runtime_raw("MIMO_TOKEN_PLAN_API_KEY", runtime_values),
        )
        credentials.extend(
            {
                "base_url": token_plan_base_url,
                "api_key": key,
                "credential_type": "token_plan",
            }
            for key in token_plan_keys
            if not any(
                item["api_key"] == key and item["base_url"] == token_plan_base_url
                for item in credentials
            )
        )
        api_keys = [str(item["api_key"]) for item in credentials]
        api_key = api_keys[0] if api_keys else ""
    else:
        mode = "ollama"
        base_url = runtime_str(
            "OLLAMA_BASE_URL",
            DEFAULT_OLLAMA_BASE_URL,
            runtime_values=runtime_values,
        ).rstrip("/")
        model_name = runtime_str(
            "OLLAMA_MODEL",
            DEFAULT_OLLAMA_MODEL,
            runtime_values=runtime_values,
        )
        api_key = runtime_raw("OLLAMA_API_KEY", runtime_values) or ""

    cfg: Dict[str, Any] = {
        "provider": provider,
        "mode": mode,
        "base_url": base_url,
        "model_name": model_name,
        "api_key": str(api_key),
        "verify_ssl": runtime_bool("LLM_VERIFY_SSL", True, runtime_values=runtime_values),
        "timeout_connect": runtime_float(
            "LLM_CONNECT_TIMEOUT", 8.0, 0.1, 1200.0, runtime_values=runtime_values
        ),
        "timeout_read": runtime_float(
            "LLM_READ_TIMEOUT", 20.0, 0.1, 1200.0, runtime_values=runtime_values
        ),
        "hard_budget_s": runtime_float(
            "LLM_HARD_BUDGET", 30.0, 1.0, 3600.0, runtime_values=runtime_values
        ),
        "streaming": runtime_bool("LLM_STREAM", True, runtime_values=runtime_values),
        "max_retries": runtime_int("LLM_RETRIES", 4, 0, 20, runtime_values=runtime_values),
        "backoff_base_s": runtime_float(
            "LLM_BACKOFF_BASE", 0.5, 0.0, 30.0, runtime_values=runtime_values
        ),
        "min_request_interval_s": runtime_float(
            "LLM_MIN_REQUEST_INTERVAL",
            0.0,
            0.0,
            60.0,
            runtime_values=runtime_values,
        ),
        "deepseek_thinking": runtime_str(
            "DEEPSEEK_THINKING",
            "enabled",
            runtime_values=runtime_values,
        ),
        # Optional body fields for gateways that need provider-specific switches
        # the OpenAI schema does not cover, such as
        # {"chat_template_kwargs": {"enable_thinking": false}}. Empty by default
        # so ordinary OpenAI-compatible endpoints never receive unknown keys.
        "extra_body": _runtime_extra_body(runtime_values),
    }
    if provider == "mimo":
        cfg["api_keys"] = list(api_keys)
        cfg["credentials"] = list(credentials)

    if include_postcheck:
        cfg["postcheck_on"] = runtime_bool("LLM_POSTCHECK", True, runtime_values=runtime_values)

    if include_max_tokens:
        cfg["max_tokens"] = runtime_int(
            "LLM_MAX_TOKENS",
            MAX_COMPLETION_TOKENS,
            128,
            MAX_COMPLETION_TOKENS,
            runtime_values=runtime_values,
        )

    return cfg
