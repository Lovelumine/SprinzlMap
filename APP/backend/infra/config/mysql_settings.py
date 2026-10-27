# -*- coding: utf-8 -*-
"""Shared MySQL environment loading and config parsing."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pymysql
from dotenv import load_dotenv


load_dotenv()


def env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_mysql_config(*, include_timeouts: bool = False) -> Optional[Dict[str, Any]]:
    return _load_from_prefix("MYSQL", include_timeouts=include_timeouts)


def load_shared_mysql_config(*, include_timeouts: bool = False) -> Optional[Dict[str, Any]]:
    """Connection for the sup-tRNA Database schema owned by another deployment.

    Our own gold, alignment and benchmark tables live in ``MYSQL_DATABASE``.
    The ``mysql_api`` routes read the shared ``trna_records`` and
    ``Engineered_sup_tRNA`` tables, which stay in their original schema, so
    they get a separate config instead of a second definition of "the database".
    """
    if not any((os.getenv(f"SHARED_MYSQL_{suffix}") or "").strip() for suffix in ("USER", "DATABASE")):
        return load_mysql_config(include_timeouts=include_timeouts)
    return _load_from_prefix("SHARED_MYSQL", include_timeouts=include_timeouts)


def _load_from_prefix(prefix: str, *, include_timeouts: bool) -> Optional[Dict[str, Any]]:
    required_keys = (f"{prefix}_HOST", f"{prefix}_USER", f"{prefix}_PASSWORD", f"{prefix}_DATABASE")
    values = {key: (os.getenv(key) or "").strip() for key in required_keys}
    if any(not values[key] for key in required_keys):
        return None

    config: Dict[str, Any] = dict(
        host=values[f"{prefix}_HOST"],
        port=env_int(f"{prefix}_PORT", 3306),
        user=values[f"{prefix}_USER"],
        password=values[f"{prefix}_PASSWORD"],
        database=values[f"{prefix}_DATABASE"],
        cursorclass=pymysql.cursors.DictCursor,
        charset=os.getenv(f"{prefix}_CHARSET", "utf8mb4"),
    )

    if include_timeouts:
        config.update(
            connect_timeout=max(1, min(env_int(f"{prefix}_CONNECT_TIMEOUT", 3), 30)),
            read_timeout=max(1, min(env_int(f"{prefix}_READ_TIMEOUT", 20), 120)),
            write_timeout=max(1, min(env_int(f"{prefix}_WRITE_TIMEOUT", 20), 120)),
        )
    return config

