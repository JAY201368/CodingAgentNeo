"""Single-session ownership and lifecycle for the Agent HTTP binding."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from math import isfinite
from queue import Queue
from typing import Any
from uuid import uuid4

from coding_agent_neo.backend import (
    AgentBackend,
    AgentBackendProvider,
    BackendClosedError,
    CloseSession,
)


class SessionExistsError(RuntimeError):
    """A second transport session was requested while one is active."""


class _EventSubscription:
    """One cancellable consumer attached to a session event pump."""

    _DONE = object()

    def __init__(self, pump: _SessionEventPump, cursor: int) -> None:
        self._pump = pump
        self.cursor = cursor
        self._values: Queue[tuple[str, Any] | object] = Queue()
        self._closed = False
        self._lock = threading.Lock()

    def put(self, value: tuple[str, Any] | object) -> None:
        with self._lock:
            if self._closed:
                return
            self._values.put(value)

    def get(self, timeout: float) -> tuple[str, Any] | object:
        return self._values.get(timeout=timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._pump.unsubscribe(self)


class _SessionEventPump:
    """Share one backend event iterator across all SSE subscribers.

    A backend ``events()`` iterator is allowed to block while waiting for new
    events.  Creating one feeder per browser connection therefore makes a
    disconnected browser leave a thread behind when an injected backend is
    not externally cancellable.  The transport session owns one pump instead;
    subscriber teardown is local and immediate, while the pump remains useful
    for reconnects and is stopped with the transport session.
    """

    def __init__(self, backend: AgentBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._subscribers: set[_EventSubscription] = set()
        self._cache: list[Any] = []
        self._started = False
        self._done = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor = 0

    def subscribe(self, cursor: int) -> _EventSubscription:
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        subscription = _EventSubscription(self, cursor)
        thread_to_start: threading.Thread | None = None
        with self._lock:
            self._subscribers.add(subscription)
            for event in self._cache:
                if _event_sequence(event) > cursor:
                    subscription.put(("event", event))
            if self._done:
                subscription.put(_EventSubscription._DONE)
            elif not self._started:
                self._started = True
                # The pump must retain the complete canonical history for the
                # session.  Individual subscribers apply their own cursor;
                # using the first subscriber's cursor here would make a later
                # lower-cursor reconnect unable to replay older events.
                self._cursor = 0
                thread_to_start = threading.Thread(
                    target=self._run,
                    name="coding-agent-neo-http-session-pump",
                    daemon=True,
                )
                self._thread = thread_to_start
        if thread_to_start is not None:
            thread_to_start.start()
        return subscription

    def unsubscribe(self, subscription: _EventSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def close(self, *, timeout_seconds: float) -> None:
        """Stop subscribers and ask the pump to finish without backend calls."""

        with self._lock:
            self._stop.set()
            self._done = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            thread = self._thread
        for subscription in subscribers:
            subscription.put(_EventSubscription._DONE)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)

    def _run(self) -> None:
        try:
            iterator = self._backend.events(since=self._cursor)
            for event in iterator:
                if self._stop.is_set():
                    break
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._cache.append(event)
                    sequence = _event_sequence(event)
                    if sequence > self._cursor:
                        self._cursor = sequence
                    subscribers = tuple(self._subscribers)
                for subscription in subscribers:
                    if _event_sequence(event) > subscription.cursor:
                        subscription.put(("event", event))
        except BaseException:
            # The HTTP stream has no safe place to expose backend failures.
            # Subscribers receive the same terminal marker as a closed stream.
            pass
        finally:
            with self._lock:
                self._done = True
                subscribers = tuple(self._subscribers)
                self._subscribers.clear()
            for subscription in subscribers:
                subscription.put(_EventSubscription._DONE)


def _event_sequence(event: Any) -> int:
    """Return a safe sequence for pump routing; wire validation happens later."""

    sequence = getattr(event, "sequence", -1)
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        return -1
    return sequence


def _close_backend(
    backend: AgentBackend,
    reason: str,
    timeout_seconds: float,
    *,
    send_command: bool = True,
) -> None:
    """Ask a backend to close and bound the adapter's wait for cleanup.

    ``AgentBackend.close`` is itself required to be bounded by the shared
    service.  The short daemon wrapper also protects the HTTP lifecycle from a
    faulty injected implementation and never reports its exception to a
    client.
    """

    def close_worker() -> None:
        try:
            if send_command:
                try:
                    backend.send(CloseSession(reason))
                except (BackendClosedError, TypeError, ValueError):
                    pass
                except BaseException:
                    pass
            try:
                backend.close()
            except BaseException:
                pass
        finally:
            done.set()

    done = threading.Event()
    worker = threading.Thread(
        target=close_worker,
        name="coding-agent-neo-http-close",
        daemon=True,
    )
    worker.start()
    done.wait(timeout_seconds)


class TransportSession:
    """An opaque transport ID and its one injected backend port."""

    def __init__(
        self,
        transport_session_id: str,
        backend: AgentBackend,
        *,
        initial_cursor: int = 0,
    ) -> None:
        if (
            isinstance(initial_cursor, bool)
            or not isinstance(initial_cursor, int)
            or initial_cursor < 0
        ):
            raise ValueError("initial_cursor must be a non-negative integer")
        self.transport_session_id = transport_session_id
        self._backend = backend
        self._lock = threading.Lock()
        self._closed = False
        self._cursor = initial_cursor
        self._close_thread: threading.Thread | None = None
        self._event_pump = _SessionEventPump(backend)

    @property
    def backend(self) -> AgentBackend:
        """Return the injected port for route-local calls."""

        return self._backend

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def state(self) -> Any:
        return self._backend.last_state

    @property
    def approval_mode(self) -> str:
        value = getattr(self._backend, "approval_mode", "ask")
        return value if value in {"ask", "auto", "deny"} else "ask"

    def record_event(self, sequence: int) -> None:
        """Advance the status cursor only after an SSE event was emitted."""

        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            return
        with self._lock:
            if sequence > self._cursor:
                self._cursor = sequence

    def subscribe_events(self, cursor: int) -> _EventSubscription:
        """Attach one SSE consumer without creating a connection feeder."""

        return self._event_pump.subscribe(cursor)

    def mark_closed(self) -> bool:
        """Mark this session closed and return whether this call won the race."""

        with self._lock:
            if self._closed:
                return False
            self._closed = True
            return True

    def close(
        self,
        *,
        reason: str,
        timeout_seconds: float,
        send_command: bool = True,
        wait: bool = True,
    ) -> bool:
        """Close the backend once; repeated calls are harmless."""

        with self._lock:
            first = not self._closed
            if first:
                self._closed = True
            close_thread = self._close_thread
        if first:
            # A backend iterator may be blocked outside the transport's
            # control.  Subscriber teardown is immediate, and the pump join
            # is deliberately short so session shutdown remains bounded by
            # the backend close lifecycle rather than an uncooperative stream.
            self._event_pump.close(timeout_seconds=min(timeout_seconds, 0.1))
            if wait:
                _close_backend(
                    self._backend,
                    reason,
                    timeout_seconds,
                    send_command=send_command,
                )
            else:
                close_thread = threading.Thread(
                    target=_close_backend,
                    args=(self._backend, reason, timeout_seconds),
                    kwargs={"send_command": send_command},
                    name="coding-agent-neo-http-close-async",
                    daemon=True,
                )
                with self._lock:
                    self._close_thread = close_thread
                    close_thread.start()
        elif wait and close_thread is not None:
            close_thread.join(timeout=timeout_seconds)
        return True


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Internal immutable view used by diagnostics and tests."""

    transport_session_id: str
    cursor: int
    closed: bool


