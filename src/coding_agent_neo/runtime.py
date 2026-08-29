"""Per-agent runtime state and explicit execution dependencies.

There is deliberately no module-level current runtime.  A caller constructs
one :class:`AgentRuntime` for each agent and passes it to the loop or tools.
All mutable state that belongs to an agent is either held directly by that
runtime or created through a per-instance default factory.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import TYPE_CHECKING, Any, Protocol

from coding_agent_neo.models import (
    AgentId,
    CorrelationId,
    EventEnvelope,
    EventType,
    IdFactoryLike,
    ProviderToolCallId,
    SessionId,
    UUIDIdFactory,
    _coerce_identifier,
    _validate_nonnegative,
    new_id,
    utc_now,
)

if TYPE_CHECKING:
    from coding_agent_neo.environment.base import ExecutionEnvironment


class CancellationRequested(RuntimeError):
    """Raised when a cancellable operation observes a cancelled signal."""


# A descriptive alias makes call sites read naturally while preserving one
# exception type for callers that need to catch it.
CancelledError = CancellationRequested


@dataclass(slots=True)
class CancellationSignal:
    """A cooperative, thread-safe cancellation signal owned by one runtime."""

    _event: Event = field(default_factory=Event, init=False, repr=False, compare=False)
    _reason: str | None = field(default=None, init=False, repr=False)

    def cancel(self, reason: str = "cancelled") -> bool:
        """Set the signal and return whether this call changed its state."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancellation reason must not be empty")
        changed = not self._event.is_set()
        if changed:
            self._reason = reason
            self._event.set()
        return changed

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancelled(self) -> bool:
        """Short compatibility spelling for ``is_cancelled``."""

        return self.is_cancelled

    def is_set(self) -> bool:
        """Event-like spelling useful to environment implementations."""

        return self.is_cancelled

    @property
    def reason(self) -> str | None:
        return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation and return the signal state."""

        return self._event.wait(timeout)

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancellationRequested(self._reason or "cancelled")

    # ``check`` is useful for environment implementations and keeps the
    # cancellation contract easy to discover without prescribing async APIs.
    check = throw_if_cancelled


class LimitReason(StrEnum):
    """Specific budget dimension that prevented more work."""

    MODEL_STEPS = "model_steps"
    TOOL_CALLS = "tool_calls"
    PROTOCOL_ERRORS = "protocol_errors"
    WALL_TIME = "wall_time"
    CONTEXT_WINDOW = "context_window"


@dataclass(slots=True)
class BudgetTracker:
    """Mutable per-runtime counters, limits, and injectable monotonic clock."""

    max_steps: int | None = None
    max_tool_calls: int | None = None
    max_protocol_errors: int | None = None
    max_wall_seconds: float | None = None
    model_steps: int = 0
    tool_calls: int = 0
    protocol_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: float | None = None
    deadline: float | None = None
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    monotonic_clock: Callable[[], float] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_nonnegative(self.max_steps, "max_steps", integer=True)
        _validate_nonnegative(self.max_tool_calls, "max_tool_calls", integer=True)
        _validate_nonnegative(self.max_protocol_errors, "max_protocol_errors", integer=True)
        _validate_nonnegative(self.max_wall_seconds, "max_wall_seconds")
        for name in (
            "model_steps",
            "tool_calls",
            "protocol_errors",
            "input_tokens",
            "output_tokens",
        ):
            _validate_nonnegative(getattr(self, name), name, integer=True)
        if self.monotonic_clock is not None:
            if not callable(self.monotonic_clock):
                raise TypeError("monotonic_clock must be callable")
            self.clock = self.monotonic_clock
        if not callable(self.clock):
            raise TypeError("clock must be callable")
        if self.started_at is None:
            self.started_at = self.clock()
        _validate_nonnegative(self.started_at, "started_at")
        _validate_nonnegative(self.deadline, "deadline")
        if self.max_wall_seconds is not None:
            expected_deadline = self.started_at + self.max_wall_seconds
            if self.deadline is None:
                self.deadline = expected_deadline
            elif self.deadline < self.started_at:
                raise ValueError("deadline must not precede started_at")

    @property
    def steps(self) -> int:
        """Alias for the model-step counter."""

        return self.model_steps

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.clock() - (self.started_at or 0.0))

    @property
    def remaining_wall_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - self.clock())

    def record_model_step(self, count: int = 1) -> int:
        self._increment(count, "count")
        self.model_steps += count
        return self.model_steps

    # This spelling mirrors the field used in the architecture pseudocode.
    record_step = record_model_step

    def record_tool_call(self, count: int = 1) -> int:
        self._increment(count, "count")
        self.tool_calls += count
        return self.tool_calls

    def record_protocol_error(self, count: int = 1) -> int:
        self._increment(count, "count")
        self.protocol_errors += count
        return self.protocol_errors

    def record_tokens(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self._increment(input_tokens, "input_tokens")
        self._increment(output_tokens, "output_tokens")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def _increment(self, count: int, name: str) -> None:
        _validate_nonnegative(count, name, integer=True)

    def limit_reached(self) -> LimitReason | None:
        """Return the first configured exhausted limit, if any."""

        if self.max_steps is not None and self.model_steps >= self.max_steps:
            return LimitReason.MODEL_STEPS
        if self.max_tool_calls is not None and self.tool_calls >= self.max_tool_calls:
            return LimitReason.TOOL_CALLS
        if (
            self.max_protocol_errors is not None
            and self.protocol_errors >= self.max_protocol_errors
        ):
            return LimitReason.PROTOCOL_ERRORS
        if self.deadline is not None and self.clock() >= self.deadline:
            return LimitReason.WALL_TIME
        return None

    @property
    def exhausted(self) -> bool:
        return self.limit_reached() is not None

    def snapshot(self) -> dict[str, int | float | None]:
        """Return a detached, serialization-friendly view of this tracker."""

        return {
            "model_steps": self.model_steps,
            "tool_calls": self.tool_calls,
            "protocol_errors": self.protocol_errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "started_at": self.started_at,
            "deadline": self.deadline,
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_protocol_errors": self.max_protocol_errors,
            "max_wall_seconds": self.max_wall_seconds,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(slots=True)
class ContextState:
    """Current Agent's context projection, never the complete session history."""

    latest_summary: str | None = None
    covered_through_sequence: int = 0
    recent_messages: MutableSequence[Mapping[str, Any]] = field(default_factory=list)
    recent_message_sequences: MutableSequence[int | None] = field(default_factory=list)
    degraded_through_sequence: int = 0
    degraded_notice: str | None = None

    def __post_init__(self) -> None:
        if self.latest_summary is not None and not isinstance(self.latest_summary, str):
            raise TypeError("latest_summary must be a string or None")
        _validate_nonnegative(
            self.covered_through_sequence, "covered_through_sequence", integer=True
        )
        _validate_nonnegative(
            self.degraded_through_sequence, "degraded_through_sequence", integer=True
        )
        if self.degraded_notice is not None and not isinstance(self.degraded_notice, str):
            raise TypeError("degraded_notice must be a string or None")
        if not isinstance(self.recent_messages, Sequence):
            raise TypeError("recent_messages must be a sequence")
        if not isinstance(self.recent_message_sequences, Sequence):
            raise TypeError("recent_message_sequences must be a sequence")
        # Copy even explicitly supplied lists so a runtime cannot accidentally
        # mutate a caller's list while building a model projection.
        self.recent_messages = list(self.recent_messages)
        self.recent_message_sequences = list(self.recent_message_sequences)
        if len(self.recent_message_sequences) > len(self.recent_messages):
            raise ValueError("recent_message_sequences cannot outnumber messages")
        for sequence in self.recent_message_sequences:
            _validate_nonnegative(sequence, "message sequence", integer=True)
        self.recent_message_sequences.extend(
            [None] * (len(self.recent_messages) - len(self.recent_message_sequences))
        )

    @property
    def summary(self) -> str | None:
        return self.latest_summary

    @summary.setter
    def summary(self, value: str | None) -> None:
        if value is not None and not isinstance(value, str):
            raise TypeError("summary must be a string or None")
        self.latest_summary = value

    @property
    def covered_sequence(self) -> int:
        return self.covered_through_sequence

    @covered_sequence.setter
    def covered_sequence(self, value: int) -> None:
        _validate_nonnegative(value, "covered_sequence", integer=True)
        self.covered_through_sequence = value

    @property
    def recent_projection(self) -> MutableSequence[Mapping[str, Any]]:
        return self.recent_messages

    def append_message(self, message: Mapping[str, Any], *, sequence: int | None) -> None:
        """Append one projected message with its canonical event sequence."""

        if not isinstance(message, Mapping):
            raise TypeError("message must be a mapping")
        _validate_nonnegative(sequence, "message sequence", integer=True)
        self.recent_message_sequences.extend(
            [None] * (len(self.recent_messages) - len(self.recent_message_sequences))
        )
        self.recent_messages.append(message)
        self.recent_message_sequences.append(sequence)


