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
from collections.abc import Iterator
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
    "AgentBackend",
    "AgentCommand",
    "ApprovalChannel",
    "ApprovalResponse",
    "BackendClosedError",
    "ChannelApprovalPort",
    "CloseSession",
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "DEFAULT_EVENT_POLL_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
    "EventStreamBuffer",
    "Interrupt",
    "LocalAgentBackend",
    "SubmitTask",
    "TurnInProgressError",
]
