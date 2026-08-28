"""Unit tests for per-agent runtime state and cancellation/budget contracts."""

from datetime import UTC, datetime

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.models import EventType
from coding_agent_neo.runtime import (
    AgentRuntime,
    BudgetTracker,
    CancellationRequested,
    CancellationSignal,
    ContextState,
    LimitReason,
    ToolExecutionContext,
)


class FakeClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeUtcClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, 12, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def test_runtime_requires_explicit_environment_and_policy() -> None:
    with pytest.raises(TypeError):
        AgentRuntime("agent-1", "session-1")  # type: ignore[call-arg]

    with pytest.raises(ValueError):
        AgentRuntime("agent-1", "session-1", None, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentRuntime("agent-1", "session-1", FakeExecutionEnvironment(), None)  # type: ignore[arg-type]


def test_runtime_default_mutable_state_is_isolated() -> None:
    first_environment = FakeExecutionEnvironment()
    second_environment = FakeExecutionEnvironment()
    first = AgentRuntime("agent-1", "session-1", first_environment, object())
    second = AgentRuntime("agent-2", "session-1", second_environment, object())

    assert first.context_state is not second.context_state
    assert first.budget is not second.budget
    assert first.active_tools is not second.active_tools
    assert first.cancellation is not second.cancellation
    assert first.environment is not second.environment

    first.context_state.recent_messages.append({"role": "user", "content": "one"})
    first.budget.record_model_step()
    first.active_tools.add("read_file")
    first.cancellation.cancel("test")

    assert second.context_state.recent_messages == []
    assert second.budget.model_steps == 0
    assert second.active_tools == set()
    assert not second.cancellation.is_cancelled
    assert first_environment.calls == []
    assert second_environment.calls == []


def test_context_state_copies_projection_list_and_validates_sequence() -> None:
    messages = [{"role": "assistant", "content": "hello"}]
    state = ContextState(
        latest_summary="summary", covered_through_sequence=4, recent_messages=messages
    )
    messages.append({"role": "user", "content": "outside"})

    assert state.summary == "summary"
    assert state.covered_sequence == 4
    assert len(state.recent_projection) == 1
    state.summary = "new summary"
    state.covered_sequence = 5
    assert state.latest_summary == "new summary"
    assert state.covered_through_sequence == 5

    with pytest.raises(ValueError):
        ContextState(covered_through_sequence=-1)


def test_budget_tracker_rejects_negative_budget_and_tracks_clock_limits() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError):
        BudgetTracker(max_steps=-1)
    with pytest.raises(ValueError):
        BudgetTracker(max_wall_seconds=-0.1)

    budget = BudgetTracker(
        max_steps=2, max_tool_calls=2, max_protocol_errors=1, max_wall_seconds=5, clock=clock
    )
    assert budget.started_at == 10.0
    assert budget.deadline == 15.0
    assert budget.limit_reached() is None

    budget.record_model_step()
    budget.record_tool_call()
    budget.record_protocol_error()
    budget.record_tokens(input_tokens=3, output_tokens=2)
    assert budget.protocol_errors == 1
    assert budget.input_tokens == 3
    assert budget.output_tokens == 2
    assert budget.limit_reached() is LimitReason.PROTOCOL_ERRORS

    with pytest.raises(ValueError):
        budget.record_tool_call(-1)

    budget.protocol_errors = 0
    budget.record_model_step()
    assert budget.limit_reached() is LimitReason.MODEL_STEPS
    clock.value = 15.0
    assert budget.remaining_wall_seconds == 0.0


def test_cancellation_signal_is_independent_and_cooperative() -> None:
    first = CancellationSignal()
    second = CancellationSignal()
    assert not first.is_cancelled
    assert first.cancel("user interrupt")
    assert not first.cancel("second reason")
    assert first.reason == "user interrupt"
    assert second.reason is None
    assert not second.is_cancelled

    with pytest.raises(CancellationRequested, match="user interrupt"):
        first.throw_if_cancelled()
    with pytest.raises(ValueError):
        second.cancel(" ")


def test_tool_execution_context_carries_explicit_runtime_dependencies() -> None:
    environment = FakeExecutionEnvironment()
    cancellation = CancellationSignal()
    context = ToolExecutionContext(
        agent_id="agent-1",
        correlation_id="correlation-1",
        provider_tool_call_id="provider-1",
        environment=environment,
        cancellation=cancellation,
    )

    assert context.agent_id == "agent-1"
    assert context.correlation_id == "correlation-1"
    assert context.provider_tool_call_id == "provider-1"
    assert context.environment is environment
    assert context.cancellation is cancellation


def test_runtime_injects_ids_and_utc_clock_into_events() -> None:
    ids = iter(("event-1", "event-2"))
    clock = FakeUtcClock()

    def next_id(kind: str) -> str:
        assert kind == "event"
        return next(ids)

    runtime = AgentRuntime(
        "agent-1",
        "session-1",
        FakeExecutionEnvironment(),
        object(),
        id_factory=next_id,
        clock=clock,
    )
    event = runtime.new_event(sequence=0, type=EventType.SESSION_START)
    assert event.event_id == "event-1"
    assert event.agent_id == "agent-1"
    assert event.session_id == "session-1"
    assert event.timestamp == "2026-08-28T12:30:00.000000Z"


def test_no_argument_id_factory_is_a_supported_test_seam() -> None:
    generated = iter(("event-1",))
    runtime = AgentRuntime(
        "agent-1",
        "session-1",
        FakeExecutionEnvironment(),
        object(),
        id_factory=lambda: next(generated),  # type: ignore[assignment]
    )
    assert runtime.new_event(sequence=0, type=EventType.SESSION_START).event_id == "event-1"