class TransportSessionRegistry:
    """Own at most one active backend and retain closed IDs for 410 replies."""

    def __init__(
        self,
        provider: AgentBackendProvider,
        *,
        close_timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(provider, AgentBackendProvider):
            raise TypeError("provider must implement AgentBackendProvider")
        if (
            isinstance(close_timeout_seconds, bool)
            or not isinstance(close_timeout_seconds, (int, float))
            or not isfinite(close_timeout_seconds)
            or close_timeout_seconds <= 0
        ):
            raise ValueError("close_timeout_seconds must be a positive number")
        self._provider = provider
        self._close_timeout = float(close_timeout_seconds)
        self._lock = threading.Lock()
        self._sessions: dict[str, TransportSession] = {}
        self._active_id: str | None = None

    @property
    def active(self) -> TransportSession | None:
        with self._lock:
            if self._active_id is None:
                return None
            session = self._sessions.get(self._active_id)
            if session is None or session.closed:
                return None
            return session

    @property
    def provider(self) -> AgentBackendProvider:
        """Return the one provider owned by this registry."""

        return self._provider

    def create(self, *, resume_session_id: str | None = None) -> TransportSession:
        """Allocate one new or resumed backend through the provider."""

        with self._lock:
            if self._active_id is not None:
                active = self._sessions.get(self._active_id)
                if active is not None and not active.closed:
                    raise SessionExistsError("an active transport session already exists")
            backend = self._provider.create_session(resume_session_id=resume_session_id)
            if not isinstance(backend, AgentBackend):
                raise TypeError("provider must return an AgentBackend port")
            initial_cursor = 0
            if resume_session_id is not None:
                initial_cursor = getattr(backend, "resume_last_sequence", 0)
                if initial_cursor is None:
                    initial_cursor = 0
                if (
                    isinstance(initial_cursor, bool)
                    or not isinstance(initial_cursor, int)
                    or initial_cursor < 0
                ):
                    raise TypeError("provider resume cursor must be a non-negative integer")
            transport_session_id = f"transport_{uuid4().hex}"
            try:
                session = TransportSession(
                    transport_session_id,
                    backend,
                    initial_cursor=initial_cursor,
                )
            except BaseException:
                _close_backend(
                    backend,
                    "session_start_failed",
                    self._close_timeout,
                    send_command=False,
                )
                raise
            self._sessions[transport_session_id] = session
            self._active_id = transport_session_id
            return session

    def get(self, transport_session_id: str) -> TransportSession | None:
        """Return a known session, including a closed one."""

        with self._lock:
            return self._sessions.get(transport_session_id)

    def close_session(
        self,
        transport_session_id: str,
        *,
        reason: str,
        send_command: bool = True,
        wait: bool = True,
    ) -> bool:
        """Close one known session and release the active slot."""

        with self._lock:
            session = self._sessions.get(transport_session_id)
            if session is None:
                return False
            if self._active_id == transport_session_id:
                self._active_id = None
        session.close(
            reason=reason,
            timeout_seconds=self._close_timeout,
            send_command=send_command,
            wait=wait,
        )
        return True

    def close_all(self, *, reason: str = "server_shutdown") -> None:
        """Close all sessions without exposing backend diagnostics."""

        with self._lock:
            sessions = tuple(self._sessions.values())
            self._active_id = None
        for session in sessions:
            session.close(reason=reason, timeout_seconds=self._close_timeout)

    def snapshot(self, transport_session_id: str) -> RegistrySnapshot | None:
        session = self.get(transport_session_id)
        if session is None:
            return None
        return RegistrySnapshot(
            transport_session_id=session.transport_session_id,
            cursor=session.cursor,
            closed=session.closed,
        )


SessionRegistry = TransportSessionRegistry


__all__ = [
    "AgentBackendProvider",
    "RegistrySnapshot",
    "SessionExistsError",
    "SessionRegistry",
    "TransportSession",
    "TransportSessionRegistry",
]
