"""T05 executor lifecycle and fail-closed tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.executor import (
    EventRecorder,
    ToolEventPublicationError,
    ToolExecutor,
)
from coding_agent_neo.models import ToolResult, ToolResultStatus
from coding_agent_neo.policy import CallbackApprovalPort, DefaultExecutionPolicy
from coding_agent_neo.runtime import AgentRuntime
from coding_agent_neo.tools import default_tool_registry
from coding_agent_neo.tools.schema import ProtocolErrorCode, ToolProtocolError


def _executor(
    environment: FakeExecutionEnvironment,
    *,
    policy=None,
    approval_port=None,
    interactive: bool | None = None,
):
    runtime = AgentRuntime(
        "agent-1",
        "session-1",
        environment,
        policy or DefaultExecutionPolicy(),
    )
    events = EventRecorder()
    return ToolExecutor(
        runtime,
        default_tool_registry(),
        event_publisher=events,
        approval_port=approval_port,
        interactive=interactive,
        id_factory=lambda kind: f"{kind}-test-{len(events.events)}",
    ), events


def test_allow_emits_one_event_of_each_type_and_preserves_provider_id() -> None:
    environment = FakeExecutionEnvironment()
    executor, events = _executor(environment)
    result = executor.execute(
        "read_file", {"path": "src/a.py"}, provider_tool_call_id="vendor/call=1"
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert len(environment.calls) == 1
    assert [event.type for event in events.events] == [
        "tool_call",
        "policy_decision",
        "tool_result",
    ]
    assert len({event.correlation_id for event in events.events}) == 1
    assert {event.provider_tool_call_id for event in events.events} == {"vendor/call=1"}
    assert result.correlation_id != result.provider_tool_call_id


def test_interactive_approval_and_rejection_have_policy_events() -> None:
    environment = FakeExecutionEnvironment()
    approving, approved_events = _executor(
        environment,
        approval_port=CallbackApprovalPort(lambda request: True),
        interactive=True,
    )
    approved = approving.execute("bash", {"command": "pytest"}, provider_tool_call_id="p1")
    assert approved.status is ToolResultStatus.SUCCESS
    assert approved_events.events[1].payload["decision"] == "allow"

    rejecting, rejected_events = _executor(
        environment,
        approval_port=CallbackApprovalPort(lambda request: False),
        interactive=True,
    )
    rejected = rejecting.execute("bash", {"command": "pytest"}, provider_tool_call_id="p2")
    assert rejected.status is ToolResultStatus.DENIED
    assert rejected_events.events[1].payload["decision"] == "deny"
    assert rejected_events.events[1].payload["reason"] == "user_rejected"
    assert len(environment.calls) == 1


def test_noninteractive_ask_rejects_without_invoking_approval_or_environment() -> None:
    environment = FakeExecutionEnvironment()
    invoked: list[str] = []
    executor, events = _executor(
        environment,
        approval_port=CallbackApprovalPort(lambda request: invoked.append("called") or True),
        interactive=False,
    )
    result = executor.execute("bash", {"command": "pytest"})
    assert result.status is ToolResultStatus.DENIED
    assert invoked == []
    assert environment.calls == []
    assert events.events[1].payload["reason"] == "non_interactive_approval_required"


def test_policy_exception_is_denied_and_environment_is_not_called() -> None:
    class ExplodingPolicy:
        def decide(self, tool_name, arguments, context=None):
            raise RuntimeError("secret argument should not escape")

    environment = FakeExecutionEnvironment()
    executor, events = _executor(environment, policy=ExplodingPolicy())
    result = executor.execute("read_file", {"path": "src/a.py"})
    assert result.status is ToolResultStatus.DENIED
    assert result.metadata["error_code"] == "policy_error"
    assert environment.calls == []
    assert events.events[1].payload["decision"] == "deny"


def test_unexpected_environment_exception_does_not_escape_into_result_or_event() -> None:
    class RaisingEnvironment(FakeExecutionEnvironment):
        def read_file(self, request, cancellation):
            self.calls.append((request, cancellation))
            raise RuntimeError("private secret")

    environment = RaisingEnvironment()
    executor, events = _executor(
        environment,
        policy=DefaultExecutionPolicy(),
    )
    result = executor.execute("read_file", {"path": "src/a.py"})
    assert result.status is ToolResultStatus.ERROR
    assert result.text == "tool execution failed"
    assert "private secret" not in str(events.events[-1].to_dict())


def test_sensitive_metadata_is_redacted_at_the_executor_boundary() -> None:
    from coding_agent_neo.models import FileResult

    class SensitiveEnvironment(FakeExecutionEnvironment):
        def read_file(self, request, cancellation):
            self.calls.append((request, cancellation))
            return FileResult(
                path=request.path,
                content="ok",
                metadata={"api_key": "secret-value", "source": "fake"},
            )

    environment = SensitiveEnvironment()
    executor, events = _executor(environment)
    result = executor.execute("read_file", {"path": "src/a.py"})
    assert result.metadata["api_key"] == "<redacted>"
    assert "secret-value" not in str(events.events[-1].to_dict())


def test_unknown_inactive_invalid_and_unexpected_tool_failures_have_one_result_event() -> None:
    environment = FakeExecutionEnvironment()
    executor, events = _executor(environment)
    for name, arguments in (
        ("missing", {}),
        ("read_file", {}),
        ("read_file", '{"path":'),
    ):
        result = executor.execute(name, arguments)
        assert isinstance(result, ToolResult)
        assert sum(event.type == "tool_result" for event in events.events) == (
            len([event for event in events.events if event.type == "tool_call"])
        )
    executor.registry.deactivate("read_file")
    inactive = executor.execute("read_file", {"path": "src/a.py"})
    assert inactive.status is ToolResultStatus.INVALID
    assert environment.calls == []

    @dataclass
    class RaisingTool:
        name: str = "explode"
        description: str = "raise"
        parameters: dict = None

        def __post_init__(self):
            self.parameters = {"type": "object", "properties": {}}

        def validate(self, arguments):
            return {}

        def execute(self, arguments, context):
            raise RuntimeError("private secret")

    executor.registry.register(RaisingTool(), active=True)

    class AllowPolicy:
        def decide(self, tool_name, arguments, context=None):
            return "allow"

    executor.policy = AllowPolicy()
    failed = executor.execute("explode", {})
    assert failed.status is ToolResultStatus.ERROR
    assert "private secret" not in failed.text
    assert environment.calls == []


def test_untrusted_names_argument_keys_and_validator_paths_do_not_leak() -> None:
    sentinel = "SECRET-SENTINEL-8472"
    environment = FakeExecutionEnvironment()
    executor, events = _executor(environment)

    unknown = executor.execute(sentinel, {sentinel: "value"})
    assert sentinel not in repr(unknown)
    assert events.events[0].payload["tool_name"] == "<invalid-tool-name>"
    assert events.events[0].payload["argument_keys"] == ["<redacted>"]

    class RaisingValidator:
        name = "validate_arguments"
        description = "raises a protocol diagnostic"
        parameters = {"type": "object", "properties": {}}

        def validate(self, arguments):
            raise ToolProtocolError(
                ProtocolErrorCode.INVALID_VALUE,
                "invalid",
                path=f"$.{sentinel}",
            )

        def execute(self, arguments, context):
            raise AssertionError("validation should stop execution")

    executor.registry.register(RaisingValidator(), active=True)
    invalid = executor.execute("validate_arguments", {sentinel: "value"})
    assert sentinel not in repr(invalid)

    serialized_events = json.dumps(
        [event.to_dict() for event in events.events],
        sort_keys=True,
    )
    assert sentinel not in serialized_events


def test_event_ids_use_runtime_factory_and_remain_unique() -> None:
    environment = FakeExecutionEnvironment()
    factory_calls: list[str] = []

    def id_factory(kind: str) -> str:
        factory_calls.append(kind)
        return f"{kind}-injected"

    runtime = AgentRuntime(
        "agent-1",
        "session-1",
        environment,
        DefaultExecutionPolicy(),
        id_factory=id_factory,
    )
    events = EventRecorder()
    executor = ToolExecutor(runtime, default_tool_registry(), event_publisher=events)

    result = executor.execute("read_file", {"path": "src/a.py"})

    assert result.status is ToolResultStatus.SUCCESS
    event_ids = [str(event.event_id) for event in events.events]
    assert event_ids == ["event-injected", "event-injected_1", "event-injected_2"]
    assert len(event_ids) == len(set(event_ids))
    assert factory_calls == ["correlation", "event", "event", "event"]


def test_event_failure_is_backward_compatible_by_default_but_strict_when_requested() -> None:
    class FailingPublisher:
        calls = 0

        def publish(self, event) -> None:
            del event
            self.calls += 1
            raise OSError("PRIVATE-PUBLISHER-DETAIL")

    default_environment = FakeExecutionEnvironment()
    default_runtime = AgentRuntime(
        "agent-default-events",
        "session-default-events",
        default_environment,
        DefaultExecutionPolicy(),
    )
    default_publisher = FailingPublisher()
    default_executor = ToolExecutor(
        default_runtime,
        default_tool_registry(),
        event_publisher=default_publisher,
    )

    result = default_executor.execute("read_file", {"path": "a.py"})

    assert result.status is ToolResultStatus.SUCCESS
    assert [call.operation for call in default_environment.calls] == ["read_file"]
    assert default_executor.event_errors == ["OSError", "OSError", "OSError"]

    strict_environment = FakeExecutionEnvironment()
    strict_runtime = AgentRuntime(
        "agent-strict-events",
        "session-strict-events",
        strict_environment,
        DefaultExecutionPolicy(),
    )
    strict_publisher = FailingPublisher()
    strict_executor = ToolExecutor(
        strict_runtime,
        default_tool_registry(),
        event_publisher=strict_publisher,
        strict_event_publishing=True,
    )

    with pytest.raises(ToolEventPublicationError) as caught:
        strict_executor.execute("read_file", {"path": "a.py"})

    assert caught.value.error_type == "OSError"
    assert "PRIVATE-PUBLISHER-DETAIL" not in str(caught.value)
    assert strict_environment.calls == []
    assert strict_publisher.calls == 1


def test_skip_publishes_complete_lifecycle_without_environment_dispatch() -> None:
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime(
        "agent-skip",
        "session-skip",
        environment,
        DefaultExecutionPolicy(),
    )
    events = EventRecorder()
    executor = ToolExecutor(runtime, default_tool_registry(), event_publisher=events)

    result = executor.skip(
        "read_file",
        {"path": "a.py"},
        reason="tool_calls",
        correlation_id="correlation-skip",
        provider_tool_call_id="provider-skip",
    )

    assert result.status is ToolResultStatus.DENIED
    assert result.metadata["executed"] is False
    assert environment.calls == []
    assert [event.type for event in events.events] == [
        "tool_call",
        "policy_decision",
        "tool_result",
    ]
    assert {event.correlation_id for event in events.events} == {"correlation-skip"}
