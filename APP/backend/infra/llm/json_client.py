from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Optional

import requests

from .provider_config import (
    apply_openai_provider_extras,
    load_provider_runtime_config,
    openai_chat_completions_url,
    openai_temperature,
)
from .usage_tracker import record_llm_usage


class JsonCompletionError(RuntimeError):
    pass


class JsonTruncatedError(JsonCompletionError):
    """The provider answered up to its completion budget and stopped mid-answer.

    This is the one failure a caller can act on by spending more budget, and it
    is worth naming because the observable symptom used to be an HTTP 200 whose
    body was empty: the model had used every token on reasoning.
    """


RETRYABLE_HTTP_STATUSES = frozenset(
    {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
)
ROTATABLE_CREDENTIAL_HTTP_STATUSES = frozenset({401, 402, 403, 429})

# Credential health is shared by all benchmark workers in this process.  A
# local index inside complete() would retry a known-bad key for every case.
_CREDENTIAL_STATE_LOCK = threading.Lock()
_CREDENTIAL_STATES: Dict[tuple[str, str], Dict[str, Any]] = {}
_CREDENTIAL_POOL_NEXT_INDEX: Dict[tuple[tuple[str, str], ...], int] = {}


@dataclass(frozen=True)
class JsonCompletion:
    payload: Dict[str, Any]
    provider: str
    model: str
    status_code: int
    elapsed_sec: float
    usage: Dict[str, Any]
    # Why the provider stopped: "stop" answered the question, "length" ran out
    # of completion budget. Only "length" matters here, because it is what tells
    # a caller that a malformed answer was cut off rather than written badly.
    finish_reason: str = ""


def _finish_reason(data: Mapping[str, Any]) -> str:
    """Stopping reason from either SSE chunks or a single JSON body."""
    direct = str(data.get("finish_reason") or "")
    if direct:
        return direct
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    if isinstance(first, Mapping):
        return str(first.get("finish_reason") or "")
    return ""


def _json_object_from_text(raw: Any) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonCompletionError("LLM response was not a JSON object") from exc
    if not isinstance(value, dict):
        raise JsonCompletionError("LLM response JSON must be an object")
    return value


def _message_content(data: Mapping[str, Any], mode: str) -> str:
    if mode == "ollama":
        message = data.get("message")
        if isinstance(message, Mapping):
            return str(message.get("content") or "")
        return str(data.get("response") or "")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping)
        )
    return ""


