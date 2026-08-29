"""T06 event persistence redaction and hostile-object safety checks."""

from __future__ import annotations

import json

from coding_agent_neo.events import UNSUPPORTED_OBJECT, PendingEvent
from coding_agent_neo.session import SessionStore


class HostileProviderObject:
    def __init__(self) -> None:
        self.string_calls = 0

    def __str__(self) -> str:
        self.string_calls += 1
        raise AssertionError("provider object string conversion must not run")

    def __repr__(self) -> str:
        self.string_calls += 1
        raise AssertionError("provider object repr must not run")


def test_nested_secret_fields_and_inline_authorization_are_redacted(tmp_path) -> None:
    sentinel_values = {
        "api-key-value-8472",
        "password-value-8472",
        "bearer-value-8472",
        "cookie-value-8472",
    }
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)
    store.append(
        PendingEvent(
            "session-1",
            "agent-1",
            "error",
            payload={
                "api_key": "api-key-value-8472",
                "nested": {
                    "password": "password-value-8472",
                    "headers": {"Authorization": "Bearer bearer-value-8472"},
                },
                "cookies": ["cookie-value-8472"],
                "message": "request failed with Bearer bearer-value-8472",
            },
        )
    )

    raw = (tmp_path / "session.jsonl").read_text(encoding="utf-8")
    assert not any(value in raw for value in sentinel_values)
    payload = json.loads(raw)["payload"]
    assert payload["api_key"] == "<redacted>"
    assert payload["nested"]["password"] == "<redacted>"
    assert payload["nested"]["headers"]["Authorization"] == "<redacted>"
    assert payload["cookies"] == "<redacted>"
    assert payload["message"] == "request failed with Bearer <redacted>"


def test_usage_token_counters_survive_without_weakening_token_redaction(tmp_path) -> None:
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)
    store.append(
        PendingEvent(
            "session-1",
            "agent-1",
            "assistant_message",
            payload={
                "usage": {
                    "input_tokens": 17,
                    "output-tokens": 5,
                    "total_tokens": 22,
                },
                "access_token": "access-token-value-8472",
                "token": "generic-token-value-8472",
            },
        )
    )

    raw = (tmp_path / "session.jsonl").read_text(encoding="utf-8")
    payload = json.loads(raw)["payload"]
    assert payload["usage"] == {
        "input_tokens": 17,
        "output-tokens": 5,
        "total_tokens": 22,
    }
    assert payload["access_token"] == "<redacted>"
    assert payload["token"] == "<redacted>"
    assert "access-token-value-8472" not in raw
    assert "generic-token-value-8472" not in raw


def test_unknown_provider_objects_are_never_stringified_or_persisted(tmp_path) -> None:
    provider_object = HostileProviderObject()
    hostile_key = HostileProviderObject()
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)

    persisted = store.append(
        PendingEvent(
            "session-1",
            "agent-1",
            "assistant_message",
            payload={
                "normalized": "safe",
                "raw_response": provider_object,
                hostile_key: "secret-under-unsupported-key-8472",
            },
        )
    )

    assert provider_object.string_calls == 0
    assert hostile_key.string_calls == 0
    assert persisted.payload["raw_response"] == UNSUPPORTED_OBJECT
    raw = (tmp_path / "session.jsonl").read_text(encoding="utf-8")
    assert "HostileProviderObject" not in raw
    assert "secret-under-unsupported-key-8472" not in raw


def test_cycles_are_replaced_without_leaking_or_recursing_forever(tmp_path) -> None:
    cyclic = {}
    cyclic["self"] = cyclic
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)

    persisted = store.append(
        PendingEvent(
            "session-1",
            "agent-1",
            "error",
            payload={"cycle": cyclic},
        )
    )

    assert persisted.payload["cycle"]["self"] == UNSUPPORTED_OBJECT
