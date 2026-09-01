"""CLI --resume loads a linear session and accepts follow-up without replay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.assembly import build_local_backend
from coding_agent_neo.backend import SubmitTask
from coding_agent_neo.cli import (
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_SUCCESS,
    main,
    run_cli,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.models import (
    EventType,
    NormalizedAssistantResponse,
    NormalizedToolCall,
)
from coding_agent_neo.session import read_session

SHORT_POLL = 0.05
SHORT_SHUTDOWN = 2.0


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools, parameters):
        del messages, tools, parameters
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class RecordingLocalEnvironment(LocalExecutionEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.writes: list[str] = []
        self.commands: list[str] = []

    def write_file(self, request, cancellation):
        self.writes.append(str(getattr(request, "path", "")))
        return super().write_file(request, cancellation)

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


def factory_for(model, environment=None):
    def factory(cfg, *, interactive, resume=None, **_kwargs):
        return build_local_backend(
            cfg,
            interactive=interactive,
            resume=resume,
            model_client=model,
            environment=environment,
            approval_timeout_seconds=2.0,
            worker_shutdown_timeout_seconds=SHORT_SHUTDOWN,
            event_poll_timeout_seconds=SHORT_POLL,
            fsync=False,
        )

    return factory


def wait_turn(tmp_path: Path, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        paths = list((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
        if paths:
            events = read_session(paths[0]).events
            if any(event.type == EventType.TURN_END for event in events):
                return paths[0], events
        time.sleep(SHORT_POLL)
    raise TimeoutError("timed out waiting for turn_end")


def seed_session(tmp_path: Path, responses, *, environment=None, task: str = "seed"):
    backend = build_local_backend(
        config(tmp_path),
        interactive=False,
        model_client=ScriptedModel(responses),
        environment=environment or FakeExecutionEnvironment(),
        worker_shutdown_timeout_seconds=SHORT_SHUTDOWN,
        event_poll_timeout_seconds=SHORT_POLL,
        fsync=False,
    )
    try:
        backend.send(SubmitTask(task))
        path, _events = wait_turn(tmp_path)
    finally:
        backend.close()
    return path, read_session(path).events


def write_call(path: str, content: str) -> NormalizedAssistantResponse:
    return NormalizedAssistantResponse(
        tool_calls=(
            NormalizedToolCall(
                provider_tool_call_id="provider_write",
                name="write_file",
                raw_arguments=json.dumps({"path": path, "content": content}),
                arguments_valid=True,
            ),
        )
    )


def test_cli_resume_followup_continues_sequence_and_stdio_contract(tmp_path: Path) -> None:
    path, original = seed_session(
        tmp_path,
        [NormalizedAssistantResponse(text="original-answer")],
        task="remember me",
    )
    last_sequence = original[-1].sequence
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="what next",
        interactive=False,
        input_stream=StringIO(),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=factory_for(
            ScriptedModel([NormalizedAssistantResponse(text="follow-answer")])
        ),
        resume=path.stem,
    )

    assert code == EXIT_SUCCESS
    assert stdout.getvalue() == "follow-answer\n"
    rendered = stderr.getvalue()
    assert "assistant> follow-answer" in rendered
    assert "original-answer" not in rendered
    assert "remember me" not in rendered
    assert "To continue this session, run:" in rendered
    assert f"--resume {path.stem}" in rendered
    events = read_session(path).events
    sequences = [event.sequence for event in events]
    assert sequences == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)
    new_events = [event for event in events if event.sequence > last_sequence]
    assert new_events[0].sequence == last_sequence + 1
    assert any(event.type == EventType.USER_MESSAGE for event in new_events)
    assert any(event.type == EventType.TURN_END for event in new_events)
    assert any(event.type == EventType.SESSION_START for event in new_events)


def test_cli_resume_by_session_id(tmp_path: Path) -> None:
    path, _original = seed_session(
        tmp_path,
        [NormalizedAssistantResponse(text="id-answer")],
        task="seed",
    )
    session_id = path.stem
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="follow",
        interactive=False,
        input_stream=StringIO(),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=factory_for(ScriptedModel([NormalizedAssistantResponse(text="from-id")])),
        resume=session_id,
    )
    assert code == EXIT_SUCCESS
    assert stdout.getvalue() == "from-id\n"


def test_cli_resume_does_not_replay_historical_side_effects(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    first = RecordingLocalEnvironment(workspace)
    path, _original = seed_session(
        tmp_path,
        [write_call("note.txt", "hello"), NormalizedAssistantResponse(text="wrote")],
        environment=first,
        task="write a note",
    )
    assert first.writes == ["note.txt"]

    resumed = RecordingLocalEnvironment(workspace)
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="status",
        interactive=False,
        input_stream=StringIO(),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=factory_for(
            ScriptedModel([NormalizedAssistantResponse(text="still here")]),
            environment=resumed,
        ),
        resume=path.stem,
    )
    assert code == EXIT_SUCCESS
    assert stdout.getvalue() == "still here\n"
    assert resumed.writes == []
    assert resumed.commands == []
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"


def test_cli_resume_reports_incomplete_tail_on_stderr(tmp_path: Path) -> None:
    path, _original = seed_session(
        tmp_path,
        [NormalizedAssistantResponse(text="complete")],
        task="seed",
    )
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"session_id":"partial"')
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="continue",
        interactive=False,
        input_stream=StringIO(),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=factory_for(ScriptedModel([NormalizedAssistantResponse(text="after")])),
        resume=path.stem,
    )
    assert code == EXIT_SUCCESS
    assert stdout.getvalue() == "after\n"
    assert "session diagnostic" in stderr.getvalue()
    assert "incomplete" in stderr.getvalue()


def test_cli_interactive_resume_prompts_followup(tmp_path: Path) -> None:
    path, _original = seed_session(
        tmp_path,
        [NormalizedAssistantResponse(text="seeded")],
        task="seed",
    )
    output = StringIO()
    code = run_cli(
        config(tmp_path),
        task=None,
        interactive=True,
        input_stream=StringIO("next please\n"),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=factory_for(ScriptedModel([NormalizedAssistantResponse(text="next")])),
        resume=path.stem,
    )
    assert code == EXIT_SUCCESS
    text = output.getvalue()
    assert "follow-up>" in text
    assert "task>" not in text
    assert "assistant> next" in text
    assert "To continue this session, run:" in text
    assert f"--resume {path.stem}" in text


def test_main_missing_session_is_configuration_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    code = main(
        [
            "--resume",
            "session_missing",
            "--task",
            "follow",
            "--workspace",
            str(tmp_path),
            "--api-key-env",
            "OPENAI_API_KEY",
        ]
    )
    assert code == EXIT_CONFIG


def test_main_rejects_explicit_jsonl_resume_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    explicit = tmp_path / "outside.jsonl"
    explicit.write_text("", encoding="utf-8")
    code = main(
        [
            "--resume",
            str(explicit),
            "--task",
            "follow",
            "--workspace",
            str(tmp_path),
            "--api-key-env",
            "OPENAI_API_KEY",
        ]
    )
    assert code == EXIT_CONFIG


def test_main_corrupt_session_is_startup_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_bad.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1}\n', encoding="utf-8")
    code = main(
        [
            "--resume",
            path.stem,
            "--task",
            "follow",
            "--workspace",
            str(tmp_path),
            "--api-key-env",
            "OPENAI_API_KEY",
        ]
    )
    assert code == EXIT_FAILED


def test_subprocess_resume_missing_file_exit_code(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["OPENAI_API_KEY"] = "placeholder"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coding_agent_neo",
            "--resume",
            "session_missing",
            "--task",
            "follow",
            "--workspace",
            str(tmp_path),
            "--api-key-env",
            "OPENAI_API_KEY",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == EXIT_CONFIG
    assert result.stdout == ""
    assert "configuration error" in result.stderr
    assert "placeholder" not in result.stderr
