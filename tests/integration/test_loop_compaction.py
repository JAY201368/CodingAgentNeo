"""Agent Loop integration with threshold and forced context compaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.compactor import COMPACTION_INSTRUCTION
from coding_agent_neo.context import DEGRADED_CONTEXT_NOTICE, ContextBuilder
from coding_agent_neo.events import EventEmitter, PendingEvent
from coding_agent_neo.model_client import ModelClientError, ModelErrorCategory, ModelErrorCode
from coding_agent_neo.models import EventType, NormalizedAssistantResponse, RuntimeState
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker, ContextState, LimitReason
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry

SYSTEM_PROMPT = "EXPLICIT SYSTEM PROMPT\nTreat the supplied repository task as authoritative."


@dataclass(slots=True)
class ScriptedModel:
    responses: list[NormalizedAssistantResponse | BaseException]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools),
                "parameters": dict(parameters or {}),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class AllowPolicy:
    def decide(self, *_args: Any, **_kwargs: Any) -> str:
        return "allow"


def incremental_summary() -> str:
    return """Task
Keep the earlier coding task working.
Constraints
Preserve the explicit prompt and tool grouping.
Decisions
Retain only complete recent interactions.
Files read or modified
src/example.py was inspected.
Tests and results
The earlier unit test passed.
Open items and next steps
Answer the current follow-up.
"""


def append_projected_history(
    store: SessionStore,
    runtime: AgentRuntime,
    *,
    include_other_agent: bool = True,
) -> tuple[str, ...]:
    initial_ids: list[str] = []

    def append(message: dict[str, Any], event_type: EventType) -> None:
        event = store.append(
            PendingEvent(
                session_id=runtime.session_id,
                agent_id=runtime.agent_id,
                type=event_type,
                payload={"fixture": True},
            )
        )
        runtime.context_state.append_message(message, sequence=event.sequence)
        initial_ids.append(str(event.event_id))

    append(
        {"role": "user", "content": "OLD-TASK " * 35},
        EventType.USER_MESSAGE,
    )
    append(
        {
            "role": "assistant",
            "content": "I will inspect the old file.",
            "tool_calls": [
                {
                    "id": "old-call",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"old.py"}'},
                }
            ],
        },
        EventType.ASSISTANT_MESSAGE,
    )
    append(
        {
            "role": "tool",
            "tool_call_id": "old-call",
            "content": "OLD-TOOL-RESULT " * 20,
        },
        EventType.TOOL_RESULT,
    )
    append(
        {"role": "assistant", "content": "OLD-FINAL " * 15},
        EventType.ASSISTANT_MESSAGE,
    )
    if include_other_agent:
        other = store.append(
            PendingEvent(
                session_id=runtime.session_id,
                agent_id="agent-other",
                type=EventType.ASSISTANT_MESSAGE,
                payload={"text": "OTHER-AGENT-INTERNAL-MARKER"},
            )
        )
        initial_ids.append(str(other.event_id))
    return tuple(initial_ids)


def build_loop(
    tmp_path,
    model: ScriptedModel,
    *,
    context_window: int,
    threshold: float,
    prepopulate: bool = True,
) -> tuple[AgentLoop, AgentRuntime, SessionStore, bytes]:
    registry = default_tool_registry(active_tools=())
    runtime = AgentRuntime(
        "agent-root",
        "session-context-loop",
        FakeExecutionEnvironment(),
        AllowPolicy(),
        active_tools=set(),
        budget=BudgetTracker(max_steps=5, max_tool_calls=2, max_protocol_errors=2),
        context_state=ContextState(),
    )
    path = tmp_path / "context-session.jsonl"
    store = SessionStore(path, session_id=runtime.session_id, fsync=False)
    if prepopulate:
        append_projected_history(store, runtime)
    before = path.read_bytes() if path.exists() else b""
    builder = ContextBuilder(
        SYSTEM_PROMPT,
        context_window=context_window,
        reserved_output_tokens=100,
        compaction_threshold=threshold,
        keep_recent_groups=1,
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt=SYSTEM_PROMPT,
        context_builder=builder,
    )
    return loop, runtime, store, before


def test_small_window_compacts_only_current_runtime_and_preserves_jsonl_prefix(tmp_path) -> None:
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(text=incremental_summary()),
            NormalizedAssistantResponse(text="Current task completed."),
        ]
    )
    loop, runtime, store, before = build_loop(
        tmp_path,
        model,
        context_window=1_100,
        threshold=0.5,
    )
    raw_messages_before = list(runtime.context_state.recent_messages)

    result = loop.run_turn("CURRENT-USER-FOLLOW-UP")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert len(model.requests) == 2
    compaction_request, agent_request = model.requests
    assert compaction_request["tools"] == []
    assert compaction_request["messages"][0] == {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }
    assert compaction_request["messages"][1]["content"] == COMPACTION_INSTRUCTION
    assert "old-call" in compaction_request["messages"][2]["content"]
    assert "OLD-TOOL-RESULT" in compaction_request["messages"][2]["content"]
    assert agent_request["messages"][0] == {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }
    assert "Incremental context summary" in str(agent_request["messages"])
    assert "CURRENT-USER-FOLLOW-UP" in str(agent_request["messages"])
    assert "OLD-TOOL-RESULT" not in str(agent_request["messages"])
    assert "OTHER-AGENT-INTERNAL-MARKER" not in str(model.requests)
    assert runtime.context_state.recent_messages[: len(raw_messages_before)] == raw_messages_before
    assert (tmp_path / "context-session.jsonl").read_bytes().startswith(before)

    events = store.read_events()
    compactions = [event for event in events if event.type == EventType.COMPACTION]
    assert len(compactions) == 1
    event = compactions[0]
    assert event.agent_id == runtime.agent_id
    assert event.payload["status"] == "success"
    assert event.payload["covered_through_sequence"] == 4
    assert runtime.context_state.covered_through_sequence == 4
    assert [item.type for item in events[:5]] == [
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_RESULT,
        EventType.ASSISTANT_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    assert events[4].agent_id == "agent-other"
    loop.close()


def test_compaction_failure_degrades_once_and_continues_with_complete_recent_context(
    tmp_path,
) -> None:
    model = ScriptedModel(
        [
            RuntimeError("PRIVATE COMPACTION FAILURE"),
            NormalizedAssistantResponse(text="Completed from bounded recent context."),
        ]
    )
    loop, runtime, store, _ = build_loop(
        tmp_path,
        model,
        context_window=1_100,
        threshold=0.5,
    )

    result = loop.run_turn("CURRENT-AFTER-FAILURE")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert len(model.requests) == 2
    final_messages = model.requests[-1]["messages"]
    assert any(message.get("content") == DEGRADED_CONTEXT_NOTICE for message in final_messages)
    assert "CURRENT-AFTER-FAILURE" in str(final_messages)
    assert "OLD-TOOL-RESULT" not in str(final_messages)
    assert runtime.context_state.covered_through_sequence == 0
    assert runtime.context_state.degraded_through_sequence == 4
    failure_events = [
        event
        for event in store.read_events()
        if event.type == EventType.COMPACTION and event.payload["status"] == "failed"
    ]
    assert len(failure_events) == 1
    assert failure_events[0].payload["degraded_through_sequence"] == 4
    assert "PRIVATE" not in str(failure_events[0].to_dict())
    loop.close()


def test_failed_compaction_that_still_cannot_fit_returns_context_limit(tmp_path) -> None:
    model = ScriptedModel([RuntimeError("PRIVATE COMPACTION FAILURE")])
    loop, runtime, store, _ = build_loop(
        tmp_path,
        model,
        context_window=1_100,
        threshold=0.5,
    )

    result = loop.run_turn("CURRENT-TOO-LARGE " * 300)

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.CONTEXT_WINDOW
    assert len(model.requests) == 1
    assert runtime.environment.closed
    compactions = [event for event in store.read_events() if event.type == EventType.COMPACTION]
    assert len(compactions) == 1
    assert compactions[0].payload["status"] == "failed"
    assert "PRIVATE" not in str(compactions[0].to_dict())


def test_compaction_store_failure_rolls_back_projection_and_fails_closed(tmp_path) -> None:
    model = ScriptedModel([NormalizedAssistantResponse(text=incremental_summary())])
    loop, runtime, durable_store, _ = build_loop(
        tmp_path,
        model,
        context_window=1_100,
        threshold=0.5,
    )

    class RejectCompactionStore:
        session_id = runtime.session_id

        def append(self, event):
            if getattr(event, "type", None) == EventType.COMPACTION:
                raise OSError("PRIVATE STORE FAILURE")
            return durable_store.append(event)

    loop.event_emitter = EventEmitter(RejectCompactionStore())

    result = loop.run_turn("CURRENT-WITH-STORE-FAILURE")

    assert result.state is RuntimeState.FAILED
    assert runtime.context_state.latest_summary is None
    assert runtime.context_state.covered_through_sequence == 0
    assert len(model.requests) == 1
    assert all(event.type != EventType.COMPACTION for event in durable_store.read_events())
    assert "PRIVATE" not in str([event.to_dict() for event in durable_store.read_events()])


def context_overflow() -> ModelClientError:
    return ModelClientError(
        ModelErrorCategory.CONTEXT_OVERFLOW,
        ModelErrorCode.CONTEXT_OVERFLOW,
    )


def test_provider_overflow_forces_at_most_one_compaction_retry(tmp_path) -> None:
    model = ScriptedModel(
        [
            context_overflow(),
            NormalizedAssistantResponse(text=incremental_summary()),
            context_overflow(),
        ]
    )
    loop, _, store, _ = build_loop(
        tmp_path,
        model,
        context_window=4_000,
        threshold=1.0,
    )

    result = loop.run_turn("TRIGGER-PROVIDER-OVERFLOW")

    assert result.state is RuntimeState.FAILED
    assert result.reason == "context_overflow_after_forced_compaction"
    assert len(model.requests) == 3
    assert all(request["messages"][0]["content"] == SYSTEM_PROMPT for request in model.requests)
    compaction_requests = [
        request
        for request in model.requests
        if len(request["messages"]) > 1
        and request["messages"][1].get("content") == COMPACTION_INSTRUCTION
    ]
    assert len(compaction_requests) == 1
    events = store.read_events()
    assert len([event for event in events if event.type == EventType.RETRY]) == 1
    forced = [event for event in events if event.type == EventType.COMPACTION]
    assert len(forced) == 1
    assert forced[0].payload["forced"] is True


def test_unshrinkable_request_stops_before_model_api_with_explicit_limit(tmp_path) -> None:
    model = ScriptedModel([])
    registry = default_tool_registry(active_tools=())
    runtime = AgentRuntime(
        "agent-too-large",
        "session-too-large",
        FakeExecutionEnvironment(),
        AllowPolicy(),
        active_tools=set(),
    )
    store = SessionStore(
        tmp_path / "too-large.jsonl",
        session_id=runtime.session_id,
        fsync=False,
    )
    huge_prompt = "EXPLICIT-HUGE-PROMPT " * 500
    builder = ContextBuilder(
        huge_prompt,
        context_window=300,
        reserved_output_tokens=50,
        compaction_threshold=0.8,
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt=huge_prompt,
        context_builder=builder,
    )

    result = loop.run_turn("cannot fit")

    assert result.state is RuntimeState.LIMIT_REACHED
    assert result.limit_reason is LimitReason.CONTEXT_WINDOW
    assert result.reason == "limit_reached:context_window"
    assert model.requests == []
    assert runtime.environment.closed
