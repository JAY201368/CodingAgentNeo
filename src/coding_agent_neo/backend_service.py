"""Shared frontend-independent Agent backend service and runtime.

``AgentBackendService`` is the concrete implementation of the public
``AgentBackend`` port.  It owns the single worker, the Store-first event
buffer, and the channel-backed approval port used by both in-process and
future transport adapters.  It intentionally has no terminal, HTTP, or Web
I/O.

Default timeouts (overridable at assembly):

- ``DEFAULT_APPROVAL_TIMEOUT_SECONDS`` (120): how long a hanging ``ask`` waits
  for ``ApprovalResponse`` before fail-closed rejection.
- ``DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS`` (30): how long ``close()`` waits
  for the worker to exit.
- ``DEFAULT_EVENT_POLL_TIMEOUT_SECONDS`` (0.1): bound used by ``events()`` so a
  consumer ``KeyboardInterrupt`` is not stuck inside an unbounded wait.

The worker runs the existing synchronous Agent Loop without changing its
decision, policy, environment, or persistence semantics.  Cross-thread shared
state is limited to the event stream buffer, approval channel, and runtime
``CancellationSignal``.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.backend import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    AgentBackend,
    AgentCommand,
    ApprovalResponse,
    BackendClosedError,
    CloseSession,
    Interrupt,
    SubmitTask,
    TurnInProgressError,
)
from coding_agent_neo.events import EventDispatchError, EventEmitter, PendingEvent, safe_json_value
from coding_agent_neo.models import EventEnvelope, EventType, RuntimeState
from coding_agent_neo.policy import ApprovalRequest
from coding_agent_neo.session import SessionStore

_APPROVAL_SUMMARY_LIMIT = 300
_TERMINAL_STATES = frozenset(
    {
        RuntimeState.FAILED,
        RuntimeState.LIMIT_REACHED,
        RuntimeState.INTERRUPTED,
    }
)


def _coerce_state(value: Any) -> RuntimeState | None:
    if isinstance(value, RuntimeState):
        return value
    if isinstance(value, str):
        try:
            return RuntimeState(value)
        except ValueError:
            return None
    return None


class EventStreamBuffer:
    """Store-first subscriber that buffers canonical events and wakes waiters.

    ``publish`` only appends and notifies; it never waits for a consumer, so a
    slow frontend cannot block Loop execution or JSONL persistence.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._events: list[EventEnvelope] = []
        self._closed = False
        self._last_state = RuntimeState.RUNNING

    def publish(self, event: EventEnvelope) -> None:
        with self._condition:
            self._events.append(event)
            self._apply_state(event)
            self._condition.notify_all()

    on_event = publish
    handle_event = publish

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def last_state(self) -> RuntimeState:
        with self._lock:
            return self._last_state

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def snapshot(self) -> tuple[EventEnvelope, ...]:
        with self._lock:
            return tuple(self._events)

    def iter_since(self, since: int, *, poll_timeout: float) -> Iterator[EventEnvelope]:
        if (
            isinstance(poll_timeout, bool)
            or not isinstance(poll_timeout, (int, float))
            or poll_timeout <= 0
        ):
            raise ValueError("poll_timeout must be a positive number")
        cursor = since
        while True:
            with self._condition:
                while True:
                    batch = [event for event in self._events if event.sequence > cursor]
                    if batch:
                        break
                    if self._closed:
                        return
                    self._condition.wait(timeout=poll_timeout)
            for event in batch:
                yield event
                cursor = event.sequence

    def _apply_state(self, event: EventEnvelope) -> None:
        name = str(event.type)
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if name == EventType.APPROVAL_REQUEST.value:
            if self._last_state not in _TERMINAL_STATES:
                self._last_state = RuntimeState.WAITING_FOR_APPROVAL
            return
        if name == EventType.POLICY_DECISION.value:
            if self._last_state is RuntimeState.WAITING_FOR_APPROVAL:
                self._last_state = RuntimeState.RUNNING
            return
        if name in {
            EventType.TURN_END.value,
            EventType.SESSION_END.value,
            EventType.AGENT_END.value,
        }:
            parsed = _coerce_state(payload.get("state"))
            if parsed is not None:
                self._last_state = parsed
            return
        if name in {
            EventType.SESSION_START.value,
            EventType.AGENT_START.value,
            EventType.USER_MESSAGE.value,
        }:
            if self._last_state not in _TERMINAL_STATES:
                self._last_state = RuntimeState.RUNNING


