"""Versioned JSON and Server-Sent Events values for the Agent HTTP binding.

The transport deliberately treats :class:`EventEnvelope` as an already
canonical backend fact.  ``event_to_dict`` only calls its public serialization
method and ``encode_sse`` only frames that result; no payload fields are
interpreted or rewritten here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from re import fullmatch
from typing import Any

from coding_agent_neo.backend import SessionEventPage, SessionHistoryPage
from coding_agent_neo.models import EventEnvelope, RuntimeState

PROTOCOL_VERSION = 1
BASE_PATH = "/api/v1"
HEALTH_PATH = f"{BASE_PATH}/health"
SESSIONS_PATH = f"{BASE_PATH}/sessions"
SSE_MEDIA_TYPE = "text/event-stream"
HISTORY_LIST_DEFAULT_LIMIT = 50
HISTORY_LIST_MAX_LIMIT = 100
HISTORY_EVENT_DEFAULT_LIMIT = 200
HISTORY_EVENT_MAX_LIMIT = 200
HISTORY_SEQUENCE_MAX = 2**63 - 1
HISTORY_EVENT_PAYLOAD_MAX_BYTES = 65_536
HISTORY_PAGE_MAX_BYTES = 8 * 1024 * 1024
_STATE_VALUES = frozenset(item.value for item in RuntimeState)
_HISTORY_SESSION_ID_PATTERN = r"session_[A-Za-z0-9_-]{1,120}"


def _state_value(state: Any) -> str:
    """Return a safe wire state without stringifying arbitrary objects."""

    if isinstance(state, RuntimeState):
        return state.value
    if isinstance(state, str) and state in _STATE_VALUES:
        return state
    return RuntimeState.FAILED.value


@dataclass(frozen=True, slots=True)
class HealthResponse:
    """Stable health response for the versioned Agent API."""

    status: str = "ok"
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "protocol_version": self.protocol_version}


@dataclass(frozen=True, slots=True)
class SessionCreatedResponse:
    """Response returned after allocating one transport session."""

    transport_session_id: str
    state: RuntimeState | str
    cursor: int = 0
    approval_mode: str = "ask"

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_session_id": self.transport_session_id,
            "state": _state_value(self.state),
            "cursor": self.cursor,
            "approval_mode": self.approval_mode,
        }


@dataclass(frozen=True, slots=True)
class SessionStatusResponse:
    """Current derived state of a transport session."""

    state: RuntimeState | str
    cursor: int
    closed: bool
    approval_mode: str = "ask"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": _state_value(self.state),
            "cursor": self.cursor,
            "closed": self.closed,
            "approval_mode": self.approval_mode,
        }


@dataclass(frozen=True, slots=True)
class AcceptedResponse:
    """Non-blocking command acknowledgement."""

    accepted: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {"accepted": self.accepted}


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """Build the only error shape exposed by the HTTP binding."""

    return {"error": {"code": code, "message": message}}


def event_to_dict(event: EventEnvelope) -> dict[str, Any]:
    """Serialize a canonical event without changing its business payload."""

    to_dict = getattr(event, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("event must provide the EventEnvelope to_dict binding")
    value = to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("EventEnvelope.to_dict() must return a mapping")
    # A shallow detached dict keeps the wire serializer from exposing a
    # mutable implementation mapping.  Nested values are intentionally left
    # untouched: json.dumps performs the final JSON-compatible encoding.
    return dict(value)


def event_json(event: EventEnvelope) -> str:
    """Return the complete canonical envelope JSON used in an SSE data line."""

    return json.dumps(
        event_to_dict(event),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def encode_sse(event: EventEnvelope) -> str:
    """Encode one canonical event as the binding's SSE frame."""

    sequence = event_to_dict(event).get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("event sequence must be a non-negative integer")
    return f"id: {sequence}\nevent: agent-event\ndata: {event_json(event)}\n\n"


def encode_keepalive() -> str:
    """Encode an SSE comment heartbeat; it is not an Agent event."""

    return ": keepalive\n\n"


class CursorError(ValueError):
    """A query or ``Last-Event-ID`` cursor is not a non-negative integer."""


def parse_cursor(value: str | None, *, field: str = "cursor") -> int:
    """Parse a strict decimal, non-negative event cursor."""

    if value is None:
        return 0
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise CursorError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError, OverflowError) as error:
        raise CursorError(f"{field} must be a non-negative integer") from error
    if parsed < 0:
        raise CursorError(f"{field} must be a non-negative integer")
    return parsed


class HistoryIdError(ValueError):
    """A history path or resume identifier is not an opaque safe ID."""


class HistoryCursorError(ValueError):
    """A finite-history list or sequence cursor is malformed or out of range."""


class HistoryLimitError(ValueError):
    """A finite-history page limit is malformed or outside its documented bound."""


def parse_history_id(value: str) -> str:
    """Validate a path-bound opaque history ID before provider/path access."""

    if not isinstance(value, str) or fullmatch(_HISTORY_SESSION_ID_PATTERN, value) is None:
        raise HistoryIdError
    return value


def parse_history_list_cursor(value: str | None) -> str | None:
    """Validate the local shape of an opaque list cursor.

    Provider-issued validity remains provider-owned; this function only
    enforces the transport's bounded ASCII token shape.
    """

    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256 or not value.isascii():
        raise HistoryCursorError
    return value


