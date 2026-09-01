"""Frontend-independent Agent command and backend application port.

The concrete worker and in-process runtime live in
``coding_agent_neo.backend_service``.  Keeping this module limited to the
immutable command DTOs, the port, and its public exceptions gives every
transport the same application boundary.

``LocalAgentBackend`` and the former stream/approval helper names are exposed
through lazy compatibility aliases only.  New code should import the concrete
``AgentBackendService`` and its helpers from ``backend_service``.
"""

from __future__ import annotations

# Compatibility aliases are resolved by ``__getattr__`` below rather than
# importing their concrete implementations into this port module.
# ruff: noqa: F822
import re
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from coding_agent_neo.models import EventEnvelope, RuntimeState

DEFAULT_APPROVAL_TIMEOUT_SECONDS = 120.0
DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 30.0
DEFAULT_EVENT_POLL_TIMEOUT_SECONDS = 0.1


class BackendClosedError(RuntimeError):
    """A command was submitted after the backend session had closed."""


class TurnInProgressError(RuntimeError):
    """``SubmitTask`` was sent while a turn was already executing."""


class AgentBackendProviderError(RuntimeError):
    """Base class for stable, credential- and path-free provider errors."""

    code = "backend_provider_error"
    safe_message = "the Agent backend provider could not complete the request"

    def __init__(self) -> None:
        # Never accept or retain a lower-level exception message at this
        # boundary.  The fixed text is safe to expose through any adapter.
        super().__init__(self.safe_message)

    @property
    def message(self) -> str:
        """Compatibility spelling for the fixed safe error message."""

        return self.safe_message


class InvalidSessionHistoryIdError(AgentBackendProviderError):
    """A history or resume ID is not a valid opaque session identifier."""

    code = "invalid_history_id"
    safe_message = "history session ID is invalid"


class InvalidSessionHistoryCursorError(AgentBackendProviderError):
    """A list cursor or finite-history sequence cursor is invalid."""

    code = "invalid_history_cursor"
    safe_message = "history cursor is invalid"


class InvalidSessionHistoryLimitError(AgentBackendProviderError):
    """A history page limit is outside its documented bound."""

    code = "invalid_history_limit"
    safe_message = "history limit is invalid"


class SessionHistoryNotFoundError(AgentBackendProviderError):
    """A valid history ID has no current fixed-directory record."""

    code = "history_not_found"
    safe_message = "session history was not found"


class SessionHistoryUnavailableError(AgentBackendProviderError):
    """A history record cannot be safely parsed or projected."""

    code = "history_unavailable"
    safe_message = "session history is unavailable"


class SessionResumeUnavailableError(AgentBackendProviderError):
    """A current history record cannot be rebuilt as a linear session."""

    code = "invalid_resume"
    safe_message = "session cannot be resumed"


@dataclass(frozen=True, slots=True)
class BoundedText:
    """A UTF-8-byte-bounded text projection with explicit truncation metadata."""

    text: str
    truncated: bool
    original_length: int
    limit: int
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        if (
            isinstance(self.original_length, bool)
            or not isinstance(self.original_length, int)
            or self.original_length < 0
        ):
            raise ValueError("original_length must be a non-negative integer")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise ValueError("limit must be a positive integer")
        if self.encoding != "utf-8":
            raise ValueError("encoding must be utf-8")
        if len(self.text.encode("utf-8")) > self.limit:
            raise ValueError("text exceeds its bound")
        if not self.truncated and self.original_length != len(self.text.encode("utf-8")):
            raise ValueError("untruncated text length does not match text")
        if self.truncated and self.original_length <= self.limit:
            raise ValueError("truncated text must exceed its bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "truncated": self.truncated,
            "original_length": self.original_length,
            "limit": self.limit,
            "encoding": self.encoding,
        }

    as_dict = to_dict


