# -*- coding: utf-8 -*-
"""
Alignment session storage (MySQL, 7-day TTL).
Stores request payload + alignment results for later retrieval by request_id.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import pymysql

from backend.infra.config.mysql_settings import load_mysql_config

MYSQL_CONFIG: Optional[Dict[str, Any]] = load_mysql_config(include_timeouts=True)

_TRACE_REGION_KEYS = (
    "region",
    "name",
    "status",
    "raw_status",
    "final_status",
    "message",
    "attempts",
    "llm_attempted",
    "fallback_used",
    "rejection_reasons",
    "output_changed",
    "accepted_candidate",
    "system_prompt",
    "user_prompt",
)


def _truncate_text(value: Any, limit: int = 4000) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _compact_trace_region(region: Any) -> Any:
    if not isinstance(region, dict):
        return region
    compact: Dict[str, Any] = {}
    for key in _TRACE_REGION_KEYS:
        if key not in region:
            continue
        value = region.get(key)
        if key in ("system_prompt", "user_prompt"):
            value = _truncate_text(value, 4000)
        elif key == "accepted_candidate" and isinstance(value, dict):
            value = {
                "labels": list(value.get("labels") or [])[:64],
                "target": list(value.get("target") or [])[:64],
                "score": value.get("score"),
            }
        elif key == "rejection_reasons" and isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            value = [str(x) for x in list(value)[:32]]
        compact[key] = value
    if "llm_attempts" in region:
        attempts = region.get("llm_attempts")
        if isinstance(attempts, list):
            compact["llm_attempt_count"] = len(attempts)
    return compact


def _compact_llm_trace(trace: Any) -> Any:
    if not isinstance(trace, dict):
        return trace
    compact: Dict[str, Any] = {}
    for key in ("template_name", "sequence_length", "ok", "warnings"):
        if key in trace:
            compact[key] = trace.get(key)
    regions = trace.get("regions")
    if isinstance(regions, list):
        compact["regions"] = [_compact_trace_region(region) for region in regions]
    return compact


def _compact_structure(structure: Any) -> Any:
    if not isinstance(structure, dict):
        return structure
    compact = dict(structure)
    id_pairs = compact.get("id_pairs")
    if isinstance(id_pairs, list) and len(id_pairs) > 256:
        compact["id_pairs"] = id_pairs[:256]
        compact["id_pair_count_stored"] = 256
    return compact


def _compact_result_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    compact = dict(row)
    if "llm_trace" in compact:
        compact["llm_trace"] = _compact_llm_trace(compact.get("llm_trace"))
    if "structure" in compact:
        compact["structure"] = _compact_structure(compact.get("structure"))
    return compact


def _compact_results_for_storage(results: Any) -> Any:
    if isinstance(results, list):
        return [_compact_result_row(row) for row in results]
    return _compact_result_row(results)


def is_session_store_enabled() -> bool:
    return MYSQL_CONFIG is not None


def _get_conn():
    if MYSQL_CONFIG is None:
        raise RuntimeError(
            "Alignment session store is disabled: missing MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DATABASE."
        )
    try:
        cfg = dict(MYSQL_CONFIG)
        # Session payloads can include multi-row alignment traces and reference baselines.
        # Give write/read operations a more realistic budget than the generic tiny defaults.
        cfg["connect_timeout"] = max(int(cfg.get("connect_timeout", 3) or 3), 5)
        cfg["read_timeout"] = max(int(cfg.get("read_timeout", 6) or 6), 20)
        cfg["write_timeout"] = max(int(cfg.get("write_timeout", 6) or 6), 20)
        return pymysql.connect(**cfg)  # type: ignore[arg-type]
    except pymysql.MySQLError as e:
        raise RuntimeError(f"Alignment session store unavailable: {e}") from e


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_table(conn) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS alignment_sessions (
            request_id VARCHAR(32) NOT NULL PRIMARY KEY,
            email VARCHAR(255) NULL,
            payload LONGTEXT NOT NULL,
            results LONGTEXT NOT NULL,
            created_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            INDEX idx_expires (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with conn.cursor() as cur:
        cur.execute(sql)


def _cleanup_expired(conn, now: datetime) -> int:
    sql = "DELETE FROM alignment_sessions WHERE expires_at < %s"
    with conn.cursor() as cur:
        cur.execute(sql, (now.replace(tzinfo=None),))
        return cur.rowcount or 0


def _generate_request_id() -> str:
    # 12 hex chars, short and URL-safe.
    return secrets.token_hex(6)


def _normalize_email(email: Optional[str]) -> Optional[str]:
    normalized = str(email or "").strip().lower()
    return normalized or None


def create_session(
    payload: Dict[str, Any],
    results: Any,
    email: Optional[str] = None,
    ttl_days: int = 7,
) -> Dict[str, Any]:
    now = _utc_now()
    expires = now + timedelta(days=ttl_days)
    normalized_email = _normalize_email(email)
    payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    compact_results = _compact_results_for_storage(results)
    results_json = json.dumps(compact_results, ensure_ascii=True, separators=(",", ":"))

    with _get_conn() as conn:
        _ensure_table(conn)
        _cleanup_expired(conn, now)
        for _ in range(5):
            request_id = _generate_request_id()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO alignment_sessions
                        (request_id, email, payload, results, created_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            request_id,
                            normalized_email,
                            payload_json,
                            results_json,
                            now.replace(tzinfo=None),
                            expires.replace(tzinfo=None),
                        ),
                    )
                conn.commit()
                return {
                    "request_id": request_id,
                    "email": normalized_email,
                    "created_at": now.isoformat(),
                    "expires_at": expires.isoformat(),
                }
            except pymysql.err.IntegrityError:
                continue
        raise RuntimeError("Failed to generate unique request_id.")


def update_session_email(request_id: str, email: str) -> Optional[Dict[str, Any]]:
    now = _utc_now()
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise ValueError("Email is required.")

    with _get_conn() as conn:
        _ensure_table(conn)
        _cleanup_expired(conn, now)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT expires_at
                FROM alignment_sessions
                WHERE request_id = %s AND expires_at >= %s
                LIMIT 1
                """,
                (request_id, now.replace(tzinfo=None)),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            cur.execute(
                "UPDATE alignment_sessions SET email = %s WHERE request_id = %s",
                (normalized_email, request_id),
            )
        conn.commit()
    expires_at = row.get("expires_at")
    return {
        "request_id": request_id,
        "email": normalized_email,
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
    }