def parse_history_sequence(value: str | None) -> int:
    """Parse a strict non-negative finite-history sequence cursor."""

    if value is None:
        return 0
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise HistoryCursorError
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError, OverflowError) as error:
        raise HistoryCursorError from error
    if parsed < 0 or parsed > HISTORY_SEQUENCE_MAX:
        raise HistoryCursorError
    return parsed


def parse_history_limit(
    value: str | None,
    *,
    default: int,
    maximum: int,
) -> int:
    """Parse a strict ASCII decimal history page limit."""

    if value is None:
        return default
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise HistoryLimitError
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError, OverflowError) as error:
        raise HistoryLimitError from error
    if not 1 <= parsed <= maximum:
        raise HistoryLimitError
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _payload_preview(payload: Mapping[str, Any], limit: int) -> dict[str, Any]:
    serialized = _canonical_json(payload)
    original_length = len(serialized.encode("utf-8"))

    def encode_size(value: Mapping[str, Any]) -> int:
        return len(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    def preview(character_count: int) -> dict[str, Any]:
        head_count = (character_count + 1) // 2
        tail_count = character_count // 2
        return {
            "truncated": True,
            "original_length": original_length,
            "limit": limit,
            "encoding": "utf-8",
            "head": serialized[:head_count],
            "tail": serialized[-tail_count:] if tail_count else "",
        }

    low, high = 0, len(serialized)
    best = preview(0)
    while low <= high:
        middle = (low + high) // 2
        candidate = preview(middle)
        if encode_size(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _bounded_history_event_dict(event: Mapping[str, Any], payload_limit: int) -> dict[str, Any]:
    result = dict(event)
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("history event payload must be a mapping")
    serialized = _canonical_json(payload)
    if len(serialized.encode("utf-8")) > payload_limit:
        result["payload"] = _payload_preview(payload, payload_limit)
    return result


def history_page_to_dict(page: SessionHistoryPage) -> dict[str, Any]:
    """Map a provider history DTO to its exact finite JSON object."""

    if not isinstance(page, SessionHistoryPage):
        raise TypeError("history page must be a SessionHistoryPage")
    value = page.to_dict()
    if not isinstance(value, dict):
        raise TypeError("history page serialization must be an object")
    return value


def event_page_to_dict(page: SessionEventPage) -> dict[str, Any]:
    """Map and defensively bound a provider event page for finite JSON."""

    if not isinstance(page, SessionEventPage):
        raise TypeError("event page must be a SessionEventPage")
    source = page.to_dict()
    events = source.get("events")
    if not isinstance(events, list):
        raise TypeError("event page events must be a list")
    serialized_events = [event_to_dict(event) for event in page.events]

    def build(payload_limit: int) -> dict[str, Any]:
        return {
            "session_id": source["session_id"],
            "events": [
                _bounded_history_event_dict(event, payload_limit) for event in serialized_events
            ],
            "next_cursor": source["next_cursor"],
            "has_more": source["has_more"],
            "diagnostics": source["diagnostics"],
        }

    value = build(HISTORY_EVENT_PAYLOAD_MAX_BYTES)
    if len(_canonical_json(value).encode("utf-8")) > HISTORY_PAGE_MAX_BYTES:
        count = max(len(serialized_events), 1)
        payload_limit = max(
            128,
            (HISTORY_PAGE_MAX_BYTES - len(_canonical_json(build(128)).encode("utf-8"))) // count,
        )
        payload_limit = min(payload_limit, HISTORY_EVENT_PAYLOAD_MAX_BYTES)
        value = build(payload_limit)
        while (
            len(_canonical_json(value).encode("utf-8")) > HISTORY_PAGE_MAX_BYTES
            and payload_limit > 128
        ):
            size = len(_canonical_json(value).encode("utf-8"))
            payload_limit = max(128, payload_limit * HISTORY_PAGE_MAX_BYTES // size - 1)
            value = build(payload_limit)
    if len(_canonical_json(value).encode("utf-8")) > HISTORY_PAGE_MAX_BYTES:
        raise ValueError("history event page exceeds its response bound")
    return value


def select_cursor(since: str | None, last_event_id: str | None) -> int:
    """Apply the binding rule: when both cursors exist, use the larger one."""

    return max(
        parse_cursor(since, field="since"),
        parse_cursor(last_event_id, field="Last-Event-ID"),
    )


__all__ = [
    "AcceptedResponse",
    "BASE_PATH",
    "CursorError",
    "HISTORY_EVENT_DEFAULT_LIMIT",
    "HISTORY_EVENT_MAX_LIMIT",
    "HISTORY_EVENT_PAYLOAD_MAX_BYTES",
    "HISTORY_LIST_DEFAULT_LIMIT",
    "HISTORY_LIST_MAX_LIMIT",
    "HISTORY_PAGE_MAX_BYTES",
    "HISTORY_SEQUENCE_MAX",
    "HistoryCursorError",
    "HistoryIdError",
    "HistoryLimitError",
    "HEALTH_PATH",
    "HealthResponse",
    "PROTOCOL_VERSION",
    "SESSIONS_PATH",
    "SSE_MEDIA_TYPE",
    "SessionCreatedResponse",
    "SessionStatusResponse",
    "encode_keepalive",
    "encode_sse",
    "error_body",
    "event_page_to_dict",
    "event_json",
    "event_to_dict",
    "history_page_to_dict",
    "parse_history_id",
    "parse_history_limit",
    "parse_history_list_cursor",
    "parse_history_sequence",
    "parse_cursor",
    "select_cursor",
]
