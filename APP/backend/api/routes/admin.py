# -*- coding: utf-8 -*-
"""
Internal admin APIs (frontend-hosted console).

This module intentionally keeps only API endpoints. The admin UI is served by
frontend entry `admin.html`.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from backend.infra.config.runtime_settings import (
    RUNTIME_SETTINGS_SCHEMA,
    coerce_runtime_updates,
    get_effective_runtime_settings,
    get_runtime_overrides_path_label,
    get_schema_payload,
    is_prompt_store_enabled,
    load_prompt_overrides,
    load_runtime_overrides,
    update_runtime_settings,
)
from backend.domain.alignment.postcheck.variable_loop import probe_llm_connection, probe_varloop_prompt
from backend.application.alignment.prompt_trace import trace_prompt_pipeline


logger = logging.getLogger(__name__)
_ENTRY_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LOCAL_AUTH_PATH = Path(__file__).resolve().parents[2] / "infra" / "config" / "admin_auth.json"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _get_ttl_seconds() -> int:
    raw = os.getenv("ADMIN_SESSION_TTL_MIN", "30")
    try:
        mins = int(raw)
    except ValueError:
        mins = 30
    mins = max(1, min(mins, 24 * 60))
    return mins * 60


def _is_authenticated() -> bool:
    now = int(time.time())
    exp = int(session.get("admin_auth_exp", 0) or 0)
    if exp > now:
        return True
    session.pop("admin_auth_exp", None)
    return False


def _mark_authenticated() -> int:
    exp = int(time.time()) + _get_ttl_seconds()
    session["admin_auth_exp"] = exp
    return exp


def _clear_auth() -> None:
    session.pop("admin_auth_exp", None)


def _load_local_auth_hash() -> str:
    if not _LOCAL_AUTH_PATH.exists():
        return ""
    try:
        payload = json.loads(_LOCAL_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("password_hash") or "").strip()


def _save_local_auth_hash(password_hash: str) -> None:
    _LOCAL_AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "password_hash": password_hash,
        "updated_at": int(time.time()),
    }
    _LOCAL_AUTH_PATH.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _password_source() -> str:
    if _load_local_auth_hash():
        return "local_file"
    if (os.getenv("ADMIN_PASSWORD_HASH") or "").strip():
        return "env_hash"
    if os.getenv("ADMIN_PASSWORD") not in (None, ""):
        return "env_plain"
    return "unset"


def _has_password_configured() -> bool:
    return _password_source() != "unset"


def _verify_admin_password(password: str) -> bool:
    password = password or ""

    local_hash = _load_local_auth_hash()
    if local_hash:
        try:
            return bool(check_password_hash(local_hash, password))
        except Exception:
            return False

    env_hash = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()
    if env_hash:
        try:
            return bool(check_password_hash(env_hash, password))
        except Exception:
            return False

    env_plain = os.getenv("ADMIN_PASSWORD")
    if env_plain is not None and env_plain != "":
        return hmac.compare_digest(str(env_plain), str(password))

    return False


def _validate_new_password(password: str, confirm: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password != confirm:
        return "Password confirmation does not match."
    return None


def register_admin_routes(app: Flask) -> None:
    if not _env_bool("ADMIN_ENABLED", False):
        return

    token = (os.getenv("ADMIN_ENTRY_TOKEN") or "").strip()
    if not token or not _ENTRY_TOKEN_PATTERN.fullmatch(token):
        logger.warning("Admin API disabled: invalid or missing ADMIN_ENTRY_TOKEN")
        return

    base_path = f"/_internal/{token}"

    def _require_auth_or_404() -> None:
        if not _is_authenticated():
            abort(404)

    @app.get(base_path)
    def admin_entry_info():
        # Keep this endpoint minimal; admin UI is frontend-hosted.
        return jsonify(
            {
                "ok": True,
                "message": "Open frontend admin console: /admin.html?token=<ADMIN_ENTRY_TOKEN>",
            }
        ), 200

    @app.get(f"{base_path}/auth/status")
    def admin_auth_status():
        return jsonify(
            {
                "authenticated": _is_authenticated(),
                "expires_at": int(session.get("admin_auth_exp", 0) or 0),
                "requires_setup": not _has_password_configured(),
                "password_source": _password_source(),
            }
        ), 200

    @app.post(f"{base_path}/auth/bootstrap")
    def admin_auth_bootstrap():
        if _has_password_configured():
            return jsonify({"message": "Password is already configured."}), 400

        data = request.get_json(silent=True) or {}
        password = str(data.get("password") or "")
        confirm = str(data.get("confirm_password") or "")

        err = _validate_new_password(password, confirm)
        if err:
            return jsonify({"message": err}), 400

        _save_local_auth_hash(generate_password_hash(password))
        exp = _mark_authenticated()
        return jsonify({"ok": True, "expires_at": exp}), 200

    @app.post(f"{base_path}/auth/login")
    def admin_auth_login():
        if not _has_password_configured():
            return jsonify({"message": "Password is not initialized."}), 400

        data = request.get_json(silent=True) or {}
        password = str(data.get("password") or "")

        if not _verify_admin_password(password):
            # Keep this endpoint low-signal.
            abort(404)

        exp = _mark_authenticated()
        return jsonify({"ok": True, "expires_at": exp}), 200

    @app.post(f"{base_path}/auth/change")
    def admin_auth_change_password():
        _require_auth_or_404()

        data = request.get_json(silent=True) or {}
        old_password = str(data.get("old_password") or "")
        new_password = str(data.get("new_password") or "")
        confirm_password = str(data.get("confirm_password") or "")

        if not _verify_admin_password(old_password):
            return jsonify({"message": "Current password is incorrect."}), 400

        err = _validate_new_password(new_password, confirm_password)
        if err:
            return jsonify({"message": err}), 400

        _save_local_auth_hash(generate_password_hash(new_password))
        return jsonify({"ok": True, "password_source": _password_source()}), 200

    @app.post(f"{base_path}/auth/logout")
    def admin_auth_logout():
        _clear_auth()
        return jsonify({"ok": True}), 200

    @app.get(f"{base_path}/config/schema")
    def admin_config_schema():
        _require_auth_or_404()
        return jsonify({"schema": get_schema_payload()}), 200

    @app.get(f"{base_path}/config/current")
    def admin_config_current():
        _require_auth_or_404()
        if is_prompt_store_enabled():
            file_overrides = load_runtime_overrides(include_prompt=False)
            prompt_overrides = load_prompt_overrides()
        else:
            file_overrides = load_runtime_overrides(include_prompt=True)
            prompt_overrides = {}
        merged_overrides = dict(file_overrides)
        merged_overrides.update(prompt_overrides)
        return jsonify(
            {
                "values": get_effective_runtime_settings(),
                "overrides": merged_overrides,
                "overrides_path": get_runtime_overrides_path_label(),
            }
        ), 200

    @app.post(f"{base_path}/config/update")
    def admin_config_update():
        _require_auth_or_404()
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        updates = payload.get("updates") or {}
        reset_keys = payload.get("reset_keys") or []
        reset_all = bool(payload.get("reset_all", False))

        if not isinstance(updates, dict):
            return jsonify({"message": "`updates` must be an object"}), 400
        if not isinstance(reset_keys, list):
            return jsonify({"message": "`reset_keys` must be an array"}), 400

        try:
            effective = update_runtime_settings(
                updates=updates,
                reset_keys=[str(x) for x in reset_keys],
                reset_all=reset_all,
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        return jsonify({"ok": True, "values": effective}), 200

    @app.post(f"{base_path}/llm/test")
    def admin_llm_test():
        _require_auth_or_404()
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        raw_settings = payload.get("settings") or {}
        if not isinstance(raw_settings, dict):
            return jsonify({"message": "`settings` must be an object"}), 400

        llm_keys = {
            key
            for key, spec in RUNTIME_SETTINGS_SCHEMA.items()
            if str(spec.get("category") or "").lower() == "llm"
        }
        try:
            updates = coerce_runtime_updates(
                {str(k): v for k, v in raw_settings.items() if str(k) in llm_keys}
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        effective = get_effective_runtime_settings()
        effective.update(updates)
        result = probe_llm_connection(effective)
        status = 200 if bool(result.get("ok")) else 400
        return jsonify(result), status

    @app.post(f"{base_path}/prompt/test")
    def admin_prompt_test():
        _require_auth_or_404()
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        raw_settings = payload.get("settings") or {}
        if not isinstance(raw_settings, dict):
            return jsonify({"message": "`settings` must be an object"}), 400

        prompt_llm_keys = {
            key
            for key, spec in RUNTIME_SETTINGS_SCHEMA.items()
            if str(spec.get("category") or "").lower() in ("llm", "prompt")
        }
        try:
            updates = coerce_runtime_updates(
                {str(k): v for k, v in raw_settings.items() if str(k) in prompt_llm_keys}
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        effective = get_effective_runtime_settings()
        effective.update(updates)
        result = probe_varloop_prompt(effective)
        status = 200 if bool(result.get("ok")) else 400
        return jsonify(result), status

    @app.post(f"{base_path}/prompt/trace")
    def admin_prompt_trace():
        _require_auth_or_404()
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        sequence = str(payload.get("sequence") or "")
        template_name_raw = payload.get("template_name")
        template_name = str(template_name_raw).strip() if template_name_raw not in (None, "") else None
        run_llm = bool(payload.get("run_llm", True))

        raw_settings = payload.get("settings") or {}
        if not isinstance(raw_settings, dict):
            return jsonify({"message": "`settings` must be an object"}), 400

        trace_keys = {
            key
            for key, spec in RUNTIME_SETTINGS_SCHEMA.items()
            if str(spec.get("category") or "").lower() in ("llm", "prompt")
        }
        try:
            updates = coerce_runtime_updates(
                {str(k): v for k, v in raw_settings.items() if str(k) in trace_keys}
            )
            effective = get_effective_runtime_settings()
            effective.update(updates)
            result = trace_prompt_pipeline(
                sequence=sequence,
                runtime_values=effective,
                run_llm=run_llm,
                template_name=template_name,
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            return jsonify({"message": f"Prompt trace failed: {exc}"}), 500
        return jsonify(result), 200

    logger.info("Admin API enabled at hidden path: %s", base_path)
