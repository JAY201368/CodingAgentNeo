"""T05 end-to-end lifecycle checks with the T02 fake environment."""

from __future__ import annotations

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.executor import EventRecorder, ToolExecutor
from coding_agent_neo.models import ToolCall, ToolResultStatus
from coding_agent_neo.policy import DefaultExecutionPolicy
from coding_agent_neo.runtime import AgentRuntime
from coding_agent_neo.tools import default_tool_registry


def test_calls_get_fresh_internal_correlations_with_independent_provider_ids() -> None:
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime("agent-1", "session-1", environment, DefaultExecutionPolicy())
    events = EventRecorder()
    ids = iter(("corr-one", "corr-two"))
    executor = ToolExecutor(
        runtime,
        default_tool_registry(),
        event_publisher=events,
        id_factory=lambda kind: next(ids),
    )
    first = executor.execute(
        ToolCall(
            correlation_id="model-placeholder",
            provider_tool_call_id="provider-1",
            name="read_file",
            raw_arguments='{"path":"a.py"}',
        )
    )
    second = executor.execute(
        ToolCall(
            correlation_id="model-placeholder-2",
            provider_tool_call_id="provider-2",
            name="read_file",
            raw_arguments='{"path":"b.py"}',
        )
    )
    assert first.status is ToolResultStatus.SUCCESS
    assert second.status is ToolResultStatus.SUCCESS
    assert first.correlation_id == "corr-one"
    assert second.correlation_id == "corr-two"
    assert first.correlation_id != second.correlation_id
    assert first.provider_tool_call_id == "provider-1"
    assert second.provider_tool_call_id == "provider-2"
    for correlation in (first.correlation_id, second.correlation_id):
        assert [event.type for event in events.events if event.correlation_id == correlation] == [
            "tool_call",
            "policy_decision",
            "tool_result",
        ]


def test_path_denial_is_side_effect_free_and_safe_to_serialize() -> None:
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime("agent-1", "session-1", environment, DefaultExecutionPolicy())
    events = EventRecorder()
    executor = ToolExecutor(runtime, default_tool_registry(), event_publisher=events)
    result = executor.execute(
        "write_file",
        {"path": "../outside.txt", "content": "must not be written"},
        provider_tool_call_id="opaque/provider id",
    )
    assert result.status is ToolResultStatus.DENIED
    assert environment.calls == []
    assert all(event.agent_id == "agent-1" for event in events.events)
    assert all(event.correlation_id == result.correlation_id for event in events.events)
    assert all(event.provider_tool_call_id == "opaque/provider id" for event in events.events)
