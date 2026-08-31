"""AC-01 scripted programming loop on a small defective fixture repository."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from shlex import quote
from typing import Any

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.events import EventEmitter
from coding_agent_neo.models import (
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
    ToolResult,
)
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker, ToolExecutionContext
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools import default_tool_registry
from coding_agent_neo.tools.schema import validate_arguments

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "buggy_counter"
LOOP_SOURCE = REPO_ROOT / "src" / "coding_agent_neo" / "agent_loop.py"
SYSTEM_PROMPT = "You are a test coding agent. Use only the supplied tools."
FAKE_TOOL_NAME = "record_note"


class AllowPolicy:
    """Test policy that allows every validated call, including injected fake Tools."""

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


class RecordNoteTool:
    """Non-built-in Tool used only to prove Loop source-agnostic dispatch."""

    name = FAKE_TOOL_NAME
    description = "Record an explicit progress note through the generic Tool protocol."
    parameters = {
        "type": "object",
        "properties": {"note": {"type": "string", "minLength": 1}},
        "required": ["note"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.notes: list[str] = []

    def validate(self, arguments: str | Mapping[str, Any]) -> dict[str, Any]:
        return validate_arguments(arguments, self.parameters)

    def execute(
        self, arguments: str | Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        parsed = self.validate(arguments)
        self.notes.append(str(parsed["note"]))
        return ToolResult(
            correlation_id=context.correlation_id,
            provider_tool_call_id=context.provider_tool_call_id,
            text=f"noted:{parsed['note']}",
        )


def _call(call_id: str, name: str, arguments: str) -> NormalizedToolCall:
    return NormalizedToolCall(
        provider_tool_call_id=call_id,
        name=name,
        raw_arguments=arguments,
        arguments_valid=True,
    )


def _copy_fixture(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT, workspace)
    return workspace


def _last_tool_payload(request: dict[str, Any]) -> dict[str, Any]:
    message = request["messages"][-1]
    assert message["role"] == "tool"
    payload = json.loads(message["content"])
    assert isinstance(payload, dict)
    return payload


def test_ac01_injected_fake_tool_is_absent_from_loop_source() -> None:
    source = LOOP_SOURCE.read_text(encoding="utf-8")
    assert FAKE_TOOL_NAME not in source
    lowered = source.lower()
    assert "skill" not in lowered
    assert "mcp" not in lowered


def test_ac01_scripted_local_environment_six_step_loop(tmp_path: Path) -> None:
    workspace = _copy_fixture(tmp_path)
    counter_path = workspace / "counter.py"
    assert "return value + 2" in counter_path.read_text(encoding="utf-8")

    fake_tool = RecordNoteTool()
    registry = default_tool_registry()
    registry.register(fake_tool, active=True)
    environment = LocalExecutionEnvironment(workspace, use_rg=False)
    runtime = AgentRuntime(
        "agent-ac01",
        "session-ac01",
        environment,
        AllowPolicy(),
        active_tools=set(registry.active_names),
        budget=BudgetTracker(max_steps=12, max_tool_calls=16, max_protocol_errors=3),
    )
    store = SessionStore(tmp_path / "session.jsonl", session_id=runtime.session_id, fsync=False)
    verify = f"rm -rf __pycache__ && {quote(sys.executable)} -B verify.py"
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                text="I will inspect the fixture.",
                tool_calls=(
                    _call("ac01-list", "list_files", '{"recursive":true}'),
                    _call("ac01-read", "read_file", '{"path":"counter.py"}'),
                    _call("ac01-search", "search", '{"query":"increment"}'),
                    _call(
                        "ac01-note",
                        FAKE_TOOL_NAME,
                        '{"note":"inspected-counter"}',
                    ),
                ),
            ),
            NormalizedAssistantResponse(
                tool_calls=(
                    _call(
                        "ac01-edit-wrong",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "counter.py",
                                "old_text": "return value + 2",
                                "new_text": "return value + 3",
                            }
                        ),
                    ),
                )
            ),
            NormalizedAssistantResponse(
                tool_calls=(_call("ac01-verify-fail", "bash", json.dumps({"command": verify})),)
            ),
            NormalizedAssistantResponse(
                tool_calls=(
                    _call(
                        "ac01-edit-fix",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "counter.py",
                                "old_text": "return value + 3",
                                "new_text": "return value + 1",
                            }
                        ),
                    ),
                )
            ),
            NormalizedAssistantResponse(
                tool_calls=(_call("ac01-verify-pass", "bash", json.dumps({"command": verify})),)
            ),
            NormalizedAssistantResponse(
                text="Fixed increment to add one and verified with the fixture script.",
                finish_reason="stop",
            ),
        ]
    )
    loop = AgentLoop(
        model,
        registry,
        EventEmitter(store),
        runtime,
        system_prompt=SYSTEM_PROMPT,
        model_parameters={"temperature": 0},
    )

    result = loop.run_turn("Fix increment so verify.py passes.")

    assert result.state is RuntimeState.COMPLETED_TURN
    assert "Fixed increment" in result.assistant_text
    assert fake_tool.notes == ["inspected-counter"]
    assert "return value + 1" in counter_path.read_text(encoding="utf-8")
    assert model.responses == []
    assert len(model.requests) == 6
    assert all(
        request["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        for request in model.requests
    )
    schema_names = [schema["function"]["name"] for schema in model.requests[0]["tools"]]
    assert FAKE_TOOL_NAME in schema_names
    assert "read_file" in schema_names

    failed_verify = _last_tool_payload(model.requests[3])
    assert failed_verify["status"] != "success"
    assert failed_verify.get("exit_code") not in {0, None}

    passed_verify = _last_tool_payload(model.requests[5])
    assert passed_verify["status"] == "success"
    assert passed_verify.get("exit_code") == 0
    assert "ok" in passed_verify["text"]

    loop.close(reason="ac01_complete")
    assert environment.closed

    events = store.read_events()
    types = [event.type for event in events]
    assert "user_message" in types
    assert "assistant_message" in types
    assert "tool_call" in types
    assert "policy_decision" in types
    assert "tool_result" in types
    assert types[-3:] == ["turn_end", "agent_end", "session_end"]
    assert events[-3].payload["state"] == RuntimeState.COMPLETED_TURN
    assert all(event.agent_id == runtime.agent_id for event in events)

    tool_events = [event for event in events if event.type.startswith("tool_")]
    correlations = {event.correlation_id for event in tool_events}
    assert len(correlations) == 8
    for correlation in correlations:
        related = [event.type for event in events if event.correlation_id == correlation]
        assert related == ["tool_call", "policy_decision", "tool_result"]
        provider_ids = {
            str(event.provider_tool_call_id)
            for event in events
            if event.correlation_id == correlation and event.type == "tool_call"
        }
        assert provider_ids
        assert all(item and item != str(correlation) for item in provider_ids)
