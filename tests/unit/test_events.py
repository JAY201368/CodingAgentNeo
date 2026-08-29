"""T06 canonical event adaptation and synchronous fan-out tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.events import (
    DeliveryStatus,
    EventDispatchError,
    EventEmitter,
    PendingEvent,
)
from coding_agent_neo.executor import ToolExecutor, ToolLifecycleEvent
from coding_agent_neo.models import (
    AgentId,
    CorrelationId,
    EventId,
    ProviderToolCallId,
    SessionId,
)
from coding_agent_neo.policy import DefaultExecutionPolicy
from coding_agent_neo.runtime import AgentRuntime
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry


def test_emitter_persists_canonical_events_before_notifying_renderer(tmp_path) -> None:
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)
    rendered = []
    emitter = EventEmitter(store, [rendered])

    first = emitter.emit(
        session_id="session-1",
        agent_id="agent-1",
        type="user_message",
        payload={"text": "hello"},
    )
    second = emitter.publish(
        PendingEvent(
            session_id="session-1",
            agent_id="agent-1",
            type="assistant_message",
            payload={"text": "hi"},
        )
    )

    assert first.succeeded and second.succeeded
    assert [event.sequence for event in rendered] == [1, 2]
    assert [event.sequence for event in store.read_events()] == [1, 2]
    assert len({event.event_id for event in store.read_events()}) == 2
    assert all(event.agent_id == "agent-1" for event in rendered)


def test_executor_lifecycle_shape_is_adapted_without_changing_ids(tmp_path) -> None:
    event = ToolLifecycleEvent(
        schema_version=1,
        session_id=SessionId("session-1"),
        event_id=EventId("event-1"),
        agent_id=AgentId("agent-1"),
        type="tool_call",
        timestamp="2026-08-29T00:00:00Z",
        correlation_id=CorrelationId("correlation-1"),
        provider_tool_call_id=ProviderToolCallId("provider/call 1"),
        payload={"tool_name": "read_file"},
    )
    store = SessionStore(tmp_path / "session.jsonl", fsync=False)

    persisted = store.append(event)

    assert persisted.sequence == 1
    assert persisted.event_id == "event-1"
    assert persisted.correlation_id == "correlation-1"
    assert persisted.provider_tool_call_id == "provider/call 1"


def test_executor_can_publish_one_lifecycle_to_store_and_renderer(tmp_path) -> None:
    counter = iter(range(20))

    def ids(kind: str) -> str:
        return f"{kind}-{next(counter)}"

    runtime = AgentRuntime(
        "agent-1",
        "session-1",
        FakeExecutionEnvironment(),
        DefaultExecutionPolicy(),
        id_factory=ids,
    )
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False)
    rendered = []
    emitter = EventEmitter(store, [rendered])
    executor = ToolExecutor(runtime, default_tool_registry(), event_publisher=emitter)

    executor.execute("read_file", {"path": "src/a.py"}, provider_tool_call_id="call/1")

    assert [event.type for event in store.read_events()] == [
        "tool_call",
        "policy_decision",
        "tool_result",
    ]
    assert [event.sequence for event in rendered] == [1, 2, 3]
    assert len({event.event_id for event in rendered}) == 3
    assert {event.correlation_id for event in rendered} == {"correlation-0"}


def test_injected_clock_and_id_factory_create_unique_canonical_events(tmp_path) -> None:
    store = SessionStore(
        tmp_path / "session.jsonl",
        session_id="session-1",
        id_factory=lambda kind: "event-fixed",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        fsync=False,
    )
    first = store.append(PendingEvent("session-1", "agent-1", "session_start"))
    second = store.append(PendingEvent("session-1", "agent-1", "agent_start"))

    assert first.event_id == "event-fixed"
    assert second.event_id == "event-fixed_1"
    assert first.timestamp == second.timestamp == "2026-08-29T00:00:00.000000Z"


def test_subscriber_failure_is_aggregated_after_other_subscribers_run(tmp_path) -> None:
    class FailingRenderer:
        def publish(self, event) -> None:
            raise RuntimeError("private renderer detail")

    rendered = []
    emitter = EventEmitter(
        SessionStore(tmp_path / "session.jsonl", session_id="session-1", fsync=False),
        [FailingRenderer(), rendered],
    )

    with pytest.raises(EventDispatchError) as caught:
        emitter.emit(
            session_id="session-1",
            agent_id="agent-1",
            type="user_message",
            payload={"text": "hello"},
        )

    report = caught.value.report
    assert report.event is not None
    assert [delivery.status for delivery in report.deliveries] == [
        DeliveryStatus.SUCCESS,
        DeliveryStatus.FAILED,
        DeliveryStatus.SUCCESS,
    ]
    assert report.deliveries[1].error_type == "RuntimeError"
    assert len(rendered) == 1
    assert emitter.last_report == report
    assert "private renderer detail" not in str(caught.value)


def test_store_failure_marks_later_observers_skipped_instead_of_successful(tmp_path) -> None:
    class FailingStore:
        def append(self, event):
            raise OSError("private path")

    rendered = []
    emitter = EventEmitter(FailingStore(), [rendered])

    with pytest.raises(EventDispatchError) as caught:
        emitter.emit(
            session_id="session-1",
            agent_id="agent-1",
            type="user_message",
        )

    assert [delivery.status for delivery in caught.value.report.deliveries] == [
        DeliveryStatus.FAILED,
        DeliveryStatus.SKIPPED,
    ]
    assert rendered == []
