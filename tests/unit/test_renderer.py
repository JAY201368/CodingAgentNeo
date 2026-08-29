from __future__ import annotations

from io import StringIO

from coding_agent_neo.models import EventEnvelope, EventType
from coding_agent_neo.renderer import TerminalRenderer


def event(sequence: int, event_type: EventType, payload: dict) -> EventEnvelope:
    return EventEnvelope.create(
        session_id="session_test",
        agent_id="agent_test",
        sequence=sequence,
        type=event_type,
        payload=payload,
    )


def test_renderer_shows_core_facts_and_counts() -> None:
    output = StringIO()
    renderer = TerminalRenderer(output, output_limit=256)
    renderer.publish(
        event(
            1,
            EventType.ASSISTANT_MESSAGE,
            {
                "text": "working",
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "tool_calls": [{"name": "bash", "raw_arguments": '{"command":"pytest"}'}],
            },
        )
    )
    renderer.publish(
        event(
            2,
            EventType.POLICY_DECISION,
            {"requested": "ask", "decision": "allow", "approved": True},
        )
    )
    renderer.publish(
        event(
            3,
            EventType.TOOL_RESULT,
            {
                "result": {
                    "status": "success",
                    "text": "ok",
                    "exit_code": 0,
                    "duration_seconds": 0.125,
                    "truncated": True,
                    "original_length": 9000,
                }
            },
        )
    )
    renderer.publish(
        event(
            4,
            EventType.TURN_END,
            {
                "state": "LIMIT_REACHED",
                "reason": "limit_reached:model_steps",
                "limit_reason": "model_steps",
                "budget": {"model_steps": 2, "tool_calls": 1},
            },
        )
    )

    rendered = output.getvalue()
    assert "assistant> working" in rendered
    assert "pytest" in rendered
    assert "approved=true" in rendered
    assert "exit_code=0" in rendered
    assert "duration=0.125s" in rendered
    assert "truncated=true original_length=9000" in rendered
    assert "LIMIT_REACHED" in rendered and "steps=2 tools=1" in rendered
    assert renderer.stats.model_calls == 1

    renderer.publish(
        event(
            5,
            EventType.RETRY,
            {"reason": "rate_limit", "attempt": 1, "max_attempts": 3},
        )
    )
    renderer.publish(
        event(
            6,
            EventType.COMPACTION,
            {"status": "success", "forced": False, "covered_through_sequence": 3},
        )
    )
    renderer.publish(
        event(
            7,
            EventType.SESSION_END,
            {
                "state": "LIMIT_REACHED",
                "reason": "limit_reached:model_steps",
                "budget": {
                    "model_steps": 2,
                    "tool_calls": 1,
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "elapsed_seconds": 1.25,
                },
            },
        )
    )
    rendered = output.getvalue()
    assert "retry> reason=rate_limit attempt=1/3" in rendered
    assert "compaction> status=success" in rendered
    assert "elapsed=1.250s" in rendered


def test_renderer_bounds_large_output() -> None:
    output = StringIO()
    renderer = TerminalRenderer(output, output_limit=128)
    renderer.publish(
        event(
            1,
            EventType.TOOL_RESULT,
            {"result": {"status": "success", "text": "x" * 1000}},
        )
    )
    assert "terminal truncated" in output.getvalue()
    assert len(output.getvalue()) < 300