class ApprovalChannel:
    """Thread-safe one-slot channel between frontend ``send`` and the worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending_id: str | None = None
        self._response: ApprovalResponse | None = None
        self._closed = False
        self._interrupt_reason: str | None = None
        self._mismatch = False

    def begin(self, request_id: str) -> None:
        with self._condition:
            self._pending_id = request_id
            self._response = None
            self._mismatch = False

    def wait(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            try:
                while True:
                    if self._closed or self._interrupt_reason is not None or self._mismatch:
                        return False
                    if self._response is not None:
                        if self._response.request_id != self._pending_id:
                            return False
                        return self._response.approved
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(timeout=remaining)
            finally:
                self._pending_id = None
                self._response = None
                self._mismatch = False

    def respond(self, response: ApprovalResponse) -> None:
        with self._condition:
            if self._pending_id is None:
                return
            if response.request_id != self._pending_id:
                self._mismatch = True
                self._condition.notify_all()
                return
            self._response = response
            self._condition.notify_all()

    def interrupt(self, reason: str) -> None:
        with self._condition:
            self._interrupt_reason = reason
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def _arguments_summary(tool_name: str, arguments: Mapping[str, Any]) -> str:
    safe = safe_json_value(dict(arguments))
    if tool_name == "bash" and isinstance(safe, Mapping) and isinstance(safe.get("command"), str):
        command = safe["command"]
        if len(command) > _APPROVAL_SUMMARY_LIMIT:
            command = f"{command[: _APPROVAL_SUMMARY_LIMIT - 3]}…"
        return json.dumps(command, ensure_ascii=False)
    text = json.dumps(safe, ensure_ascii=False)
    if len(text) > _APPROVAL_SUMMARY_LIMIT:
        text = f"{text[: _APPROVAL_SUMMARY_LIMIT - 3]}…"
    return text


class ChannelApprovalPort:
    """Approval port that publishes a request and waits for the frontend.

    Terminal I/O stays in the frontend. Timeout, interrupt, ``close()``, and
    ``request_id`` mismatch are fail-closed rejections; this port never
    auto-approves.
    """

    interactive = True

    def __init__(
        self,
        emitter: EventEmitter,
        channel: ApprovalChannel,
        *,
        session_id: str,
        agent_id: str,
        timeout_seconds: float,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")
        self._emitter = emitter
        self._channel = channel
        self._session_id = session_id
        self._agent_id = agent_id
        self.timeout_seconds = float(timeout_seconds)

    def request_approval(self, request: ApprovalRequest) -> bool:
        request_id = str(request.correlation_id)
        self._channel.begin(request_id)
        payload = {
            "request_id": request_id,
            "tool_name": request.tool_name,
            "arguments_summary": _arguments_summary(request.tool_name, request.arguments),
            "timeout_seconds": self.timeout_seconds,
        }
        event = PendingEvent(
            session_id=self._session_id,
            agent_id=self._agent_id,
            type=EventType.APPROVAL_REQUEST,
            correlation_id=request.correlation_id,
            provider_tool_call_id=request.provider_tool_call_id,
            payload=payload,
        )
        try:
            report = self._emitter.publish(event)
        except EventDispatchError as error:
            if error.report.event is None:
                return False
        else:
            if report.event is None:
                return False
        return self._channel.wait(self.timeout_seconds)

    approve = request_approval
    confirm = request_approval


class AgentBackendService(AgentBackend):
    """Concrete shared ``AgentBackend`` implementation with one worker."""

    def __init__(
        self,
        loop: AgentLoop,
        store: SessionStore,
        *,
        event_stream: EventStreamBuffer,
        approval_channel: ApprovalChannel,
        worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
        resume_diagnostics: tuple[Any, ...] = (),
        resume_last_sequence: int = 0,
    ) -> None:
        if (
            isinstance(worker_shutdown_timeout_seconds, bool)
            or not isinstance(worker_shutdown_timeout_seconds, (int, float))
            or worker_shutdown_timeout_seconds <= 0
        ):
            raise ValueError("worker_shutdown_timeout_seconds must be a positive number")
        if (
            isinstance(event_poll_timeout_seconds, bool)
            or not isinstance(event_poll_timeout_seconds, (int, float))
            or event_poll_timeout_seconds <= 0
        ):
            raise ValueError("event_poll_timeout_seconds must be a positive number")
        self._loop = loop
        self._store = store
        self._stream = event_stream
        self._approval = approval_channel
        self._worker_shutdown_timeout = float(worker_shutdown_timeout_seconds)
        self._event_poll_timeout = float(event_poll_timeout_seconds)
        self.resume_diagnostics = tuple(resume_diagnostics)
        if (
            isinstance(resume_last_sequence, bool)
            or not isinstance(resume_last_sequence, int)
            or resume_last_sequence < 0
        ):
            raise ValueError("resume_last_sequence must be a non-negative integer")
        self.resume_last_sequence = resume_last_sequence
        self._queue: queue.Queue[AgentCommand] = queue.Queue()
        self._lock = threading.Lock()
        self._turn_in_progress = False
        self._stopped = False
        self._close_called = False
        self._worker = threading.Thread(
            target=self._run_worker,
            name="coding-agent-neo-backend",
            daemon=True,
        )
        self._worker.start()

    @property
    def last_state(self) -> RuntimeState:
        return self._stream.last_state

    def send(self, command: AgentCommand) -> None:
        if not isinstance(command, (SubmitTask, ApprovalResponse, Interrupt, CloseSession)):
            raise TypeError("command must be a public AgentCommand")
        with self._lock:
            if self._stopped:
                raise BackendClosedError("backend session is closed")
            if isinstance(command, ApprovalResponse):
                self._approval.respond(command)
                return
            if isinstance(command, Interrupt):
                self._approval.interrupt(command.reason)
                self._loop.runtime.cancellation.cancel(command.reason)
                return
            if isinstance(command, CloseSession):
                self._request_stop_locked(command)
                return
            if self._turn_in_progress:
                raise TurnInProgressError("SubmitTask is refused while a turn is running")
            self._turn_in_progress = True
            self._queue.put(command)

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ValueError("since must be a non-negative integer")
        return self._stream.iter_since(since, poll_timeout=self._event_poll_timeout)

    def close(self) -> None:
        with self._lock:
            already = self._close_called
            self._close_called = True
            if not self._stopped:
                self._request_stop_locked(CloseSession("session_closed"))
        if already and not self._worker.is_alive():
            self._stream.close()
            return
        self._worker.join(timeout=self._worker_shutdown_timeout)
        if not self._worker.is_alive():
            try:
                self._loop.close(reason="session_closed")
            except BaseException:
                pass
        self._store.close()
        self._stream.close()

    def _request_stop_locked(self, command: CloseSession) -> None:
        self._stopped = True
        self._approval.close()
        if self._turn_in_progress:
            try:
                self._loop.runtime.cancellation.cancel(command.reason)
            except ValueError:
                pass
        self._queue.put(command)

    def _run_worker(self) -> None:
        try:
            while True:
                command = self._queue.get()
                if isinstance(command, CloseSession):
                    try:
                        self._loop.close(reason=command.reason)
                    except BaseException:
                        pass
                    break
                if not isinstance(command, SubmitTask):
                    continue
                try:
                    self._loop.run_turn(command.text)
                except BaseException:
                    pass
                finally:
                    with self._lock:
                        self._turn_in_progress = False
        finally:
            self._stream.close()


# Baseline compatibility name.  This is an alias, not a second implementation.
LocalAgentBackend = AgentBackendService


__all__ = [
    "AgentBackendService",
    "ApprovalChannel",
    "ChannelApprovalPort",
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "DEFAULT_EVENT_POLL_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
    "EventStreamBuffer",
    "LocalAgentBackend",
]
