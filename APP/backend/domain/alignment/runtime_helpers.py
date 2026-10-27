# -*- coding: utf-8 -*-
"""Shared runtime access helpers for alignment and postcheck modules."""

from __future__ import annotations

from typing import Any, Dict, Optional
import os

try:
    from backend.infra.config.runtime_settings import get_effective_runtime_settings
except Exception:  # pragma: no cover
    get_effective_runtime_settings = None  # type: ignore[assignment]


def load_runtime_values(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    if get_effective_runtime_settings is not None:
        try:
            resolved = get_effective_runtime_settings()
            if isinstance(resolved, dict):
                values = dict(resolved)
        except Exception:
            values = {}
    return merge_runtime_values(values, override)


def merge_runtime_values(base: Optional[Dict[str, Any]], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not override:
        return dict(base or {})
    out = dict(base or {})
    for key, value in override.items():
        if key is None:
            continue
        out[str(key)] = value
    return out


def runtime_raw(name: str, runtime_values: Optional[Dict[str, Any]] = None) -> Any:
    if runtime_values is not None and name in runtime_values:
        return runtime_values[name]
    return os.getenv(name)


def runtime_str(
    name: str,
    default: str = "",
    *,
    runtime_values: Optional[Dict[str, Any]] = None,
) -> str:
    raw = runtime_raw(name, runtime_values)
    if raw in (None, ""):
        return default
    return str(raw).strip()


def runtime_prompt_value(key: str, runtime_values: Optional[Dict[str, Any]] = None) -> str:
    return runtime_str(key, "", runtime_values=runtime_values)


def runtime_bool(
    name: str,
    default: bool,
    *,
    runtime_values: Optional[Dict[str, Any]] = None,
) -> bool:
    raw = runtime_raw(name, runtime_values)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def runtime_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    *,
    runtime_values: Optional[Dict[str, Any]] = None,
) -> int:
    raw = runtime_raw(name, runtime_values)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def runtime_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
    *,
    runtime_values: Optional[Dict[str, Any]] = None,
) -> float:
    raw = runtime_raw(name, runtime_values)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def deepseek_fallback_model(runtime_values: Optional[Dict[str, Any]] = None) -> str:
    return runtime_str("DEEPSEEK_FALLBACK_MODEL", "deepseek-chat", runtime_values=runtime_values)
