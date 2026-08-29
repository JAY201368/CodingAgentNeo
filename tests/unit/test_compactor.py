"""Incremental compactor request and bounded failure behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.compactor import COMPACTION_INSTRUCTION, Compactor
from coding_agent_neo.context import CompactionPlan, group_messages
from coding_agent_neo.models import NormalizedAssistantResponse, NormalizedUsage
from coding_agent_neo.runtime import AgentRuntime, ContextState


@dataclass(slots=True)
class RecordingModel:
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


def make_runtime(*, summary: str = "previous summary", covered: int = 4) -> AgentRuntime:
    return AgentRuntime(
        "agent-compact",
        "session-compact",
        FakeExecutionEnvironment(),
        object(),
        context_state=ContextState(
            latest_summary=summary,
            covered_through_sequence=covered,
        ),
    )


def summary_text() -> str:
    return """Task
Fix the parser.
Constraints
Keep the public API stable.
Decisions
Use the existing decoder.
Files read or modified
src/parser.py
Tests and results
pytest passed.
Open items and next steps
Run integration tests.
"""


def test_compactor_sends_old_summary_and_complete_history_without_tools() -> None:
    groups = group_messages(
        [
            {"role": "user", "content": "earlier task"},
            {"role": "assistant", "content": "decision and file notes"},
        ],
        [5, 6],
    )
    plan = CompactionPlan(
        old_summary="previous summary",
        groups=groups,
        previous_covered_sequence=4,
    )
    runtime = make_runtime()
    model = RecordingModel(
        [
            NormalizedAssistantResponse(
                text=summary_text(),
                usage=NormalizedUsage(input_tokens=40, output_tokens=20),
            )
        ]
    )
    compactor = Compactor(
        model,
        system_prompt="EXPLICIT-READ-ONLY-PROMPT",
        context_window=2_000,
        reserved_output_tokens=200,
        model_parameters={"temperature": 0},
    )

    outcome = compactor.compact(runtime, plan)

    assert outcome.succeeded
    assert outcome.covered_through_sequence == 6
    assert runtime.context_state.covered_through_sequence == 6
    assert runtime.context_state.latest_summary == summary_text().strip()
    assert outcome.usage == NormalizedUsage(input_tokens=40, output_tokens=20)
    assert len(model.requests) == 1
    request = model.requests[0]
    assert request["tools"] == []
    assert request["messages"][0] == {
        "role": "system",
        "content": "EXPLICIT-READ-ONLY-PROMPT",
    }
    assert request["messages"][1]["content"] == COMPACTION_INSTRUCTION
    source = json.loads(request["messages"][2]["content"])
    assert source["previous_summary"] == "previous summary"
    assert [item["end_sequence"] for item in source["history_groups"]] == [5, 6]
    assert request["parameters"] == {"temperature": 0}


def test_plain_summary_is_honestly_wrapped_with_all_required_sections() -> None:
    groups = group_messages([{"role": "user", "content": "task facts"}], [1])
    runtime = make_runtime(summary="", covered=0)
    model = RecordingModel([NormalizedAssistantResponse(text="Known task facts only.")])
    compactor = Compactor(
        model,
        system_prompt="system",
        context_window=1_000,
        reserved_output_tokens=100,
    )

    outcome = compactor.compact(
        runtime,
        CompactionPlan(old_summary=None, groups=groups),
    )

    assert outcome.succeeded
    for heading in (
        "Task",
        "Constraints",
        "Decisions",
        "Files read or modified",
        "Tests and results",
        "Open items and next steps",
    ):
        assert heading in (outcome.summary or "")
    assert "Not separately identified" in (outcome.summary or "")


def test_failure_is_attempted_once_and_does_not_advance_runtime_summary() -> None:
    groups = group_messages([{"role": "user", "content": "old facts"}], [5])
    runtime = make_runtime()
    model = RecordingModel([RuntimeError("PRIVATE PROVIDER DETAIL")])
    compactor = Compactor(
        model,
        system_prompt="system",
        context_window=1_000,
        reserved_output_tokens=100,
    )

    outcome = compactor.compact(
        runtime,
        CompactionPlan(
            old_summary="previous summary",
            groups=groups,
            previous_covered_sequence=4,
        ),
    )

    assert not outcome.succeeded
    assert outcome.reason == "compaction_model_failed"
    assert outcome.error_type == "RuntimeError"
    assert len(model.requests) == 1
    assert runtime.context_state.latest_summary == "previous summary"
    assert runtime.context_state.covered_through_sequence == 4
    assert "PRIVATE" not in str(outcome)


def test_oversized_complete_group_fails_before_api_without_partial_truncation() -> None:
    groups = group_messages([{"role": "user", "content": "x" * 10_000}], [5])
    runtime = make_runtime()
    model = RecordingModel([])
    compactor = Compactor(
        model,
        system_prompt="system",
        context_window=300,
        reserved_output_tokens=50,
    )

    outcome = compactor.compact(
        runtime,
        CompactionPlan(
            old_summary="previous summary",
            groups=groups,
            previous_covered_sequence=4,
        ),
    )

    assert not outcome.succeeded
    assert outcome.reason == "compaction_source_too_large"
    assert model.requests == []
    assert outcome.source_start_sequence == outcome.source_end_sequence == 5
