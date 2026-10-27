from __future__ import annotations

from datetime import datetime

import pytest

from backend.api.routes.align_payloads import (
    parse_session_create_request,
    parse_session_email_update_request,
    parse_session_list_query,
)
from backend.infra.storage import alignment_session_store as store


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def test_session_email_payloads_normalize_consistently():
    created = parse_session_create_request(
        {"payload": {}, "results": [], "email": "  User@Example.COM "}
    )
    updated = parse_session_email_update_request({"email": " USER@example.com "})
    queried = parse_session_list_query({"email": "user@EXAMPLE.com"})

    assert created.email == "user@example.com"
    assert updated.email == "user@example.com"
    assert queried.email == "user@example.com"


@pytest.mark.parametrize("value", ["", "not-an-email", "a@b", "a b@example.com"])
def test_session_email_update_rejects_invalid_addresses(value):
    with pytest.raises(ValueError):
        parse_session_email_update_request({"email": value})


def test_update_session_email_updates_existing_unexpired_session(monkeypatch):
    expires = datetime(2030, 1, 2, 3, 4, 5)
    connection = _Connection({"expires_at": expires})
    monkeypatch.setattr(store, "_get_conn", lambda: connection)
    monkeypatch.setattr(store, "_ensure_table", lambda conn: None)
    monkeypatch.setattr(store, "_cleanup_expired", lambda conn, now: 0)

    result = store.update_session_email("abc123", " User@Example.COM ")

    assert result == {
        "request_id": "abc123",
        "email": "user@example.com",
        "expires_at": "2030-01-02T03:04:05",
    }
    assert connection.cursor_value.calls[-1] == (
        "UPDATE alignment_sessions SET email = %s WHERE request_id = %s",
        ("user@example.com", "abc123"),
    )
    assert connection.commits == 1


def test_update_session_email_returns_none_for_missing_session(monkeypatch):
    connection = _Connection(None)
    monkeypatch.setattr(store, "_get_conn", lambda: connection)
    monkeypatch.setattr(store, "_ensure_table", lambda conn: None)
    monkeypatch.setattr(store, "_cleanup_expired", lambda conn, now: 0)

    assert store.update_session_email("missing", "user@example.com") is None
    assert connection.commits == 1
