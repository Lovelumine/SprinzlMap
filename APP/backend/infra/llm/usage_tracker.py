# -*- coding: utf-8 -*-
"""Process-local LLM API usage accounting for benchmark runs."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


_LOCK = threading.Lock()
_CALLS_BY_CONTEXT: Dict[tuple[str, str], list[Dict[str, Any]]] = {}


def _as_text(raw: Any) -> str:
    return str(raw or "").strip()


def usage_context_from_cfg(cfg: Mapping[str, Any]) -> tuple[str, str]:
    run_id = _as_text(cfg.get("usage_run_id") or cfg.get("LLM_USAGE_RUN_ID"))
    case_id = _as_text(cfg.get("usage_case_id") or cfg.get("LLM_USAGE_CASE_ID"))
    return run_id, case_id


def reset_usage_context(run_id: str, case_id: str) -> None:
    key = (_as_text(run_id), _as_text(case_id))
    with _LOCK:
        _CALLS_BY_CONTEXT[key] = []


def _flatten_numeric(obj: Any, *, prefix: str = "") -> Dict[str, int | float]:
    out: Dict[str, int | float] = {}
    if not isinstance(obj, Mapping):
        return out
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out[name] = value
        elif isinstance(value, float):
            out[name] = value
        elif isinstance(value, Mapping):
            out.update(_flatten_numeric(value, prefix=name))
    return out


def extract_usage_from_response_text(response_text: str) -> Optional[Dict[str, Any]]:
    text = str(response_text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, Mapping):
        return None
    usage = data.get("usage")
    if isinstance(usage, Mapping):
        return dict(usage)
    ollama_keys = {"prompt_eval_count", "eval_count", "total_duration"}
    if any(key in data for key in ollama_keys):
        return {
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
            "total_duration": data.get("total_duration"),
        }
    return None


def record_llm_usage(
    *,
    cfg: Mapping[str, Any],
    source: str,
    url: str,
    payload: Optional[Mapping[str, Any]] = None,
    status_code: Optional[int] = None,
    elapsed_sec: Optional[float] = None,
    response_text: str = "",
    usage: Optional[Mapping[str, Any]] = None,
    error_type: str = "",
) -> None:
    run_id, case_id = usage_context_from_cfg(cfg)
    if not run_id or not case_id:
        return
    usage_obj = dict(usage or extract_usage_from_response_text(response_text) or {})
    usage_flat = _flatten_numeric(usage_obj)
    model = _as_text((payload or {}).get("model") or cfg.get("model_name"))
    call = {
        "source": _as_text(source),
        "provider": _as_text(cfg.get("provider")),
        "mode": _as_text(cfg.get("mode")),
        "model": model,
        "url": _as_text(url),
        "status_code": status_code,
        "elapsed_sec": round(float(elapsed_sec), 4) if elapsed_sec is not None else None,
        "usage": usage_obj,
        "usage_flat": usage_flat,
    }
    if error_type:
        call["error_type"] = _as_text(error_type)
    with _LOCK:
        _CALLS_BY_CONTEXT.setdefault((run_id, case_id), []).append(call)


def summarize_usage_calls(calls: list[Dict[str, Any]]) -> Dict[str, Any]:
    totals: Dict[str, int | float] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    calls_with_usage = 0
    for call in calls:
        usage_flat = dict(call.get("usage_flat") or {})
        if usage_flat:
            calls_with_usage += 1
        model = _as_text(call.get("model")) or "unknown"
        model_summary = by_model.setdefault(model, {"call_count": 0, "calls_with_usage": 0, "tokens": {}})
        model_summary["call_count"] = int(model_summary.get("call_count") or 0) + 1
        if usage_flat:
            model_summary["calls_with_usage"] = int(model_summary.get("calls_with_usage") or 0) + 1
        model_tokens = dict(model_summary.get("tokens") or {})
        for key, value in usage_flat.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            totals[key] = totals.get(key, 0) + value
            model_tokens[key] = model_tokens.get(key, 0) + value
        model_summary["tokens"] = model_tokens
    return {
        "call_count": len(calls),
        "calls_with_usage": calls_with_usage,
        "tokens": totals,
        "by_model": by_model,
    }


def get_usage_summary(run_id: str, case_id: str, *, include_calls: bool = False) -> Dict[str, Any]:
    key = (_as_text(run_id), _as_text(case_id))
    with _LOCK:
        calls = deepcopy(_CALLS_BY_CONTEXT.get(key, []))
    summary = summarize_usage_calls(calls)
    if include_calls:
        summary["calls"] = calls
    return summary
