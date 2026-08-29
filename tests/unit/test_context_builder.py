"""Context budgeting, grouping, and Runtime isolation."""

from __future__ import annotations

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.context import (
    ContextBuilder,
    ContextIntegrityError,
    group_messages,
)
from coding_agent_neo.runtime import AgentRuntime, ContextState


def runtime_with(state: ContextState, *, agent_id: str = "agent-context") -> AgentRuntime:
    return AgentRuntime(
        agent_id,
        "session-context",
        FakeExecutionEnvironment(),
        object(),
        context_state=state,
    )


def tool_assistant(*call_ids: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "calling tools",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "fake", "arguments": "{}"},
            }
            for call_id in call_ids
        ],
    }


def tool_result(call_id: str, content: str = "ok") -> dict[str, str]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_budget_covers_full_prompt_tools_summary_messages_results_and_reserve() -> None:
    system_prompt = "arbitrary read-only assembly text\n" + "policy " * 30
    state = ContextState(
        latest_summary="Earlier task and decision summary",
        covered_through_sequence=2,
    )
    state.append_message({"role": "user", "content": "old"}, sequence=1)
    state.append_message({"role": "assistant", "content": "old answer"}, sequence=2)
    state.append_message(tool_assistant("call-1"), sequence=3)
    state.append_message(tool_result("call-1", "tool output " * 20), sequence=4)
    runtime = runtime_with(state)
    schemas = (
        {
            "type": "function",
            "function": {
                "name": "fake",
                "description": "schema description " * 10,
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )
    builder = ContextBuilder(
        system_prompt,
        context_window=4_000,
        reserved_output_tokens=321,
    )

    projection = builder.project(runtime, schemas)

    assert projection.messages[0] == {"role": "system", "content": system_prompt}
    assert projection.messages[1]["content"].endswith(state.latest_summary or "")
    assert [message["role"] for message in projection.messages[-2:]] == [
        "assistant",
        "tool",
    ]
    assert projection.estimate.reserved_output_tokens == 321
    assert projection.estimate.total_tokens == (
        projection.estimate.input_tokens + projection.estimate.reserved_output_tokens
    )
    assert (
        builder.estimate(projection.messages, schemas).input_tokens
        > builder.estimate(projection.messages, ()).input_tokens
    )
    short_prompt_builder = ContextBuilder(
        "short",
        context_window=4_000,
        reserved_output_tokens=321,
    )
    short_projection = short_prompt_builder.project(runtime, schemas)
    assert projection.estimate.input_tokens > short_projection.estimate.input_tokens


def test_tool_calls_and_all_results_are_one_indivisible_group() -> None:
    messages = [
        {"role": "user", "content": "task"},
        tool_assistant("call-1", "call-2"),
        tool_result("call-1"),
        tool_result("call-2"),
        {"role": "assistant", "content": "finished"},
        {"role": "user", "content": "follow up"},
    ]
    groups = group_messages(messages, [1, 2, 3, 4, 5, 6])

    assert [len(group.messages) for group in groups] == [1, 3, 1, 1]
    assert groups[1].start_sequence == 2
    assert groups[1].end_sequence == 4

    state = ContextState(recent_messages=messages, recent_message_sequences=[1, 2, 3, 4, 5, 6])
    builder = ContextBuilder(
        "system",
        context_window=2_000,
        reserved_output_tokens=100,
        keep_recent_groups=1,
    )
    plan = builder.plan_compaction(runtime_with(state))

    assert plan is not None
    assert any(len(group.messages) == 3 for group in plan.groups)
    assert plan.covered_through_sequence == 5


def test_incomplete_or_orphaned_tool_results_fail_instead_of_splitting() -> None:
    with pytest.raises(ContextIntegrityError, match="missing results"):
        group_messages([tool_assistant("call-1")], [1])

    with pytest.raises(ContextIntegrityError, match="no preceding"):
        group_messages([tool_result("call-1")], [1])


def test_builder_uses_only_the_explicit_runtime_and_never_other_agent_messages() -> None:
    first_state = ContextState()
    first_state.append_message({"role": "user", "content": "FIRST-ONLY"}, sequence=3)
    second_state = ContextState(latest_summary="SECOND-SUMMARY", covered_through_sequence=2)
    second_state.append_message({"role": "user", "content": "SECOND-ONLY"}, sequence=3)
    first = runtime_with(first_state, agent_id="agent-first")
    second = runtime_with(second_state, agent_id="agent-second")
    builder = ContextBuilder("EXPLICIT-SYSTEM", context_window=1_000, reserved_output_tokens=50)

    rendered = str(builder.build(first))

    assert "EXPLICIT-SYSTEM" in rendered
    assert "FIRST-ONLY" in rendered
    assert "SECOND-ONLY" not in rendered
    assert "SECOND-SUMMARY" not in rendered
    assert second.context_state.latest_summary == "SECOND-SUMMARY"


def test_sequence_tracking_stays_aligned_with_legacy_direct_message_appends() -> None:
    state = ContextState(recent_messages=[{"role": "user", "content": "legacy"}])
    state.recent_messages.append({"role": "assistant", "content": "direct"})
    state.append_message({"role": "user", "content": "tracked"}, sequence=9)

    assert state.recent_message_sequences == [None, None, 9]
    groups = ContextBuilder(
        "system",
        context_window=1_000,
        reserved_output_tokens=50,
    ).all_groups(runtime_with(state))
    assert [group.messages[0]["content"] for group in groups] == [
        "legacy",
        "direct",
        "tracked",
    ]


def test_synthetic_sequences_are_stable_after_legacy_history_is_compacted() -> None:
    state = ContextState(
        recent_messages=[
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current"},
        ]
    )
    runtime = runtime_with(state)
    builder = ContextBuilder(
        "system",
        context_window=1_000,
        reserved_output_tokens=50,
        keep_recent_groups=1,
    )

    plan = builder.plan_compaction(runtime)
    assert plan is not None
    state.latest_summary = "summary"
    state.covered_through_sequence = plan.covered_through_sequence

    projected = str(builder.build(runtime))
    assert "old user" not in projected
    assert "old answer" not in projected
    assert "current" in projected
    assert state.recent_message_sequences == [1, 2, 3]


def test_trigger_threshold_is_lower_than_hard_window_and_includes_output_reserve() -> None:
    state = ContextState()
    state.append_message({"role": "user", "content": "x" * 800}, sequence=1)
    builder = ContextBuilder(
        "system",
        context_window=500,
        reserved_output_tokens=100,
        compaction_threshold=0.8,
    )

    estimate = builder.project(runtime_with(state)).estimate

    assert estimate.trigger_tokens == 400
    assert estimate.needs_compaction
    assert estimate.total_tokens <= estimate.context_window
