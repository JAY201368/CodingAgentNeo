"""T08 scripted end-to-end Agent Loop checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.events import EventEmitter
from coding_agent_neo.executor import ToolEventPublicationError
from coding_agent_neo.models import (
    CommandResult,
    EnvironmentStatus,
    FileResult,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    NormalizedUsage,
    RuntimeState,
    SearchMatch,
    SearchResult,
    ToolResult,
)
from coding_agent_neo.policy import CallbackApprovalPort, DefaultExecutionPolicy
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker, ToolExecutionContext
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry
from coding_agent_neo.tools.schema import validate_arguments


class AllowPolicy:
    def decide(self, *_args: Any, **_kwargs: Any) -> str:
        return "allow"


@dataclass(slots=True)
class ScriptedModel:
    responses: list[NormalizedAssistantResponse | BaseException]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or ()),
                "parameters": dict(parameters or {}),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@dataclass(slots=True)
class SequencedEnvironment(FakeExecutionEnvironment):
    edit_responses: list[FileResult] = field(default_factory=list)

    def edit_file(self, request, cancellation) -> FileResult:
        self.calls.append(type("Call", (), {"operation": "edit_file", "request": request})())
        if self.edit_responses:
            return self.edit_responses.pop(0)
        return FileResult(path=request.path, content=request.new_text)


class InspectTool:
    name = "inspect_state"
    description = "Inspect explicitly supplied state through a fake tool."
    parameters = {
        "type": "object",
        "properties": {"label": {"type": "string", "minLength": 1}},
        "required": ["label"],
        "additionalProperties": False,
    }

    def __init__(self, order: list[str]) -> None:
        self.order = order

    def validate(self, arguments):
        return validate_arguments(arguments, self.parameters)

    def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        parsed = self.validate(arguments)
        self.order.append(self.name)
        return ToolResult(
            correlation_id=context.correlation_id,
            provider_tool_call_id=context.provider_tool_call_id,
            text=f"custom:{parsed['label']}",
        )


def call(call_id: str, name: str, arguments: str) -> NormalizedToolCall:
    return NormalizedToolCall(
        provider_tool_call_id=call_id,
        name=name,
        raw_arguments=arguments,
        arguments_valid=True,
    )


def make_loop(tmp_path, model, environment, registry, *, budget=None):
    runtime = AgentRuntime(
        "agent-loop",
        "session-loop",
        environment,
        AllowPolicy(),
        active_tools=set(registry.active_names),
        budget=budget or BudgetTracker(),
    )
    store = SessionStore(
        tmp_path / "session.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt="You are a test coding agent.",
        model_parameters={"temperature": 0},
    )
    return loop, runtime, store


def test_scripted_loop_uses_builtins_and_injected_tool_in_declared_order(tmp_path) -> None:
    order: list[str] = []
    custom_tool = InspectTool(order)
    registry = default_tool_registry()
    registry.register(custom_tool, active=True)
    environment = SequencedEnvironment(
        responses={
            "read_file": FileResult(path="src/app.py", content="value = 1"),
            "search": SearchResult(
                matches=(SearchMatch(path="src/app.py", line_number=1, text="value = 1"),)
            ),
            "run_command": CommandResult(stdout="1 passed", exit_code=0),
        },
        edit_responses=[
            FileResult(
                status=EnvironmentStatus.ERROR,
                message="old text was not found",
                path="src/app.py",
            ),
            FileResult(path="src/app.py", content="value = 2"),
        ],
    )
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                text="I will inspect first.",
                tool_calls=(
                    call("provider-read", "read_file", '{"path":"src/app.py"}'),
                    call("provider-search", "search", '{"query":"value","path":"src"}'),
                    call("provider-custom", "inspect_state", '{"label":"before-edit"}'),
                ),
                usage=NormalizedUsage(input_tokens=4, output_tokens=3),
                finish_reason="tool_calls",
            ),
            NormalizedAssistantResponse(
                tool_calls=(
                    call(
                        "provider-edit-bad",
                        "edit_file",
                        '{"path":"src/app.py","old_text":"missing","new_text":"value = 2"}',
                    ),
                )
            ),
            NormalizedAssistantResponse(
                tool_calls=(
                    call(
                        "provider-edit-good",
                        "edit_file",
                        '{"path":"src/app.py","old_text":"value = 1","new_text":"value = 2"}',
                    ),
                    call("provider-bash", "bash", '{"command":"pytest -q"}'),
                )
            ),
            NormalizedAssistantResponse(text="Implemented and verified.", finish_reason="stop"),
        ]
    )
    loop, runtime, store = make_loop(tmp_path, model, environment, registry)

    result = loop.run_turn("Fix the value and run tests.")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert result.assistant_text == "Implemented and verified."
    assert not environment.closed
    assert [item.operation for item in environment.calls] == [
        "read_file",
        "search",
        "edit_file",
        "edit_file",
        "run_command",
    ]
    assert order == ["inspect_state"]
    assert runtime.budget.model_steps == 4
    assert runtime.budget.tool_calls == 6
    assert runtime.budget.input_tokens == 4
    assert runtime.budget.output_tokens == 3
    assert runtime.budget.protocol_errors == 0

    # The failed edit remains a normal model-visible tool result and the next
    # scripted response can correct it.
    failed_request = model.requests[2]["messages"]
    failed_tool_message = failed_request[-1]
    assert failed_tool_message["role"] == "tool"
    failed_content = json.loads(failed_tool_message["content"])
    assert failed_content["status"] == "error"
    assert "old text was not found" in failed_content["text"]

    # Every request gets the explicit system prompt and the same generic
    # active schema view, including the injected non-built-in Tool.
    assert all(
        request["messages"][0] == {"role": "system", "content": "You are a test coding agent."}
        for request in model.requests
    )
    schema_names = [schema["function"]["name"] for schema in model.requests[0]["tools"]]
    assert schema_names[-1] == "inspect_state"

    loop.close(reason="test_complete")
    assert environment.closed
    events = store.read_events()
    event_types = [event.type for event in events]
    assert event_types[:4] == [
        "session_start",
        "agent_start",
        "user_message",
        "assistant_message",
    ]
    assert event_types[-3:] == ["turn_end", "agent_end", "session_end"]
    assert len({event.event_id for event in events}) == len(events)
    assert all(event.agent_id == runtime.agent_id for event in events)
    tool_events = [event for event in events if event.type.startswith("tool_")]
    correlations = {event.correlation_id for event in tool_events}
    assert len(correlations) == 6
    for correlation in correlations:
        assert [event.type for event in events if event.correlation_id == correlation] == [
            "tool_call",
            "policy_decision",
            "tool_result",
        ]


def test_command_timeout_is_a_normal_result_the_model_can_handle(tmp_path) -> None:
    registry = default_tool_registry(active_tools=("bash",))
    environment = FakeExecutionEnvironment(
        responses={
            "run_command": CommandResult(
                status=EnvironmentStatus.TIMEOUT,
                message="command timed out",
                timed_out=True,
            )
        }
    )
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                tool_calls=(call("provider-bash", "bash", '{"command":"slow"}'),)
            ),
            NormalizedAssistantResponse(text="The command hit its timeout; no retry needed."),
        ]
    )
    loop, _, _ = make_loop(tmp_path, model, environment, registry)

    result = loop.run_turn("Run the bounded command.")

    assert result.state is RuntimeState.COMPLETED_TURN
    content = json.loads(model.requests[1]["messages"][-1]["content"])
    assert content["status"] == "timeout"
    assert content["timed_out"] is True
    loop.close()


def test_successful_turn_keeps_session_open_for_follow_up(tmp_path) -> None:
    registry = default_tool_registry(active_tools=())
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(text="first"),
            NormalizedAssistantResponse(text="second"),
        ]
    )
    environment = FakeExecutionEnvironment()
    loop, _, store = make_loop(tmp_path, model, environment, registry)

    assert loop.run_turn("one").text == "first"
    assert loop.run_turn("two").text == "second"

    assert not environment.closed
    assert [event.type for event in store.read_events()].count("session_start") == 1
    assert [event.type for event in store.read_events()].count("turn_end") == 2
    loop.close()
    assert [event.type for event in store.read_events()][-2:] == ["agent_end", "session_end"]


def test_interactive_approval_exposes_waiting_state(tmp_path) -> None:
    registry = default_tool_registry(active_tools=("bash",))
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime(
        "agent-approval",
        "session-approval",
        environment,
        DefaultExecutionPolicy(),
        active_tools={"bash"},
        budget=BudgetTracker(max_steps=3, max_tool_calls=2, max_protocol_errors=2),
    )
    store = SessionStore(
        tmp_path / "approval.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )
    observed_states: list[RuntimeState] = []
    holder: dict[str, AgentLoop] = {}

    def approve(_request) -> bool:
        observed_states.append(holder["loop"].state)
        return True

    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                tool_calls=(call("approval-call", "bash", '{"command":"true"}'),)
            ),
            NormalizedAssistantResponse(text="approved"),
        ]
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt="approval",
        approval_port=CallbackApprovalPort(approve),
        interactive=True,
    )
    holder["loop"] = loop

    result = loop.run_turn("approve the command")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert observed_states == [RuntimeState.WAITING_FOR_APPROVAL]
    assert loop.state is RuntimeState.COMPLETED_TURN
    loop.close()


def test_tool_call_store_failure_is_failed_before_environment_side_effect(tmp_path) -> None:
    sentinel = "PRIVATE-STORE-DETAIL-7492"
    registry = default_tool_registry(active_tools=("read_file",))
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime(
        "agent-store-failure",
        "session-store-failure",
        environment,
        AllowPolicy(),
        active_tools={"read_file"},
        budget=BudgetTracker(max_steps=3, max_tool_calls=2, max_protocol_errors=2),
    )
    durable_store = SessionStore(
        tmp_path / "store-failure.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )

    class RejectToolLifecycleStore:
        session_id = runtime.session_id

        def append(self, event):
            if getattr(event, "type", None) in {
                "tool_call",
                "policy_decision",
                "tool_result",
            }:
                raise OSError(sentinel)
            return durable_store.append(event)

    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                tool_calls=(call("store-failure-call", "read_file", '{"path":"a.py"}'),)
            ),
            NormalizedAssistantResponse(text="must not complete"),
        ]
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(RejectToolLifecycleStore()),
        runtime,
        system_prompt="store first",
    )

    result = loop.run_turn("do not bypass persistence")

    assert result.state is RuntimeState.FAILED
    assert result.error_type == ToolEventPublicationError.__name__
    assert len(model.requests) == 1
    assert environment.calls == []
    assert environment.closed
    events = durable_store.read_events()
    assert all(
        event.type not in {"tool_call", "policy_decision", "tool_result"} for event in events
    )
    assert [event.type for event in events][-4:] == [
        "error",
        "turn_end",
        "agent_end",
        "session_end",
    ]
    assert sentinel not in str([event.to_dict() for event in events])


def test_renderer_failure_after_store_success_does_not_stop_tool_execution(tmp_path) -> None:
    registry = default_tool_registry(active_tools=("read_file",))
    environment = FakeExecutionEnvironment()
    runtime = AgentRuntime(
        "agent-renderer-failure",
        "session-renderer-failure",
        environment,
        AllowPolicy(),
        active_tools={"read_file"},
        budget=BudgetTracker(max_steps=3, max_tool_calls=2, max_protocol_errors=2),
    )
    store = SessionStore(
        tmp_path / "renderer-failure.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )

    class FailingRenderer:
        def publish(self, event) -> None:
            del event
            raise RuntimeError("PRIVATE-RENDERER-DETAIL")

    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                tool_calls=(call("renderer-call", "read_file", '{"path":"a.py"}'),)
            ),
            NormalizedAssistantResponse(text="completed from canonical facts"),
        ]
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store, [FailingRenderer()]),
        runtime,
        system_prompt="store first",
    )

    result = loop.run_turn("observer failure is non-fatal")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert [item.operation for item in environment.calls] == ["read_file"]
    assert [
        event.type
        for event in store.read_events()
        if event.type in {"tool_call", "policy_decision", "tool_result"}
    ] == ["tool_call", "policy_decision", "tool_result"]
    loop.close()
