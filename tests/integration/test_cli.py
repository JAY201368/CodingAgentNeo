from __future__ import annotations

import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

from coding_agent_neo.assembly import build_local_backend
from coding_agent_neo.cli import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_LIMIT_REACHED,
    build_parser,
    exit_code_for,
    format_resume_hint,
    run_cli,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.model_client import (
    ModelClientError,
    ModelErrorCategory,
    ModelErrorCode,
    OpenAICompatibleModelClient,
)
from coding_agent_neo.models import (
    EventType,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
)
from coding_agent_neo.session import read_session


def test_parser_and_help_expose_public_contract(capsys) -> None:
    args = build_parser().parse_args(
        ["--task", "inspect", "--model", "model-name", "--max-wall-seconds", "10"]
    )
    assert args.task == "inspect"
    assert args.model == "model-name"
    assert args.max_wall_seconds == 10

    try:
        build_parser().parse_args(["--help"])
    except SystemExit as error:
        assert error.code == 0
    output = capsys.readouterr().out.lower()
    assert "interactive or one-shot" in output
    assert "exit codes" in output


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools, parameters):
        del messages, tools, parameters
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def config(tmp_path: Path, **changes) -> AppConfig:
    values = {
        "workspace": tmp_path,
        "session_dir": tmp_path / "sessions",
        "api_key": "placeholder",
        "context_window": 8000,
        "reserved_output_tokens": 1000,
    }
    values.update(changes)
    return AppConfig(**values)


def factory_for(model):
    def factory(cfg, *, interactive, **_kwargs):
        return build_local_backend(
            cfg,
            interactive=interactive,
            model_client=model,
            approval_timeout_seconds=2.0,
            worker_shutdown_timeout_seconds=5.0,
            event_poll_timeout_seconds=0.05,
            fsync=False,
        )

    return factory


def test_noninteractive_success_stdout_contract_and_parseable_session(tmp_path: Path) -> None:
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="inspect",
        interactive=False,
        input_stream=StringIO("must-not-be-read"),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=factory_for(ScriptedModel([NormalizedAssistantResponse(text="done")])),
    )

    assert code == 0
    assert stdout.getvalue() == "done\n"
    assert "assistant> done" in stderr.getvalue()
    paths = list((tmp_path / "sessions").glob("*.jsonl"))
    assert len(paths) == 1
    session_id = paths[0].stem
    hint = stderr.getvalue()
    assert "To continue this session, run:" in hint
    assert f"--resume {session_id}" in hint
    assert "--session-dir" in hint
    events = read_session(paths[0]).events
    assert events[-1].type == EventType.SESSION_END


def test_interactive_initial_and_followup(tmp_path: Path) -> None:
    output = StringIO()
    model = ScriptedModel(
        [NormalizedAssistantResponse(text="first"), NormalizedAssistantResponse(text="second")]
    )
    code = run_cli(
        config(tmp_path),
        task=None,
        interactive=True,
        input_stream=StringIO("initial\nfollow up\n"),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=factory_for(model),
    )
    assert code == 0
    assert "task> " in output.getvalue()
    assert "follow-up> " in output.getvalue()
    assert "assistant> first" in output.getvalue()
    assert "assistant> second" in output.getvalue()
    session_id = next((tmp_path / "sessions").glob("*.jsonl")).stem
    assert "To continue this session, run:" in output.getvalue()
    assert f"--resume {session_id}" in output.getvalue()


def test_failed_and_limit_exit_codes(tmp_path: Path) -> None:
    fatal = ModelClientError(ModelErrorCategory.FATAL, ModelErrorCode.UNKNOWN)
    code = run_cli(
        config(tmp_path),
        task="fail",
        interactive=False,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=StringIO(),
        backend_factory=factory_for(ScriptedModel([fatal])),
    )
    assert code == EXIT_FAILED
    limited = run_cli(
        config(tmp_path, max_steps=1),
        task="limit",
        interactive=False,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=StringIO(),
        backend_factory=factory_for(ScriptedModel([NormalizedAssistantResponse()])),
    )
    assert limited == EXIT_LIMIT_REACHED


def test_exit_code_interrupted_contract() -> None:
    class Result:
        state = RuntimeState.INTERRUPTED

    assert exit_code_for(Result()) == EXIT_INTERRUPTED


def test_format_resume_hint_omits_default_session_dir() -> None:
    hint = format_resume_hint("session_abc")
    assert hint == "To continue this session, run: coding-agent-neo --resume session_abc"
    default = format_resume_hint("session_abc", session_dir=Path(".coding-agent-neo/sessions"))
    assert default == hint


def test_format_resume_hint_includes_custom_session_dir(tmp_path: Path) -> None:
    session_dir = tmp_path / "custom sessions"
    hint = format_resume_hint("session_abc", session_dir=session_dir)
    assert hint.startswith("To continue this session, run: coding-agent-neo --resume session_abc")
    assert "--session-dir" in hint
    assert "custom sessions" in hint


