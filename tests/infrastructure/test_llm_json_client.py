from __future__ import annotations

from collections import deque
from io import BytesIO

import pytest
import requests
import json

from backend.infra.llm import json_client
from backend.infra.llm.json_client import JsonCompletionError, StructuredJsonLLMClient


@pytest.fixture(autouse=True)
def reset_credential_health():
    json_client._reset_credential_health()
    yield
    json_client._reset_credential_health()


class _Response:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code = status_code
        self._data = dict(data or {})
        self.headers = dict(headers or {"Content-Type": "text/event-stream"})
        self.encoding = None
        self.text = json.dumps(self._data)

    def json(self):
        return dict(self._data)

    def iter_lines(self, decode_unicode=True, chunk_size=1024):
        choices = self._data.get("choices") or []
        message = choices[0].get("message") if choices else {}
        content = str((message or {}).get("content") or "")
        chunk = {"choices": [{"delta": {"content": content}}]}
        yield f"data: {json.dumps(chunk)}"
        yield f"data: {json.dumps({'choices': [], 'usage': self._data.get('usage', {})})}"
        yield "data: [DONE]"

    def close(self):
        return None


class _Session:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.calls = []
        self.trust_env = True

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _config(**overrides):
    config = {
        "provider": "mimo",
        "mode": "openai",
        "base_url": "https://example.invalid/v1",
        "model_name": "mimo-v2.5-pro",
        "api_key": "test-key",
        "verify_ssl": True,
        "timeout_connect": 1.0,
        "timeout_read": 2.0,
        "hard_budget_s": 30.0,
        "max_retries": 2,
        "backoff_base_s": 0.5,
        "min_request_interval_s": 0.0,
        "max_tokens": 512,
    }
    config.update(overrides)
    return config


def _success_response():
    return _Response(
        200,
        {
            "choices": [{"message": {"content": '{"decision":"accept"}'}}],
            "usage": {"total_tokens": 12},
        },
    )


def _complete(monkeypatch, session, config, runtime_values_override=None):
    monkeypatch.setattr(json_client.requests, "Session", lambda: session)
    monkeypatch.setattr(
        json_client,
        "load_provider_runtime_config",
        lambda *args, **kwargs: dict(config),
    )
    return StructuredJsonLLMClient().complete(
        messages=[{"role": "user", "content": "plan"}],
        runtime_values_override=runtime_values_override,
    )


def test_streamed_response_publishes_incremental_content(monkeypatch):
    session = _Session([_success_response()])
    events = []

    completion = _complete(
        monkeypatch,
        session,
        _config(),
        runtime_values_override={"_status_callback": events.append},
    )

    assert completion.payload == {"decision": "accept"}
    deltas = [event["process_event"]["content"] for event in events if event.get("stage") == "llm_delta"]
    assert deltas == ['{"decision":"accept"}']
    assert session.calls[0][1]["json"]["stream"] is True
    assert completion.usage == {"total_tokens": 12}


def test_real_requests_stream_is_consumed_once_and_stops_at_done(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/event-stream"
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "private reasoning"}}]},
        {"choices": [{"delta": {"content": '{"decision":'}}]},
        {"choices": [{"delta": {"content": '"accept"}'}}]},
        {"choices": [], "usage": {"total_tokens": 17}},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    response.raw = BytesIO((body + 'data: [DONE]\n\ndata: {"choices":[{"delta":{"content":"ignored"}}]}\n\n').encode())
    events = []
    completion = _complete(monkeypatch, _Session([response]), _config(), {"_status_callback": events.append})
    assert completion.payload == {"decision": "accept"}
    assert completion.usage == {"total_tokens": 17}
    assert [e["process_event"]["content"] for e in events if e["stage"] == "llm_delta"] == ['{"decision":', '"accept"}']
    assert response.raw.closed


def test_stream_read_failure_retries_and_discards_partial_answer(monkeypatch):
    class InterruptedResponse(_Response):
        closed = False

        def iter_lines(self, **kwargs):
            yield 'data: {"choices":[{"delta":{"content":"incomplete"}}]}'
            raise requests.ConnectionError("stream interrupted")

        def close(self):
            self.closed = True

    response = InterruptedResponse(200)
    session = _Session([response, _success_response()])
    monkeypatch.setattr(json_client.time, "sleep", lambda _: None)
    completion = _complete(monkeypatch, session, _config())
    assert completion.payload == {"decision": "accept"}
    assert len(session.calls) == 2
    assert response.closed


def test_utf8_stream_overrides_requests_latin1_default(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/event-stream"
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    answer = {"assessment": "全部残基映射完整"}
    content = json.dumps(answer, ensure_ascii=False)
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "内部分析"}}]},
        {"choices": [{"delta": {"content": content}}]},
    ]
    body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
    response.raw = BytesIO((body + "data: [DONE]\n\n").encode("utf-8"))
    events = []

    completion = _complete(monkeypatch, _Session([response]), _config(), {"_status_callback": events.append})

    assert completion.payload == answer
    assert [e["process_event"]["content"] for e in events if e["stage"] == "llm_delta"] == [content]


def test_json_response_and_ollama_remain_supported(monkeypatch):
    for mode in ("openai", "ollama"):
        data = {"message": {"content": '{"decision":"accept"}'}} if mode == "ollama" else _success_response()._data
        session = _Session([_Response(200, data, {"Content-Type": "application/json"})])
        completion = _complete(monkeypatch, session, _config(mode=mode))
        assert completion.payload == {"decision": "accept"}
        assert session.calls[0][1]["json"]["stream"] is (mode == "openai")


