"""Budget, protocol, interruption, and abnormal Agent Loop termination."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.events import EventEmitter
from coding_agent_neo.models import (
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
)
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker, LimitReason
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry


class AllowPolicy:
    def decide(self, *_args: Any, **_kwargs: Any) -> str:
        return "allow"


@dataclass(slots=True)
class Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


class RepeatingModel:
    def __init__(self, response: NormalizedAssistantResponse) -> None:
        self.response = response
        self.calls = 0

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        self.calls += 1
        return self.response


class ScriptedModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def tool_call(call_id: str, arguments: str = '{"path":"a.py"}') -> NormalizedToolCall:
    return NormalizedToolCall(
        provider_tool_call_id=call_id,
        name="read_file",
        raw_arguments=arguments,
        arguments_valid=arguments.startswith("{"),
        diagnostics=() if arguments.startswith("{") else ("invalid_arguments",),
    )


def loop_for(tmp_path, model, budget, *, environment=None, suffix="limit"):
    registry = default_tool_registry(active_tools=("read_file",))
    selected_environment = environment or FakeExecutionEnvironment()
    runtime = AgentRuntime(
        f"agent-{suffix}",
        f"session-{suffix}",
        selected_environment,
        AllowPolicy(),
        active_tools=set(registry.active_names),
        budget=budget,
    )
    store = SessionStore(
        tmp_path / f"{suffix}.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt="bounded",
    )
    return loop, runtime, store, selected_environment


def assert_every_declared_call_has_one_result(store: SessionStore) -> None:
    events = store.read_events()
    declared = [
        call["correlation_id"]
        for event in events
        if event.type == "assistant_message"
        for call in event.payload["tool_calls"]
    ]
    results = [str(event.correlation_id) for event in events if event.type == "tool_result"]
    assert Counter(results) == Counter(declared)
    assert all(count == 1 for count in Counter(results).values())
    for correlation_id in declared:
        assert [event.type for event in events if str(event.correlation_id) == correlation_id] == [
            "tool_call",
            "policy_decision",
            "tool_result",
        ]


def test_model_step_limit_stops_a_repeating_model(tmp_path) -> None:
    model = RepeatingModel(NormalizedAssistantResponse(tool_calls=(tool_call("provider-repeat"),)))
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=2, max_tool_calls=10, max_protocol_errors=3),
        suffix="steps",
    )

    result = loop.run_turn("repeat")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.MODEL_STEPS
    assert result.reason == "limit_reached:model_steps"
    assert model.calls == 2
    assert runtime.budget.model_steps == 2
    assert runtime.budget.tool_calls == 2
    assert environment.closed
    assert store.read_events()[-1].payload["state"] == "LIMIT_REACHED"


def test_tool_call_limit_stops_before_the_next_declared_call(tmp_path) -> None:
    model = RepeatingModel(
        NormalizedAssistantResponse(tool_calls=(tool_call("provider-1"), tool_call("provider-2")))
    )
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=5, max_tool_calls=1, max_protocol_errors=3),
        suffix="tools",
    )

    result = loop.run_turn("one call only")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.TOOL_CALLS
    assert runtime.budget.tool_calls == 1
    assert [call.operation for call in environment.calls] == ["read_file"]
    assert_every_declared_call_has_one_result(store)
    results = [event for event in store.read_events() if event.type == "tool_result"]
    assert [event.payload["status"] for event in results] == ["success", "denied"]
    assert results[-1].payload["result"]["metadata"]["executed"] is False
    assert results[-1].payload["result"]["metadata"]["reason"] == "tool_calls"


def test_last_allowed_tool_call_can_be_followed_by_a_final_summary(tmp_path) -> None:
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(tool_calls=(tool_call("provider-only"),)),
            NormalizedAssistantResponse(text="summarized after the final allowed call"),
        ]
    )
    loop, runtime, _, _ = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=3, max_tool_calls=1, max_protocol_errors=2),
        suffix="tool-summary",
    )

    result = loop.run_turn("use one call and summarize")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert runtime.budget.tool_calls == 1
    assert model.calls == 2
    loop.close()


def test_consecutive_protocol_errors_have_a_hard_limit(tmp_path) -> None:
    invalid_calls = (
        tool_call("provider-invalid-1", "not-json"),
        tool_call("provider-invalid-2", "still-not-json"),
        tool_call("provider-invalid-3", "again-not-json"),
    )
    model = RepeatingModel(NormalizedAssistantResponse(tool_calls=invalid_calls))
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=5, max_tool_calls=10, max_protocol_errors=2),
        suffix="protocol",
    )

    result = loop.run_turn("bad protocol")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.PROTOCOL_ERRORS
    assert runtime.budget.protocol_errors == 2
    assert runtime.budget.tool_calls == 2
    assert environment.calls == []
    result_events = [event for event in store.read_events() if event.type == "tool_result"]
    assert [event.payload["status"] for event in result_events] == [
        "invalid",
        "invalid",
        "denied",
    ]
    assert result_events[-1].payload["result"]["metadata"]["executed"] is False
    assert_every_declared_call_has_one_result(store)


def test_missing_and_duplicate_provider_ids_are_protocol_errors(tmp_path) -> None:
    calls = (
        NormalizedToolCall(
            provider_tool_call_id="duplicate",
            name="read_file",
            raw_arguments='{"path":"first.py"}',
            arguments_valid=True,
        ),
        NormalizedToolCall(
            provider_tool_call_id="duplicate",
            name="read_file",
            raw_arguments='{"path":"second.py"}',
            arguments_valid=True,
            diagnostics=("duplicate_tool_call_id",),
        ),
        NormalizedToolCall(
            provider_tool_call_id=None,
            name="read_file",
            raw_arguments='{"path":"third.py"}',
            arguments_valid=True,
            diagnostics=("missing_tool_call_id",),
        ),
    )
    model = RepeatingModel(NormalizedAssistantResponse(tool_calls=calls))
    loop, runtime, _, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=5, max_tool_calls=10, max_protocol_errors=2),
        suffix="provider-ids",
    )

    result = loop.run_turn("validate provider IDs")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.PROTOCOL_ERRORS
    assert runtime.budget.protocol_errors == 2
    assert runtime.budget.tool_calls == 3
    assert [call.operation for call in environment.calls] == ["read_file"]


def test_valid_protocol_call_resets_the_consecutive_error_counter(tmp_path) -> None:
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(tool_calls=(tool_call("bad-1", "bad-json"),)),
            NormalizedAssistantResponse(tool_calls=(tool_call("valid"),)),
            NormalizedAssistantResponse(tool_calls=(tool_call("bad-2", "bad-json"),)),
            NormalizedAssistantResponse(text="recovered"),
        ]
    )
    loop, runtime, _, _ = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=6, max_tool_calls=6, max_protocol_errors=2),
        suffix="protocol-reset",
    )

    result = loop.run_turn("recover")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert runtime.budget.protocol_errors == 0
    loop.close()


def test_empty_assistant_responses_are_model_visible_and_bounded(tmp_path) -> None:
    model = RepeatingModel(NormalizedAssistantResponse(diagnostics=("response_missing",)))
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=10, max_tool_calls=10, max_protocol_errors=2),
        suffix="empty",
    )

    result = loop.run_turn("do not return empty")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.PROTOCOL_ERRORS
    assert model.calls == 2
    assert runtime.context_state.recent_messages[-1]["role"] == "system"
    assert environment.closed
    assert [event.type for event in store.read_events()].count("error") == 2


def test_wall_clock_limit_is_checked_after_a_slow_model_step(tmp_path) -> None:
    clock = Clock()

    class SlowModel:
        calls = 0

        def complete(self, messages, tools, parameters=None):
            self.calls += 1
            clock.value = 3.0
            return NormalizedAssistantResponse(
                text="arrived too late",
                tool_calls=(tool_call("wall-1"), tool_call("wall-2")),
            )

    budget = BudgetTracker(
        max_steps=5,
        max_tool_calls=5,
        max_protocol_errors=2,
        max_wall_seconds=2,
        clock=clock,
    )
    loop, _, store, environment = loop_for(
        tmp_path,
        SlowModel(),
        budget,
        suffix="wall",
    )

    result = loop.run_turn("time bound")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.WALL_TIME
    assert result.assistant_text == "arrived too late"
    assert environment.closed
    assert environment.calls == []
    assert_every_declared_call_has_one_result(store)
    wall_results = [event for event in store.read_events() if event.type == "tool_result"]
    assert [event.payload["status"] for event in wall_results] == ["denied", "denied"]
    assert all(
        event.payload["result"]["metadata"]["reason"] == "wall_time" for event in wall_results
    )


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        (RuntimeError("model exploded"), RuntimeState.FAILED),
        (KeyboardInterrupt(), RuntimeState.INTERRUPTED),
    ],
)
def test_abnormal_model_end_is_recorded_and_environment_closed(
    tmp_path, failure, expected_state
) -> None:
    model = ScriptedModel([failure])
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=3, max_tool_calls=3, max_protocol_errors=2),
        suffix=expected_state.value.lower(),
    )

    result = loop.run_turn("fail safely")

    assert result.state is expected_state
    assert environment.closed
    events = store.read_events()
    assert [event.type for event in events][-3:] == [
        "turn_end",
        "agent_end",
        "session_end",
    ]
    if expected_state is RuntimeState.FAILED:
        error = next(event for event in events if event.type == "error")
        assert error.payload["error_type"] == "RuntimeError"
        assert error.payload["message"] == "unhandled system exception"
        assert error.payload["stack"]
        assert "model exploded" not in str(error.payload)
    else:
        assert runtime.cancellation.is_cancelled


def test_cooperative_cancellation_interrupts_before_model_call(tmp_path) -> None:
    model = RepeatingModel(NormalizedAssistantResponse(text="must not run"))
    loop, runtime, store, environment = loop_for(
        tmp_path,
        model,
        BudgetTracker(max_steps=3, max_tool_calls=3, max_protocol_errors=2),
        suffix="cancel",
    )
    runtime.cancellation.cancel("user_cancelled")

    result = loop.run_turn("cancel now")

    assert result.state is RuntimeState.INTERRUPTED
    assert result.reason == "user_cancelled"
    assert model.calls == 0
    assert environment.closed
    assert store.read_events()[-1].payload["state"] == "INTERRUPTED"