class ExecutionPolicy(Protocol):
    """Minimal policy dependency accepted by :class:`AgentRuntime`.

    The concrete decision vocabulary and approval adapter belong to the later
    policy/executor task.  Runtime only keeps the explicit dependency.
    """

    def decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext | None = None,
    ) -> str:
        """Return a policy decision such as ``allow``, ``ask`` or ``deny``."""


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Explicit dependencies exposed to a tool implementation."""

    agent_id: AgentId | str
    correlation_id: CorrelationId | str
    environment: ExecutionEnvironment
    cancellation: CancellationSignal
    provider_tool_call_id: ProviderToolCallId | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _coerce_identifier(self.agent_id, AgentId))
        object.__setattr__(
            self,
            "correlation_id",
            _coerce_identifier(self.correlation_id, CorrelationId),
        )
        if self.environment is None:
            raise ValueError("environment is required")
        if not isinstance(self.cancellation, CancellationSignal):
            raise TypeError("cancellation must be a CancellationSignal")
        if self.provider_tool_call_id is not None:
            object.__setattr__(
                self,
                "provider_tool_call_id",
                _coerce_identifier(self.provider_tool_call_id, ProviderToolCallId),
            )


@dataclass(slots=True)
class AgentRuntime:
    """All mutable state and execution dependencies for one Agent.

    ``agent_id``, ``session_id``, ``environment`` and ``execution_policy`` are
    intentionally required.  This applies equally to a root runtime; no
    process-global fallback can accidentally hide a missing dependency.
    """

    agent_id: AgentId | str
    session_id: SessionId | str
    environment: ExecutionEnvironment
    execution_policy: ExecutionPolicy
    parent_agent_id: AgentId | str | None = None
    context_state: ContextState = field(default_factory=ContextState)
    budget: BudgetTracker = field(default_factory=BudgetTracker)
    active_tools: set[str] = field(default_factory=set)
    cancellation: CancellationSignal = field(default_factory=CancellationSignal)
    id_factory: IdFactoryLike = field(default_factory=UUIDIdFactory, repr=False, compare=False)
    clock: Callable[[], datetime] = field(default=utc_now, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _coerce_identifier(self.agent_id, AgentId))
        object.__setattr__(self, "session_id", _coerce_identifier(self.session_id, SessionId))
        if self.parent_agent_id is not None:
            object.__setattr__(
                self,
                "parent_agent_id",
                _coerce_identifier(self.parent_agent_id, AgentId),
            )
            if self.parent_agent_id == self.agent_id:
                raise ValueError("parent_agent_id must differ from agent_id")
        if self.environment is None:
            raise ValueError("environment is required")
        if self.execution_policy is None:
            raise ValueError("execution_policy is required")
        if not isinstance(self.context_state, ContextState):
            raise TypeError("context_state must be a ContextState")
        if not isinstance(self.budget, BudgetTracker):
            raise TypeError("budget must be a BudgetTracker")
        if not isinstance(self.cancellation, CancellationSignal):
            raise TypeError("cancellation must be a CancellationSignal")
        if not isinstance(self.active_tools, set):
            self.active_tools = set(self.active_tools)
        if any(not isinstance(name, str) or not name for name in self.active_tools):
            raise ValueError("active_tools must contain non-empty strings")
        # Ensure explicit mutable collections cannot be shared accidentally.
        self.active_tools = set(self.active_tools)
        if not callable(self.id_factory) and not hasattr(self.id_factory, "new_id"):
            raise TypeError("id_factory must be callable or expose new_id")
        if not callable(self.clock):
            raise TypeError("clock must be callable")

    @property
    def budget_tracker(self) -> BudgetTracker:
        return self.budget

    @property
    def policy(self) -> ExecutionPolicy:
        return self.execution_policy

    def new_id(self, kind: str) -> str:
        """Generate an ID through this runtime's injected factory."""

        return new_id(self.id_factory, kind)

    def new_correlation_id(self) -> CorrelationId:
        return CorrelationId(self.new_id("correlation"))

    def new_event(
        self,
        *,
        sequence: int,
        type: str | EventType,
        payload: Mapping[str, Any] | None = None,
        correlation_id: CorrelationId | str | None = None,
        provider_tool_call_id: str | None = None,
    ) -> EventEnvelope:
        """Create an event owned by this runtime with injected ID/clock sources."""

        return EventEnvelope.create(
            session_id=self.session_id,
            agent_id=self.agent_id,
            sequence=sequence,
            type=type,
            id_factory=self.id_factory,
            clock=self.clock,
            parent_agent_id=self.parent_agent_id,
            correlation_id=correlation_id,
            provider_tool_call_id=provider_tool_call_id,
            payload=payload,
        )


# Imported after the runtime classes so the Protocol can refer back to
# ``CancellationSignal`` without an import cycle during module initialization.
from coding_agent_neo.environment.base import ExecutionEnvironment  # noqa: E402

__all__ = [
    "AgentRuntime",
    "BudgetTracker",
    "CancelledError",
    "CancellationRequested",
    "CancellationSignal",
    "ContextState",
    "ExecutionPolicy",
    "LimitReason",
    "ToolExecutionContext",
]
