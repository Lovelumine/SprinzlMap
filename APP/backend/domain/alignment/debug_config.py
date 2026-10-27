#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Centralized debug flag parsing for alignment-related modules."""

from __future__ import annotations

import os


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


# Keep debug output opt-in by default. Researchers can still turn any channel
# back on explicitly through TRNAALIGN_* environment variables when tracing.
DEBUG_ALIGN = env_flag("TRNAALIGN_DEBUG_ALIGN", False)
DEBUG_ALIGN_FULL = env_flag("TRNAALIGN_DEBUG_ALIGN_FULL", False)
DEBUG_ALIGN_MAXLEN = max(40, env_int("TRNAALIGN_DEBUG_MAXLEN", 200))

DEBUG_MATCH = env_flag("TRNAALIGN_DEBUG_MATCH", False)

DEBUG_ANNOTATE = env_flag("TRNAALIGN_DEBUG_ANNOTATE", False)
DEBUG_ANNOTATE_FULL = env_flag("TRNAALIGN_DEBUG_ANNOTATE_FULL", False)
DEBUG_ANNOTATE_SUMMARY = env_flag("TRNAALIGN_DEBUG_ANNOTATE_SUMMARY", False)