def fetch_session(request_id: str) -> Optional[Dict[str, Any]]:
    now = _utc_now()
    with _get_conn() as conn:
        _ensure_table(conn)
        _cleanup_expired(conn, now)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id, email, payload, results, created_at, expires_at
                FROM alignment_sessions
                WHERE request_id = %s
                LIMIT 1
                """,
                (request_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        expires_at = row.get("expires_at")
        if expires_at and isinstance(expires_at, datetime):
            if expires_at.replace(tzinfo=timezone.utc) < now:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM alignment_sessions WHERE request_id = %s",
                        (request_id,),
                    )
                conn.commit()
                return None
        payload = json.loads(row.get("payload") or "{}")
        results = json.loads(row.get("results") or "[]")
        return {
            "request_id": row.get("request_id"),
            "email": row.get("email"),
            "payload": payload,
            "results": results,
            "created_at": row.get("created_at").isoformat()
            if row.get("created_at")
            else None,
            "expires_at": row.get("expires_at").isoformat()
            if row.get("expires_at")
            else None,
        }


def list_sessions_by_email(email: str, limit: int = 20) -> Dict[str, Any]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return {"email": "", "items": []}
    safe_limit = max(1, min(int(limit), 100))
    now = _utc_now()
    with _get_conn() as conn:
        _ensure_table(conn)
        _cleanup_expired(conn, now)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id, created_at, expires_at
                FROM alignment_sessions
                WHERE email = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (normalized_email, safe_limit),
            )
            rows = cur.fetchall() or []
    items = []
    for row in rows:
        items.append(
            {
                "request_id": row.get("request_id"),
                "created_at": row.get("created_at").isoformat()
                if row.get("created_at")
                else None,
                "expires_at": row.get("expires_at").isoformat()
                if row.get("expires_at")
                else None,
            }
        )
    return {"email": normalized_email, "items": items}