def test_extra_body_fields_are_forwarded_only_when_configured(monkeypatch):
    session = _Session([_success_response()])
    _complete(monkeypatch, session, _config(extra_body={
        "chat_template_kwargs": {"enable_thinking": False},
    }))
    payload = session.calls[0][1]["json"]
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    session = _Session([_success_response()])
    _complete(monkeypatch, session, _config())
    assert "chat_template_kwargs" not in session.calls[0][1]["json"]


def test_retries_rate_limit_and_respects_retry_after(monkeypatch):
    session = _Session([_Response(429, headers={"Retry-After": "1.25"}), _success_response()])
    sleeps = []
    monkeypatch.setattr(json_client.time, "sleep", sleeps.append)

    completion = _complete(monkeypatch, session, _config())

    assert completion.payload == {"decision": "accept"}
    assert len(session.calls) == 2
    assert sleeps == [1.25]


def test_does_not_retry_non_transient_payment_error(monkeypatch):
    session = _Session([_Response(402), _success_response()])
    sleeps = []
    monkeypatch.setattr(json_client.time, "sleep", sleeps.append)

    with pytest.raises(JsonCompletionError, match="HTTP 402"):
        _complete(monkeypatch, session, _config())

    assert len(session.calls) == 1
    assert sleeps == []


def test_rotates_mimo_credentials_on_payment_error(monkeypatch):
    session = _Session([_Response(402), _success_response()])
    sleeps = []
    monkeypatch.setattr(json_client.time, "sleep", sleeps.append)

    completion = _complete(
        monkeypatch,
        session,
        _config(api_key="first", api_keys=["first", "second"]),
    )

    assert completion.payload == {"decision": "accept"}
    assert len(session.calls) == 2
    first_headers = session.calls[0][1]["headers"]
    second_headers = session.calls[1][1]["headers"]
    assert first_headers["Authorization"] == "Bearer first"
    assert second_headers["Authorization"] == "Bearer second"
    assert sleeps == []


def test_payment_failed_credential_is_skipped_by_later_completion(monkeypatch):
    first_session = _Session([_Response(402), _success_response()])
    _complete(
        monkeypatch,
        first_session,
        _config(api_key="first", api_keys=["first", "second"]),
    )

    second_session = _Session([_success_response()])
    _complete(
        monkeypatch,
        second_session,
        _config(api_key="first", api_keys=["first", "second"]),
    )

    assert len(second_session.calls) == 1
    assert second_session.calls[0][1]["headers"]["Authorization"] == "Bearer second"


def test_rotation_keeps_each_key_bound_to_its_endpoint(monkeypatch):
    session = _Session([_Response(402), _success_response()])
    monkeypatch.setattr(json_client.time, "sleep", lambda _: None)

    completion = _complete(
        monkeypatch,
        session,
        _config(
            credentials=[
                {"base_url": "https://relay.invalid/v1", "api_key": "relay"},
                {"base_url": "https://backup.invalid/v1", "api_key": "backup"},
            ]
        ),
    )

    assert completion.payload == {"decision": "accept"}
    assert session.calls[0][0][0] == "https://relay.invalid/v1/chat/completions"
    assert session.calls[1][0][0] == "https://backup.invalid/v1/chat/completions"


def test_retries_cloudflare_gateway_timeout(monkeypatch):
    session = _Session([_Response(524), _success_response()])
    sleeps = []
    monkeypatch.setattr(json_client.time, "sleep", sleeps.append)

    completion = _complete(monkeypatch, session, _config(backoff_base_s=0.75))

    assert completion.payload == {"decision": "accept"}
    assert len(session.calls) == 2
    assert sleeps == [0.75]


def test_retries_transient_network_error(monkeypatch):
    session = _Session([requests.ConnectionError("temporary"), _success_response()])
    sleeps = []
    monkeypatch.setattr(json_client.time, "sleep", sleeps.append)

    completion = _complete(monkeypatch, session, _config(backoff_base_s=0.25))

    assert completion.payload == {"decision": "accept"}
    assert len(session.calls) == 2
    assert sleeps == [0.25]


def test_request_slots_are_independent_per_credential(monkeypatch):
    monkeypatch.setattr(json_client.time, "monotonic", lambda: 10.0)
    credentials = [
        {"base_url": "https://example.invalid/v1", "api_key": "first"},
        {"base_url": "https://example.invalid/v1", "api_key": "second"},
    ]

    first_index, first_delay = json_client._reserve_credential_slot(credentials, 3.0)
    second_index, second_delay = json_client._reserve_credential_slot(credentials, 3.0)

    assert (first_index, first_delay) == (0, 0.0)
    assert (second_index, second_delay) == (1, 0.0)


def test_live_callback_reports_retry_before_success(monkeypatch):
    session = _Session([requests.ReadTimeout('read timed out'), _success_response()])
    events = []
    monkeypatch.setattr(json_client.requests, 'Session', lambda: session)
    monkeypatch.setattr(json_client, 'load_provider_runtime_config', lambda *a, **k: _config())
    monkeypatch.setattr(json_client.time, 'sleep', lambda _: None)
    completion = StructuredJsonLLMClient().complete(
        messages=[{'role': 'user', 'content': 'plan'}],
        runtime_values_override={'_status_callback': events.append},
    )
    assert completion.payload['decision'] == 'accept'
    assert [e['stage'] for e in events] == [
        'llm_waiting', 'llm_request_failed', 'llm_retry', 'llm_waiting', 'llm_delta', 'llm_response_received',
    ]
    assert events[1]['process_event']['error_type'] == 'ReadTimeout'
    assert events[2]['process_event']['attempt'] == 2
