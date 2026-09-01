"""Linear session recovery rebuilds Runtime state without replaying side effects."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.assembly import (
    SessionResumeError,
    build_agent_backend_provider,
    build_local_backend,
    recover_session_plan,
)
from coding_agent_neo.backend import SubmitTask
from coding_agent_neo.config import AppConfig, ConfigError
from coding_agent_neo.context import ContextBuilder
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.models import (
    EventType,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    NormalizedUsage,
)
from coding_agent_neo.session import (
    SessionFormatError,
    discard_incomplete_tail,
    read_session,
    resolve_session_path,
)

SHORT_POLL = 0.05
SHORT_SHUTDOWN = 2.0


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def complete(self, messages, tools, parameters):
        self.requests.append({"messages": [dict(item) for item in messages], "tools": list(tools)})
        del parameters
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class RecordingLocalEnvironment(LocalExecutionEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.writes: list[str] = []
        self.edits: list[str] = []
        self.commands: list[str] = []

    def write_file(self, request, cancellation):
        self.writes.append(str(getattr(request, "path", "")))
        return super().write_file(request, cancellation)

    def edit_file(self, request, cancellation):
        self.edits.append(str(getattr(request, "path", "")))
        return super().edit_file(request, cancellation)

    def run_command(self, request, cancellation):
        self.commands.append(str(getattr(request, "command", "")))
        return super().run_command(request, cancellation)


def config(tmp_path: Path, **changes) -> AppConfig:
    values = {
        "workspace": tmp_path,
        "api_key": "placeholder",
        "approval_mode": "auto",
        "context_window": 8000,
        "reserved_output_tokens": 1000,
    }
    values.update(changes)
    return AppConfig(**values)


def make_backend(tmp_path: Path, model, *, resume=None, environment=None, **changes):
    return build_local_backend(
        config(tmp_path, **changes),
        interactive=False,
        resume=resume,
        model_client=model,
        environment=environment,
        approval_timeout_seconds=0.3,
        worker_shutdown_timeout_seconds=SHORT_SHUTDOWN,
        event_poll_timeout_seconds=SHORT_POLL,
        fsync=False,
    )


def wait_session(tmp_path: Path, predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        paths = list((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
        if paths:
            events = read_session(paths[0]).events
            if any(predicate(event) for event in events):
                return paths[0], events
        time.sleep(SHORT_POLL)
    raise TimeoutError("timed out waiting for persisted event")


def run_session(tmp_path: Path, responses, *, environment=None, task: str = "first"):
    model = ScriptedModel(responses)
    env = FakeExecutionEnvironment() if environment is None else environment
    backend = make_backend(tmp_path, model, environment=env)
    try:
        backend.send(SubmitTask(task))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
    finally:
        backend.close()
    path = next((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
    return path, env, model


def write_call(path: str, content: str) -> NormalizedAssistantResponse:
    return NormalizedAssistantResponse(
        tool_calls=(
            NormalizedToolCall(
                provider_tool_call_id="provider_write",
                name="write_file",
                raw_arguments=json.dumps({"path": path, "content": content}),
                arguments_valid=True,
            ),
        ),
        usage=NormalizedUsage(input_tokens=10, output_tokens=4, total_tokens=14),
    )


def envelope(
    sequence: int,
    event_type: str,
    payload: dict,
    *,
    session_id: str = "session_resume1",
    agent_id: str = "agent_resume1",
    event_id: str | None = None,
    correlation_id: str | None = None,
    provider_tool_call_id: str | None = None,
    parent_agent_id: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "event_id": event_id or f"event_{sequence:08d}",
        "agent_id": agent_id,
        "parent_agent_id": parent_agent_id,
        "sequence": sequence,
        "type": event_type,
        "correlation_id": correlation_id,
        "provider_tool_call_id": provider_tool_call_id,
        "timestamp": "2026-08-30T00:00:00Z",
        "payload": payload,
    }


def json_line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json_line(record) + "\n" for record in records)
    path.write_text("".join(serialized), encoding="utf-8")


def minimal_session_records(**changes) -> list[dict]:
    session_id = changes.get("session_id", "session_resume1")
    agent_id = changes.get("agent_id", "agent_resume1")
    tools = changes.get(
        "active_tools",
        ["read_file", "list_files", "search", "write_file", "edit_file", "bash"],
    )
    return [
        envelope(
            1,
            "session_start",
            {"state": "RUNNING"},
            session_id=session_id,
            agent_id=agent_id,
        ),
        envelope(
            2,
            "agent_start",
            {"state": "RUNNING", "active_tools": tools},
            session_id=session_id,
            agent_id=agent_id,
        ),
        envelope(
            3,
            "user_message",
            {"text": "hello"},
            session_id=session_id,
            agent_id=agent_id,
        ),
        envelope(
            4,
            "assistant_message",
            {
                "text": "done",
                "tool_calls": [],
                "usage": {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13},
            },
            session_id=session_id,
            agent_id=agent_id,
        ),
        envelope(
            5,
            "turn_end",
            {
                "state": "COMPLETED_TURN",
                "budget": {
                    "model_steps": 1,
                    "tool_calls": 0,
                    "protocol_errors": 0,
                    "input_tokens": 11,
                    "output_tokens": 2,
                },
            },
            session_id=session_id,
            agent_id=agent_id,
        ),
    ]


def test_resolve_session_path_uses_only_fixed_workspace_directory(tmp_path: Path) -> None:
    assert resolve_session_path("session_abc", tmp_path) == (
        tmp_path / ".coding-agent-neo" / "sessions" / "session_abc.jsonl"
    )
    with pytest.raises(ValueError):
        resolve_session_path(str(tmp_path / "custom.jsonl"), tmp_path)


def _symlink_directory_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")


@pytest.mark.parametrize("component", (".coding-agent-neo", "sessions"))
def test_new_backend_rejects_symlinked_session_component(
    tmp_path: Path,
    component: str,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-session-outside"
    outside.mkdir()
    root = tmp_path / ".coding-agent-neo"
    if component == "sessions":
        root.mkdir()
        link = root / component
    else:
        link = root
    _symlink_directory_or_skip(link, outside)

    with pytest.raises(ValueError, match="symlink"):
        resolve_session_path("session_new", tmp_path)
    with pytest.raises(ConfigError, match="invalid"):
        make_backend(
            tmp_path,
            ScriptedModel([]),
            environment=FakeExecutionEnvironment(),
        )

    assert link.is_symlink()
    assert not list(outside.rglob("*.jsonl"))


@pytest.mark.parametrize("component", (".coding-agent-neo", "sessions"))
def test_resume_rejects_symlinked_session_component(
    tmp_path: Path,
    component: str,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-resume-outside"
    outside_sessions = outside / "sessions"
    outside_sessions.mkdir(parents=True)
    session_id = "session_symlinked"
    outside_path = outside_sessions / f"{session_id}.jsonl"
    write_records(outside_path, minimal_session_records(session_id=session_id))
    before = outside_path.read_bytes()

    root = tmp_path / ".coding-agent-neo"
    if component == "sessions":
        root.mkdir()
        link = root / component
    else:
        link = root
    _symlink_directory_or_skip(link, outside)

    with pytest.raises(ConfigError, match="invalid"):
        make_backend(
            tmp_path,
            ScriptedModel([]),
            resume=session_id,
            environment=FakeExecutionEnvironment(),
        )

    assert link.is_symlink()
    assert outside_path.read_bytes() == before


def test_full_session_restores_ids_budget_tools_context_and_followup(tmp_path: Path) -> None:
    path, _env, _model = run_session(
        tmp_path,
        [NormalizedAssistantResponse(text="first-answer")],
        task="remember this",
    )
    original = read_session(path)
    root = next(event for event in original.events if event.type == EventType.AGENT_START)
    last_budget = original.events[-1].payload["budget"]
    now = time.monotonic()

    fake = FakeExecutionEnvironment()
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="follow-answer")]),
        resume=path.stem,
        environment=fake,
    )
    try:
        runtime = backend._loop.runtime
        assert str(runtime.session_id) == str(original.events[0].session_id)
        assert str(runtime.agent_id) == str(root.agent_id)
        assert runtime.budget.model_steps == last_budget["model_steps"]
        assert runtime.budget.tool_calls == last_budget["tool_calls"]
        assert runtime.budget.protocol_errors == last_budget["protocol_errors"]
        assert runtime.budget.input_tokens == last_budget["input_tokens"]
        assert runtime.budget.output_tokens == last_budget["output_tokens"]
        assert abs((runtime.budget.started_at or 0) - now) < 5
        assert runtime.active_tools == set(root.payload["active_tools"])
        assert set(backend._loop.registry.active_names) == runtime.active_tools
        assert runtime.cancellation.is_cancelled is False
        contents = [message.get("content") for message in runtime.context_state.recent_messages]
        assert "remember this" in contents
        assert "first-answer" in contents

        backend.send(SubmitTask("continue"))
        wait_session(
            tmp_path,
            lambda event: (
                event.type == EventType.TURN_END and event.sequence > original.last_valid_sequence
            ),
        )
        events = read_session(path).events
        sequences = [event.sequence for event in events]
        assert sequences == list(range(1, len(events) + 1))
        assert len({event.event_id for event in events}) == len(events)
        follow = [event for event in events if event.sequence > original.last_valid_sequence]
        assert follow[0].sequence == original.last_valid_sequence + 1
        assert any(
            event.type == EventType.USER_MESSAGE and event.payload.get("text") == "continue"
            for event in follow
        )
        assert any(event.type == EventType.TURN_END for event in follow)
        assert any(event.type == EventType.SESSION_START for event in follow)
    finally:
        backend.close()


def test_incomplete_tail_is_reported_ignored_and_does_not_block_resume(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_resume1.jsonl"
    write_records(path, minimal_session_records())
    complete = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"session_id":"session_resume1"')

    plan = recover_session_plan(path)
    assert plan.diagnostics
    assert plan.diagnostics[0].code == "incomplete_tail"
    assert [message[0]["content"] for message in plan.messages] == ["hello", "done"]

    fake = FakeExecutionEnvironment()
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="after-tail")]),
        resume=path.stem,
        environment=fake,
    )
    try:
        assert backend.resume_diagnostics[0].code == "incomplete_tail"
        assert fake.calls == []
        backend.send(SubmitTask("follow"))
        wait_session(
            tmp_path,
            lambda event: (
                event.type == EventType.ASSISTANT_MESSAGE
                and event.payload.get("text") == "after-tail"
            ),
        )
    finally:
        backend.close()
    restored = read_session(path)
    assert restored.tail_diagnostic is None
    assert path.read_bytes().startswith(complete)
    assert any(event.payload.get("text") == "after-tail" for event in restored.events)


def test_middle_corruption_schema_and_identity_errors_fail(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    records = minimal_session_records()
    records.insert(
        2,
        envelope(9, "user_message", {"text": "bad"}, event_id="event_not_json"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json_line(record) for record in records[:2]]
    lines.append("{not-json")
    lines.extend(json_line(record) for record in records[2:])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(SessionFormatError):
        recover_session_plan(path)

    schema_path = tmp_path / "schema.jsonl"
    bad_schema = minimal_session_records()
    bad_schema[0]["schema_version"] = 2
    write_records(schema_path, bad_schema)
    with pytest.raises(SessionFormatError):
        recover_session_plan(schema_path)

    id_path = tmp_path / "id-change.jsonl"
    changed = minimal_session_records()
    changed[3]["session_id"] = "session_other1"
    write_records(id_path, changed)
    with pytest.raises(SessionFormatError):
        recover_session_plan(id_path)

    seq_path = tmp_path / "sequence.jsonl"
    repeated = minimal_session_records()
    repeated[3]["sequence"] = 3
    write_records(seq_path, repeated)
    with pytest.raises(SessionFormatError):
        recover_session_plan(seq_path)

    dup_path = tmp_path / "dup-id.jsonl"
    duplicated = minimal_session_records()
    duplicated[3]["event_id"] = duplicated[0]["event_id"]
    write_records(dup_path, duplicated)
    with pytest.raises(SessionFormatError):
        recover_session_plan(dup_path)


def test_empty_file_and_missing_root_agent_fail(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SessionResumeError, match="empty"):
        recover_session_plan(empty)

    missing_root = tmp_path / "no-root.jsonl"
    write_records(
        missing_root,
        [envelope(1, "session_start", {"state": "RUNNING"})],
    )
    with pytest.raises(SessionResumeError, match="root agent_start"):
        recover_session_plan(missing_root)

    with pytest.raises(ConfigError, match="not found"):
        make_backend(
            tmp_path,
            ScriptedModel([]),
            resume="session_missing",
            environment=FakeExecutionEnvironment(),
        )

    with pytest.raises(ConfigError, match="invalid"):
        make_backend(
            tmp_path,
            ScriptedModel([]),
            resume=str(tmp_path / "outside.jsonl"),
            environment=FakeExecutionEnvironment(),
        )


def test_compaction_restores_summary_and_later_complete_groups_only(tmp_path: Path) -> None:
    session_id = "session_compacted"
    path = tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl"
    records = [
        envelope(1, "session_start", {"state": "RUNNING"}),
        envelope(
            2,
            "agent_start",
            {
                "state": "RUNNING",
                "active_tools": [
                    "read_file",
                    "list_files",
                    "search",
                    "write_file",
                    "edit_file",
                    "bash",
                ],
            },
        ),
        envelope(3, "user_message", {"text": "old task"}),
        envelope(4, "assistant_message", {"text": "old answer", "tool_calls": []}),
        envelope(
            5,
            "compaction",
            {
                "status": "success",
                "summary": "Summarized earlier work.",
                "covered_through_sequence": 4,
                "source_start_sequence": 3,
                "source_end_sequence": 4,
            },
        ),
        envelope(6, "user_message", {"text": "later question"}),
        envelope(
            7,
            "assistant_message",
            {
                "text": "",
                "tool_calls": [
                    {
                        "correlation_id": "correlation_write1",
                        "provider_tool_call_id": "write_1",
                        "name": "write_file",
                        "raw_arguments": '{"path": "x.txt", "content": "x"}',
                        "diagnostics": [],
                    }
                ],
            },
        ),
        envelope(
            8,
            "tool_result",
            {
                "status": "success",
                "text": "wrote",
                "result": {
                    "correlation_id": "correlation_write1",
                    "status": "success",
                    "text": "wrote",
                    "metadata": {},
                },
            },
            correlation_id="correlation_write1",
            provider_tool_call_id="write_1",
        ),
        envelope(9, "assistant_message", {"text": "later answer", "tool_calls": []}),
        envelope(
            10,
            "assistant_message",
            {
                "text": "",
                "tool_calls": [
                    {
                        "correlation_id": "correlation_open",
                        "provider_tool_call_id": "open_1",
                        "name": "write_file",
                        "raw_arguments": '{"path": "y.txt", "content": "y"}',
                        "diagnostics": [],
                    }
                ],
            },
        ),
    ]
    for record in records:
        record["session_id"] = session_id
    write_records(path, records)
    plan = recover_session_plan(path)
    assert plan.latest_summary == "Summarized earlier work."
    assert plan.covered_through_sequence == 4
    contents = [message["content"] for message, _sequence in plan.messages]
    assert "old task" not in contents
    assert "old answer" not in contents
    assert "later question" in contents
    assert "later answer" in contents
    assert sum(1 for message, _sequence in plan.messages if message.get("role") == "tool") == 1
    # Trailing incomplete assistant/tool group is omitted rather than split.
    assert not any(
        message.get("role") == "assistant"
        and message.get("tool_calls")
        and "open_1" in json.dumps(message)
        for message, _sequence in plan.messages
    )

    fake = FakeExecutionEnvironment()
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="resumed")]),
        resume=path.stem,
        environment=fake,
    )
    try:
        builder = ContextBuilder("prompt", context_window=8000, reserved_output_tokens=100)
        projection = builder.project(backend._loop.runtime)
        projected = "\n".join(str(message.get("content")) for message in projection.messages)
        assert "Summarized earlier work." in projected
        assert "later question" in projected
        assert "old task" not in projected
    finally:
        backend.close()


def test_fake_environment_sees_no_historical_side_effects_during_resume(tmp_path: Path) -> None:
    path, first_env, _model = run_session(
        tmp_path,
        [write_call("note.txt", "hello"), NormalizedAssistantResponse(text="wrote")],
        task="write a note",
    )
    assert any(call.operation == "write_file" for call in first_env.calls)
    historical_writes = [
        call for call in first_env.calls if call.operation in {"write_file", "run_command"}
    ]
    assert historical_writes

    fake = FakeExecutionEnvironment()
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="still here")]),
        resume=path.stem,
        environment=fake,
    )
    try:
        assert fake.started is False
        assert fake.calls == []
        backend.send(SubmitTask("status"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        side_effects = {"write_file", "edit_file", "run_command"}
        assert not any(call.operation in side_effects for call in fake.calls)
    finally:
        backend.close()


def test_local_environment_sees_no_historical_side_effects_during_resume(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    first = RecordingLocalEnvironment(workspace)
    path, first_env, _model = run_session(
        tmp_path,
        [write_call("note.txt", "hello"), NormalizedAssistantResponse(text="wrote")],
        environment=first,
        task="write a note",
    )
    assert first_env.writes == ["note.txt"]
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"

    resumed = RecordingLocalEnvironment(workspace)
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="still here")]),
        resume=path.stem,
        environment=resumed,
    )
    try:
        assert resumed.writes == []
        assert resumed.edits == []
        assert resumed.commands == []
        backend.send(SubmitTask("status"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        assert resumed.writes == []
        assert resumed.commands == []
    finally:
        backend.close()
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"


def test_assembled_provider_resume_continues_sequence_without_replaying_effects(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    first_environment = RecordingLocalEnvironment(workspace)
    path, _first_environment, _model = run_session(
        tmp_path,
        [write_call("note.txt", "hello"), NormalizedAssistantResponse(text="wrote")],
        environment=first_environment,
        task="write a note",
    )
    original_events = read_session(path).events
    last_sequence = original_events[-1].sequence
    assert first_environment.writes == ["note.txt"]

    resumed_environment = RecordingLocalEnvironment(workspace)
    provider = build_agent_backend_provider(
        config(tmp_path),
        interactive=False,
        model_client=ScriptedModel([NormalizedAssistantResponse(text="still here")]),
        environment=resumed_environment,
        worker_shutdown_timeout_seconds=SHORT_SHUTDOWN,
        event_poll_timeout_seconds=SHORT_POLL,
        fsync=False,
    )
    backend = provider.create_session(resume_session_id=path.stem)
    try:
        assert backend.resume_last_sequence == last_sequence
        assert resumed_environment.writes == []
        assert resumed_environment.commands == []
        backend.send(SubmitTask("status"))
        wait_session(
            tmp_path,
            lambda event: event.type == EventType.TURN_END and event.sequence > last_sequence,
        )
    finally:
        backend.close()

    events = read_session(path).events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    follow_up = [event for event in events if event.sequence > last_sequence]
    assert follow_up[0].sequence == last_sequence + 1
    assert any(
        event.type == EventType.USER_MESSAGE and event.payload.get("text") == "status"
        for event in follow_up
    )
    assert any(event.type == EventType.TURN_END for event in follow_up)
    assert resumed_environment.writes == []
    assert resumed_environment.commands == []
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"


def test_interrupted_session_resumes_with_fresh_cancellation(tmp_path: Path) -> None:
    session_id = "session_interrupted"
    path = tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl"
    records = minimal_session_records(session_id=session_id)
    records.append(
        envelope(
            6,
            "session_end",
            {
                "state": "INTERRUPTED",
                "budget": {
                    "model_steps": 1,
                    "tool_calls": 0,
                    "protocol_errors": 0,
                    "input_tokens": 11,
                    "output_tokens": 2,
                },
            },
            session_id=session_id,
        )
    )
    write_records(path, records)
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="ok")]),
        resume=path.stem,
        environment=FakeExecutionEnvironment(),
    )
    try:
        assert backend._loop.runtime.cancellation.is_cancelled is False
    finally:
        backend.close()


def test_discard_incomplete_tail_truncates_only_uncommitted_bytes(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    write_records(path, minimal_session_records())
    complete = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"partial":')
    diagnostic = discard_incomplete_tail(path)
    assert diagnostic is not None
    assert diagnostic.code == "incomplete_tail"
    assert path.read_bytes() == complete
    assert discard_incomplete_tail(path) is None