@dataclass(frozen=True, slots=True)
class HistoryDiagnostic:
    """A bounded stable diagnostic for one history candidate."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code or len(self.code) > 64:
            raise ValueError("diagnostic code must be a short non-empty string")
        if not isinstance(self.message, str) or not self.message or len(self.message) > 256:
            raise ValueError("diagnostic message must be a short non-empty string")
        if any(ord(character) < 32 for character in self.code + self.message):
            raise ValueError("diagnostic values must not contain control characters")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    as_dict = to_dict


def _diagnostic_tuple(value: Sequence[HistoryDiagnostic] | None) -> tuple[HistoryDiagnostic, ...]:
    if value is None:
        return ()
    diagnostics = tuple(value)
    if len(diagnostics) > 8:
        raise ValueError("history diagnostics are limited to 8 items")
    if any(not isinstance(item, HistoryDiagnostic) for item in diagnostics):
        raise TypeError("diagnostics must contain HistoryDiagnostic values")
    return diagnostics


_HISTORY_SESSION_ID = re.compile(r"^session_[A-Za-z0-9_-]{1,120}$")


def _validate_history_session_id(value: str) -> None:
    if not isinstance(value, str) or _HISTORY_SESSION_ID.fullmatch(value) is None:
        raise ValueError("session_id must be a valid opaque session ID")


@dataclass(frozen=True, slots=True)
class SessionHistoryItem:
    """Safe bounded projection of one fixed-directory session candidate."""

    session_id: str
    first_user_message: BoundedText | None
    created_at: str | None
    updated_at: str | None
    last_sequence: int
    last_state: str | None
    resumable: bool
    diagnostics: tuple[HistoryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _validate_history_session_id(self.session_id)
        for name in ("created_at", "updated_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")
        if isinstance(self.last_sequence, bool) or not isinstance(self.last_sequence, int):
            raise TypeError("last_sequence must be an integer")
        if self.last_sequence < 0:
            raise ValueError("last_sequence must be non-negative")
        if self.last_state is not None and not isinstance(self.last_state, str):
            raise TypeError("last_state must be a string or None")
        if not isinstance(self.resumable, bool):
            raise TypeError("resumable must be a boolean")
        object.__setattr__(self, "diagnostics", _diagnostic_tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "first_user_message": (
                None if self.first_user_message is None else self.first_user_message.to_dict()
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_sequence": self.last_sequence,
            "last_state": self.last_state,
            "resumable": self.resumable,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    as_dict = to_dict


@dataclass(frozen=True, slots=True)
class SessionHistoryPage:
    """A finite, bounded newest-first history listing page."""

    sessions: tuple[SessionHistoryItem, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        sessions = tuple(self.sessions)
        if any(not isinstance(item, SessionHistoryItem) for item in sessions):
            raise TypeError("sessions must contain SessionHistoryItem values")
        if len(sessions) > 100:
            raise ValueError("sessions are limited to 100 items")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str)
            or not self.next_cursor
            or len(self.next_cursor) > 256
            or not self.next_cursor.isascii()
        ):
            raise ValueError("next_cursor must be a non-empty ASCII token")
        object.__setattr__(self, "sessions", sessions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": [item.to_dict() for item in self.sessions],
            "next_cursor": self.next_cursor,
        }

    as_dict = to_dict


@dataclass(frozen=True, slots=True)
class SessionEventPage:
    """A finite ascending page of canonical historical event envelopes."""

    session_id: str
    events: tuple[EventEnvelope, ...]
    next_cursor: int | None = None
    has_more: bool = False
    diagnostics: tuple[HistoryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _validate_history_session_id(self.session_id)
        events = tuple(self.events)
        if any(not isinstance(event, EventEnvelope) for event in events):
            raise TypeError("events must contain EventEnvelope values")
        if len(events) > 200:
            raise ValueError("events are limited to 200 items")
        if self.next_cursor is not None and (
            isinstance(self.next_cursor, bool)
            or not isinstance(self.next_cursor, int)
            or self.next_cursor < 0
        ):
            raise ValueError("next_cursor must be a non-negative integer or None")
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be a boolean")
        if self.has_more and self.next_cursor is None:
            raise ValueError("has_more pages require a next_cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("pages without more events must have no next_cursor")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "diagnostics", _diagnostic_tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "events": [event.to_dict() for event in self.events],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    as_dict = to_dict


@runtime_checkable
class AgentBackendProvider(Protocol):
    """Workspace-scoped application dependency for history and session creation."""

    def list_sessions(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> SessionHistoryPage: ...

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage: ...

    def create_session(self, *, resume_session_id: str | None = None) -> AgentBackend: ...


@dataclass(frozen=True, slots=True)
class SubmitTask:
    """Ask the backend to run one user turn. JSON-serializable; no callables."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("task text must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "SubmitTask", **asdict(self)}


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    """Answer one hanging approval request identified by ``request_id``."""

    request_id: str
    approved: bool

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "ApprovalResponse", **asdict(self)}


@dataclass(frozen=True, slots=True)
class Interrupt:
    """Cancel the running turn. Takes effect on the caller thread."""

    reason: str = "interrupted"

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("interrupt reason must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Interrupt", **asdict(self)}


@dataclass(frozen=True, slots=True)
class CloseSession:
    """Request an orderly worker shutdown and session close."""

    reason: str = "session_closed"

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("close reason must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "CloseSession", **asdict(self)}


AgentCommand = SubmitTask | ApprovalResponse | Interrupt | CloseSession


@runtime_checkable
class AgentBackend(Protocol):
    """The only backend surface a frontend is allowed to depend on."""

    @property
    def last_state(self) -> RuntimeState: ...

    def send(self, command: AgentCommand) -> None: ...

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]: ...

    def close(self) -> None: ...


_COMPATIBILITY_NAMES = frozenset(
    {"ApprovalChannel", "ChannelApprovalPort", "EventStreamBuffer", "LocalAgentBackend"}
)


def __getattr__(name: str) -> Any:
    """Resolve pre-T01 implementation names without coupling this port module.

    The import is intentionally lazy: importing ``backend`` never imports the
    Agent Loop, worker, event emitter, or session store.  The aliases are kept
    only for callers of the baseline module-level names and all resolve to the
    single implementations owned by ``backend_service``.
    """

    if name in _COMPATIBILITY_NAMES:
        from coding_agent_neo import backend_service

        return getattr(backend_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentBackendProvider",
    "AgentBackendProviderError",
    "AgentBackend",
    "AgentCommand",
    "ApprovalChannel",
    "ApprovalResponse",
    "BoundedText",
    "BackendClosedError",
    "ChannelApprovalPort",
    "CloseSession",
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "DEFAULT_EVENT_POLL_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
    "EventStreamBuffer",
    "HistoryDiagnostic",
    "InvalidSessionHistoryCursorError",
    "InvalidSessionHistoryIdError",
    "InvalidSessionHistoryLimitError",
    "Interrupt",
    "LocalAgentBackend",
    "SessionEventPage",
    "SessionHistoryItem",
    "SessionHistoryNotFoundError",
    "SessionHistoryPage",
    "SessionHistoryUnavailableError",
    "SessionResumeUnavailableError",
    "SubmitTask",
    "TurnInProgressError",
]
