# -*- coding: utf-8 -*-
"""Lightweight readers for current runtime settings."""

from __future__ import annotations

import os
from typing import Any

from .runtime_settings import RUNTIME_SETTINGS_SCHEMA, coerce_runtime_updates


def get_runtime_value(key: str) -> Any:
    if key not in RUNTIME_SETTINGS_SCHEMA:
        raise KeyError(f"Unsupported runtime setting: {key}")
    default = RUNTIME_SETTINGS_SCHEMA[key]["default"]
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return coerce_runtime_updates({key: raw})[key]
    except ValueError:
        return default


def get_runtime_int(key: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = int(get_runtime_value(key))
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def get_runtime_str(key: str, *, fallback: str | None = None) -> str:
    value = str(get_runtime_value(key) or "").strip()
    if value:
        return value
    if fallback is not None:
        return fallback
    return str(RUNTIME_SETTINGS_SCHEMA[key]["default"])
