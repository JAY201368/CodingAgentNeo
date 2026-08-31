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
from typing import Any

from coding_agent_neo.models import EventEnvelope, RuntimeState

PROTOCOL_VERSION = 1
BASE_PATH = "/api/v1"
HEALTH_PATH = f"{BASE_PATH}/health"
SESSIONS_PATH = f"{BASE_PATH}/sessions"
SSE_MEDIA_TYPE = "text/event-stream"
_STATE_VALUES = frozenset(item.value for item in RuntimeState)


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_session_id": self.transport_session_id,
            "state": _state_value(self.state),
            "cursor": self.cursor,
        }


@dataclass(frozen=True, slots=True)
class SessionStatusResponse:
    """Current derived state of a transport session."""

    state: RuntimeState | str
    cursor: int
    closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": _state_value(self.state),
            "cursor": self.cursor,
            "closed": self.closed,
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
    "event_json",
    "event_to_dict",
    "parse_cursor",
    "select_cursor",
]