def test_interactive_empty_input_does_not_print_resume_hint(tmp_path: Path) -> None:
    output = StringIO()
    code = run_cli(
        config(tmp_path),
        task=None,
        interactive=True,
        input_stream=StringIO(""),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=factory_for(ScriptedModel([])),
    )
    assert code == 0
    assert "To continue this session" not in output.getvalue()


def bash_call(command: str) -> NormalizedAssistantResponse:
    return NormalizedAssistantResponse(
        tool_calls=(
            NormalizedToolCall(
                provider_tool_call_id="provider_bash",
                name="bash",
                raw_arguments=f'{{"command":{command!r}}}'.replace("'", '"'),
                arguments_valid=True,
            ),
        )
    )


def test_noninteractive_ask_denies_without_reading_and_auto_runs(tmp_path: Path) -> None:
    ask_stderr = StringIO()
    ask_code = run_cli(
        config(tmp_path, approval_mode="ask"),
        task="ask",
        interactive=False,
        input_stream=StringIO("yes\n"),
        output_stream=StringIO(),
        error_stream=ask_stderr,
        backend_factory=factory_for(
            ScriptedModel([bash_call("printf blocked"), NormalizedAssistantResponse(text="denied")])
        ),
    )
    assert ask_code == 0
    assert "decision=deny" in ask_stderr.getvalue()
    assert "Approve bash" not in ask_stderr.getvalue()

    auto_stderr = StringIO()
    auto_code = run_cli(
        config(tmp_path, approval_mode="auto"),
        task="auto",
        interactive=False,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=auto_stderr,
        backend_factory=factory_for(
            ScriptedModel([bash_call("printf auto-ok"), NormalizedAssistantResponse(text="done")])
        ),
    )
    assert auto_code == 0
    assert "exit_code=0" in auto_stderr.getvalue()
    assert "auto-ok" in auto_stderr.getvalue()


def test_interactive_bash_confirmation(tmp_path: Path) -> None:
    output = StringIO()
    code = run_cli(
        config(tmp_path, approval_mode="ask"),
        task="confirm",
        interactive=True,
        input_stream=StringIO("yes\n"),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=factory_for(
            ScriptedModel([bash_call("printf approved"), NormalizedAssistantResponse(text="done")])
        ),
    )
    assert code == 0
    assert "Approve bash command" in output.getvalue()
    assert "decision=allow approved=true" in output.getvalue()
    assert "approved" in output.getvalue()


def test_keyboard_interrupt_is_documented_and_persisted(tmp_path: Path) -> None:
    stderr = StringIO()
    code = run_cli(
        config(tmp_path),
        task="interrupt",
        interactive=False,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=stderr,
        backend_factory=factory_for(ScriptedModel([KeyboardInterrupt()])),
    )
    assert code == EXIT_INTERRUPTED
    path = next((tmp_path / "sessions").glob("*.jsonl"))
    assert read_session(path).events[-1].payload["state"] == RuntimeState.INTERRUPTED
    assert "To continue this session, run:" in stderr.getvalue()
    assert f"--resume {path.stem}" in stderr.getvalue()


def test_subprocess_help_and_redacted_config_failure(tmp_path: Path) -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "coding_agent_neo", "--help"],
        cwd=tmp_path,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "Exit codes" in help_result.stdout
    secret = "subprocess-secret-sentinel"
    environment = os.environ.copy()
    environment.pop("MISSING_TEST_KEY", None)
    failure = subprocess.run(
        [
            sys.executable,
            "-m",
            "coding_agent_neo",
            "--task",
            "noop",
            "--workspace",
            str(tmp_path),
            "--session-dir",
            str(tmp_path / secret),
            "--api-key-env",
            "MISSING_TEST_KEY",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failure.returncode == 2
    assert failure.stdout == ""
    assert "configuration error" in failure.stderr
    assert secret not in failure.stderr
    assert not (tmp_path / secret).exists()


def test_model_retry_observer_exposes_only_stable_event_facts() -> None:
    attempts = 0
    retries: list[dict] = []

    def transport(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelClientError(ModelErrorCategory.RETRYABLE, ModelErrorCode.RATE_LIMIT)
        return {
            "choices": [{"message": {"content": "done", "tool_calls": []}, "finish_reason": "stop"}]
        }

    client = OpenAICompatibleModelClient(
        transport=transport,
        model="test-model",
        max_retries=1,
        initial_delay_seconds=0,
        sleep=lambda _delay: None,
        retry_observer=lambda payload: retries.append(dict(payload)),
    )
    assert client.complete([], []).text == "done"
    assert retries == [
        {
            "reason": "rate_limit",
            "category": "retryable",
            "status_code": None,
            "attempt": 1,
            "max_attempts": 2,
            "delay_seconds": 0.0,
        }
    ]
