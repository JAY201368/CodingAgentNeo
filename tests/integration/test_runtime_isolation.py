"""Two Agent Loops in one process keep every mutable runtime fact isolated."""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.agent_loop import ActiveToolsMismatchError, AgentLoop
from coding_agent_neo.events import EventEmitter
from coding_agent_neo.models import NormalizedAssistantResponse, NormalizedToolCall, RuntimeState
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry


class AllowPolicy:
    def decide(self, *_args: Any, **_kwargs: Any) -> str:
        return "allow"


class OneToolThenDoneModel:
    def __init__(self, *, tool_name: str, arguments: str, label: str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.label = label
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        self.requests.append([dict(message) for message in messages])
        self.calls += 1
        if self.calls == 1:
            return NormalizedAssistantResponse(
                tool_calls=(
                    NormalizedToolCall(
                        provider_tool_call_id=f"provider-{self.label}",
                        name=self.tool_name,
                        raw_arguments=self.arguments,
                        arguments_valid=True,
                    ),
                )
            )
        return NormalizedAssistantResponse(text=f"done-{self.label}")


def build_loop(tmp_path, *, label, registry, environment, model):
    runtime = AgentRuntime(
        f"agent-{label}",
        f"session-{label}",
        environment,
        AllowPolicy(),
        active_tools=set(registry.active_names),
        budget=BudgetTracker(max_steps=4, max_tool_calls=4, max_protocol_errors=2),
    )
    store = SessionStore(
        tmp_path / f"{label}.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt=f"system-{label}",
    )
    return loop, runtime, store


def test_two_loops_do_not_share_messages_budget_tools_cancel_or_environment(tmp_path) -> None:
    first_registry = default_tool_registry(active_tools=("read_file",))
    second_registry = default_tool_registry(active_tools=("search",))
    first_environment = FakeExecutionEnvironment()
    second_environment = FakeExecutionEnvironment()
    first_model = OneToolThenDoneModel(
        tool_name="read_file",
        arguments='{"path":"first.py"}',
        label="first",
    )
    second_model = OneToolThenDoneModel(
        tool_name="search",
        arguments='{"query":"second"}',
        label="second",
    )
    first_loop, first_runtime, first_store = build_loop(
        tmp_path,
        label="first",
        registry=first_registry,
        environment=first_environment,
        model=first_model,
    )
    second_loop, second_runtime, second_store = build_loop(
        tmp_path,
        label="second",
        registry=second_registry,
        environment=second_environment,
        model=second_model,
    )
    first_runtime.cancellation.cancel("only first")

    first_result = first_loop.run_turn("first task")
    second_result = second_loop.run_turn("second task")

    assert first_result.state is RuntimeState.INTERRUPTED
    assert second_result.state is RuntimeState.COMPLETED_TURN
    assert first_model.calls == 0
    assert second_model.calls == 2
    assert first_runtime.budget.model_steps == 0
    assert second_runtime.budget.model_steps == 2
    assert first_runtime.context_state.recent_messages == [
        {"role": "user", "content": "first task"}
    ]
    assert all("first task" not in str(message) for message in second_model.requests[-1])
    assert all("system-first" not in str(message) for message in second_model.requests[-1])
    assert first_runtime.active_tools == {"read_file"}
    assert second_runtime.active_tools == {"search"}
    assert first_runtime.cancellation.is_cancelled
    assert not second_runtime.cancellation.is_cancelled
    assert first_environment.calls == []
    assert [call.operation for call in second_environment.calls] == ["search"]
    assert first_environment.closed
    assert not second_environment.closed
    assert {event.agent_id for event in first_store.read_events()} == {"agent-first"}
    assert {event.agent_id for event in second_store.read_events()} == {"agent-second"}
    second_loop.close()


def test_active_view_mismatch_fails_at_construction_before_any_side_effect(tmp_path) -> None:
    registry = default_tool_registry(active_tools=("read_file",))
    environment = FakeExecutionEnvironment()

    class RecordingModel:
        calls = 0

        def complete(self, messages, tools, parameters=None):
            self.calls += 1
            return NormalizedAssistantResponse(text="unexpected")

    model = RecordingModel()
    runtime = AgentRuntime(
        "agent-mismatch",
        "session-mismatch",
        environment,
        AllowPolicy(),
        active_tools={"search"},
    )
    session_path = tmp_path / "mismatch.jsonl"
    emitter = EventEmitter(SessionStore(session_path, session_id=runtime.session_id, fsync=False))

    with pytest.raises(ActiveToolsMismatchError, match="active_tools"):
        AgentLoop(
            model,
            registry,
            emitter,
            runtime,
            system_prompt="mismatch",
        )

    assert model.calls == 0
    assert not environment.started
    assert not environment.closed
    assert environment.calls == []
    assert not session_path.exists()
    assert runtime.budget.max_steps is None


def test_active_view_drift_after_construction_fails_before_model_or_tool(tmp_path) -> None:
    registry = default_tool_registry(active_tools=("read_file",))
    environment = FakeExecutionEnvironment()

    class RecordingModel:
        calls = 0

        def complete(self, messages, tools, parameters=None):
            self.calls += 1
            return NormalizedAssistantResponse(text="unexpected")

    model = RecordingModel()
    loop, runtime, store = build_loop(
        tmp_path,
        label="drift",
        registry=registry,
        environment=environment,
        model=model,
    )
    runtime.active_tools.clear()

    result = loop.run_turn("must fail before model")

    assert result.state is RuntimeState.FAILED
    assert result.error_type == "ActiveToolsMismatchError"
    assert model.calls == 0
    assert environment.calls == []
    assert not environment.started
    assert environment.closed is False
    assert [event.type for event in store.read_events()] == [
        "error",
        "turn_end",
        "agent_end",
        "session_end",
    ]