def _retry_after_seconds(response: requests.Response) -> Optional[float]:
    raw = str(response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _credential_identity(credential: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(credential.get("base_url") or "").rstrip("/"),
        str(credential.get("api_key") or ""),
    )


def _reserve_credential_slot(
    credentials: List[Dict[str, str]],
    min_interval_s: float,
    *,
    preferred_index: Optional[int] = None,
    ignore_preferred_cooldown: bool = False,
) -> tuple[int, float]:
    """Reserve the next per-credential request slot.

    A 402/401/403 credential is disabled for the process.  A 429 credential is
    only cooled down, allowing another key to continue immediately.  The
    returned delay is slept outside the lock.
    """
    interval_s = max(0.0, float(min_interval_s))
    pool_key = tuple(_credential_identity(item) for item in credentials)
    while True:
        with _CREDENTIAL_STATE_LOCK:
            now = time.monotonic()
            next_index = int(_CREDENTIAL_POOL_NEXT_INDEX.get(pool_key, 0))
            ordered: List[int] = []
            if preferred_index is not None and 0 <= preferred_index < len(credentials):
                ordered.append(preferred_index)
            ordered.extend(
                index
                for offset in range(len(credentials))
                for index in [(next_index + offset) % len(credentials)]
                if index not in ordered
            )

            earliest_ready: Optional[float] = None
            for index in ordered:
                identity = _credential_identity(credentials[index])
                state = _CREDENTIAL_STATES.setdefault(
                    identity,
                    {"disabled": False, "cooldown_until": 0.0, "next_request_at": 0.0},
                )
                if bool(state.get("disabled")):
                    continue
                cooldown_until = float(state.get("cooldown_until") or 0.0)
                if index == preferred_index and ignore_preferred_cooldown:
                    cooldown_until = 0.0
                ready_at = max(cooldown_until, float(state.get("next_request_at") or 0.0))
                if ready_at <= now:
                    state["next_request_at"] = now + interval_s
                    _CREDENTIAL_POOL_NEXT_INDEX[pool_key] = (index + 1) % len(credentials)
                    return index, 0.0
                earliest_ready = (
                    ready_at
                    if earliest_ready is None
                    else min(earliest_ready, ready_at)
                )

            if earliest_ready is None:
                raise JsonCompletionError("No usable LLM credential remains")
            delay_s = max(0.0, earliest_ready - now)
        if delay_s:
            time.sleep(delay_s)


def _mark_credential_disabled(credential: Mapping[str, Any]) -> None:
    identity = _credential_identity(credential)
    with _CREDENTIAL_STATE_LOCK:
        state = _CREDENTIAL_STATES.setdefault(identity, {})
        state["disabled"] = True


def _has_enabled_credential(credentials: List[Dict[str, str]]) -> bool:
    with _CREDENTIAL_STATE_LOCK:
        return any(
            not bool(
                _CREDENTIAL_STATES.get(_credential_identity(credential), {}).get(
                    "disabled"
                )
            )
            for credential in credentials
        )


def _mark_credential_rate_limited(
    credential: Mapping[str, Any],
    cooldown_s: float,
) -> None:
    identity = _credential_identity(credential)
    with _CREDENTIAL_STATE_LOCK:
        state = _CREDENTIAL_STATES.setdefault(identity, {})
        state["cooldown_until"] = max(
            float(state.get("cooldown_until") or 0.0),
            time.monotonic() + max(0.0, float(cooldown_s)),
        )


def _reset_credential_health() -> None:
    """Reset process-local state; used by isolated tests and fresh runs."""
    with _CREDENTIAL_STATE_LOCK:
        _CREDENTIAL_STATES.clear()
        _CREDENTIAL_POOL_NEXT_INDEX.clear()


class StructuredJsonLLMClient:
    """Small synchronous client for constrained JSON planning calls."""

    def complete(
        self,
        *,
        messages: List[Dict[str, str]],
        runtime_values_override: Optional[Dict[str, Any]] = None,
        source: str = "agent_planner",
        model_name: str = "",
        max_tokens: int = 512,
    ) -> JsonCompletion:
        runtime_values = dict(runtime_values_override or {})
        def publish(stage: str, **details: Any) -> None:
            callback = runtime_values.get("_status_callback")
            if callable(callback):
                try:
                    callback({"stage": stage, "process_event": {
                        "stage": stage, "source": "system",
                        "at": datetime.now(timezone.utc).isoformat(), **details,
                    }})
                except Exception:
                    pass
        cfg = load_provider_runtime_config(
            runtime_values,
            include_postcheck=False,
            include_max_tokens=True,
        )
        # Planner responses are streamed so the UI can show the model's
        # public answer while retaining the final JSON validation below.
        cfg["streaming"] = str(cfg.get("mode") or "") != "ollama"
        cfg["usage_run_id"] = str(runtime_values.get("LLM_USAGE_RUN_ID") or "")
        cfg["usage_case_id"] = str(runtime_values.get("LLM_USAGE_CASE_ID") or "")
        selected_model = str(model_name or cfg.get("model_name") or "").strip()
        if not selected_model:
            raise JsonCompletionError("No LLM model is configured")

        url, payload = self._request_payload(
            cfg=cfg,
            messages=messages,
            model_name=selected_model,
            max_tokens=max_tokens,
        )
        credentials = [
            {
                "base_url": str(item.get("base_url") or cfg.get("base_url") or "").rstrip("/"),
                "api_key": str(item.get("api_key") or "").strip(),
            }
            for item in list(cfg.get("credentials") or [])
            if isinstance(item, Mapping) and str(item.get("api_key") or "").strip()
        ]
        if not credentials:
            api_keys = [
                str(value).strip()
                for value in list(cfg.get("api_keys") or [])
                if str(value).strip()
            ]
            if not api_keys and str(cfg.get("api_key") or "").strip():
                api_keys = [str(cfg["api_key"]).strip()]
            credentials = [
                {
                    "base_url": str(cfg.get("base_url") or "").rstrip("/"),
                    "api_key": key,
                }
                for key in api_keys
            ]
        if not credentials:
            credentials = [{"base_url": str(cfg.get("base_url") or ""), "api_key": ""}]

        session = requests.Session()
        session.trust_env = False
        started = time.monotonic()
        hard_budget_s = float(cfg["hard_budget_s"])
        max_retries = int(cfg.get("max_retries") or 0)
        response: requests.Response | None = None
        streamed_content = ""
        retry_count = 0
        preferred_index: Optional[int] = None
        ignore_preferred_cooldown = False
        while True:
            credential_index, slot_delay = _reserve_credential_slot(
                credentials,
                float(cfg.get("min_request_interval_s") or 0.0),
                preferred_index=preferred_index,
                ignore_preferred_cooldown=ignore_preferred_cooldown,
            )
            preferred_index = None
            ignore_preferred_cooldown = False
            if slot_delay:
                time.sleep(slot_delay)
            remaining_s = hard_budget_s - (time.monotonic() - started)
            if remaining_s <= 0:
                raise JsonCompletionError("LLM planning call exceeded the hard time budget")
            headers = {"Content-Type": "application/json"}
            request_url = url
            if credentials:
                credential = credentials[credential_index]
                if credential.get("api_key"):
                    headers["Authorization"] = f"Bearer {credential['api_key']}"
                credential_base_url = str(credential.get("base_url") or "")
                if credential_base_url and str(cfg.get("mode") or "") == "openai":
                    request_url = openai_chat_completions_url(credential_base_url)
            attempt_started = time.monotonic()
            publish("llm_waiting", attempt=retry_count + 1)
            try:
                response = session.post(
                    request_url,
                    json=payload,
                    headers=headers,
                    timeout=(
                        min(float(cfg["timeout_connect"]), remaining_s),
                        min(float(cfg["timeout_read"]), remaining_s),
                    ),
                    verify=bool(cfg["verify_ssl"]),
                    stream=bool(cfg.get("streaming")),
                )
                try:
                    response_data, streamed_content, response_text = self._read_response(
                        response,
                        streaming=bool(cfg.get("streaming")),
                        deadline=started + hard_budget_s,
                        callback=runtime_values.get("_status_callback"),
                    )
                finally:
                    response.close()
            except requests.RequestException as exc:
                publish("llm_request_failed", error_type=type(exc).__name__, attempt=retry_count + 1)
                attempt_elapsed = time.monotonic() - attempt_started
                record_llm_usage(
                    cfg=cfg,
                    source=source,
                    url=request_url,
                    payload=payload,
                    status_code=None,
                    elapsed_sec=attempt_elapsed,
                    error_type=type(exc).__name__,
                )
                if retry_count >= max_retries:
                    raise JsonCompletionError(
                        f"LLM request failed: {type(exc).__name__}"
                    ) from exc
                delay_s = float(cfg.get("backoff_base_s") or 0.0) * (2**retry_count)
                retry_count += 1
                if time.monotonic() - started + delay_s >= hard_budget_s:
                    raise JsonCompletionError(
                        "LLM planning call exceeded the hard time budget after "
                        f"{type(exc).__name__}"
                    ) from exc
                publish("llm_retry", attempt=retry_count + 1, retry_in_seconds=delay_s)
                time.sleep(delay_s)
                continue

            attempt_elapsed = time.monotonic() - attempt_started
            usage = self._usage(response_data, str(cfg.get("mode") or ""))
            record_llm_usage(
                cfg=cfg,
                source=source,
                url=request_url,
                payload=payload,
                status_code=response.status_code,
                elapsed_sec=attempt_elapsed,
                response_text=response_text,
                usage=usage,
            )
            if response.status_code == 200:
                publish("llm_response_received", attempt=retry_count + 1)
                break
            if response.status_code in {401, 402, 403}:
                _mark_credential_disabled(credentials[credential_index])
                if not _has_enabled_credential(credentials):
                    raise JsonCompletionError(
                        f"LLM returned HTTP {response.status_code}"
                    )
                retry_count = 0
                continue
            if response.status_code == 429:
                retry_after_s = _retry_after_seconds(response)
                delay_s = (
                    retry_after_s
                    if retry_after_s is not None
                    else float(cfg.get("backoff_base_s") or 0.0) * (2**retry_count)
                )
                _mark_credential_rate_limited(credentials[credential_index], delay_s)
                if retry_count >= max_retries:
                    raise JsonCompletionError("LLM returned HTTP 429 after retries")
                retry_count += 1
                if time.monotonic() - started + delay_s >= hard_budget_s:
                    raise JsonCompletionError(
                        "LLM planning call exceeded the hard time budget"
                    )
                # Wait for the provider-advised interval, then retry this key.
                # Other workers can use another healthy key meanwhile.
                publish("llm_retry", attempt=retry_count + 1, retry_in_seconds=delay_s)
                time.sleep(delay_s)
                preferred_index = credential_index
                ignore_preferred_cooldown = True
                continue
            if (
                response.status_code not in RETRYABLE_HTTP_STATUSES
                or retry_count >= max_retries
            ):
                raise JsonCompletionError(f"LLM returned HTTP {response.status_code}")
            retry_after_s = _retry_after_seconds(response)
            delay_s = (
                retry_after_s
                if retry_after_s is not None
                else float(cfg.get("backoff_base_s") or 0.0) * (2**retry_count)
            )
            retry_count += 1
            if time.monotonic() - started + delay_s >= hard_budget_s:
                raise JsonCompletionError(
                    "LLM planning call exceeded the hard time budget"
                )
            publish("llm_retry", attempt=retry_count + 1, retry_in_seconds=delay_s)
            time.sleep(delay_s)

        if response is None:
            raise JsonCompletionError("LLM request did not return a response")
        elapsed = time.monotonic() - started
        if elapsed > hard_budget_s:
            raise JsonCompletionError("LLM planning call exceeded the hard time budget")

        finish_reason = _finish_reason(response_data)
        content = streamed_content or _message_content(response_data, str(cfg.get("mode") or ""))
        if not content:
            if finish_reason == "length":
                raise JsonTruncatedError(
                    "LLM response stopped at the completion budget before any answer"
                )
            raise JsonCompletionError("LLM response did not contain message content")
        try:
            payload = _json_object_from_text(content)
        except JsonCompletionError as exc:
            # A body that will not parse is a bad answer, unless the provider
            # also said it ran out of room.  Only then is more budget the fix.
            if finish_reason == "length":
                raise JsonTruncatedError(
                    f"LLM response stopped at the completion budget: {exc}"
                ) from exc
            raise
        return JsonCompletion(
            payload=payload,
            provider=str(cfg.get("provider") or ""),
            model=selected_model,
            status_code=response.status_code,
            elapsed_sec=elapsed,
            usage=usage,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _read_response(response, *, streaming, deadline, callback):
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if response.status_code != 200 or not streaming or "application/json" in content_type:
            text = response.text or ""
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {}
            return (data if isinstance(data, dict) else {}), "", text

        # Read the body once. Accessing response.text after iter_lines() has
        # consumed a real requests.Response raises StreamConsumedError.
        # SSE is UTF-8. Requests otherwise defaults text/event-stream without
        # a charset to Latin-1, corrupting Chinese and splitting some bytes
        # into spurious line breaks that silently discard JSON chunks.
        response.encoding = "utf-8"
        parts: List[str] = []
        usage: Dict[str, Any] = {}
        finish_reason = ""
        for line in response.iter_lines(decode_unicode=True, chunk_size=1):
            if time.monotonic() > deadline:
                raise JsonCompletionError("LLM planning call exceeded the hard time budget")
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw == "[DONE]":
                break
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(chunk, Mapping):
                continue
            if isinstance(chunk.get("usage"), Mapping):
                usage = dict(chunk["usage"])
            choices = chunk.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            if isinstance(first, Mapping) and first.get("finish_reason"):
                finish_reason = str(first.get("finish_reason"))
            delta = first.get("delta") if isinstance(first, Mapping) else None
            piece = delta.get("content") if isinstance(delta, Mapping) else None
            # Expose only the public answer, never provider reasoning fields.
            if isinstance(piece, str) and piece:
                parts.append(piece)
                if callable(callback):
                    try:
                        callback({"stage": "llm_delta", "process_event": {
                            "stage": "llm_delta", "source": "agent",
                            "at": datetime.now(timezone.utc).isoformat(),
                            "content": piece,
                        }})
                    except Exception:
                        pass
        text = "".join(parts)
        return {"usage": usage, "finish_reason": finish_reason}, text, text

    @staticmethod
    def _request_payload(
        *,
        cfg: Mapping[str, Any],
        messages: List[Dict[str, str]],
        model_name: str,
        max_tokens: int,
    ) -> tuple[str, Dict[str, Any]]:
        if str(cfg.get("mode") or "") == "ollama":
            return (
                f"{str(cfg['base_url']).rstrip('/')}/api/chat",
                {
                    "model": model_name,
                    "messages": messages,
                    "options": {"temperature": 0},
                    "format": "json",
                    "stream": False,
                },
            )
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": openai_temperature(cfg, 0.0),
            "max_tokens": max(128, min(int(max_tokens), int(cfg.get("max_tokens") or 4096))),
            "stream": bool(cfg.get("streaming")),
            "response_format": {"type": "json_object"},
        }
        extra_body = cfg.get("extra_body")
        if isinstance(extra_body, Mapping):
            for key, value in extra_body.items():
                payload.setdefault(str(key), value)
        if payload["stream"]:
            payload["stream_options"] = {"include_usage": True}
        apply_openai_provider_extras(payload, cfg, model_name=model_name)
        return openai_chat_completions_url(str(cfg["base_url"])), payload

    @staticmethod
    def _usage(data: Mapping[str, Any], mode: str) -> Dict[str, Any]:
        if isinstance(data.get("usage"), Mapping):
            return dict(data["usage"])
        if mode == "ollama":
            return {
                "prompt_tokens": data.get("prompt_eval_count"),
                "completion_tokens": data.get("eval_count"),
                "total_duration": data.get("total_duration"),
            }
        return {}


__all__ = [
    "JsonCompletion",
    "JsonCompletionError",
    "ROTATABLE_CREDENTIAL_HTTP_STATUSES",
    "RETRYABLE_HTTP_STATUSES",
    "StructuredJsonLLMClient",
]
