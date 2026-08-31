"""Interactive and one-shot CLI frontend for CodingAgentNeo.

The CLI parses arguments, loads configuration, obtains an ``AgentBackend``
from the assembly layer, and then only sends commands / consumes events.
It does not hold Loop, Runtime, Store, Environment, ModelClient, or Registry
objects, and it does not assemble those dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from coding_agent_neo import __version__
from coding_agent_neo.assembly import SessionResumeError, build_in_process_adapter
from coding_agent_neo.backend import (
    AgentBackend,
    ApprovalResponse,
    BackendClosedError,
    CloseSession,
    Interrupt,
    SubmitTask,
)
from coding_agent_neo.config import AppConfig, ConfigError, load_config
from coding_agent_neo.models import EventType, RuntimeState
from coding_agent_neo.renderer import TerminalRenderer
from coding_agent_neo.session import SessionError, SessionFormatError

EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_LIMIT_REACHED = 3
EXIT_INTERRUPTED = 130
_DEFAULT_SESSION_DIR = Path(".coding-agent-neo/sessions")

_CONFIG_OPTIONS = (
    "model",
    "api_base",
    "api_key_env",
    "workspace",
    "session_dir",
    "approval_mode",
    "max_steps",
    "max_tool_calls",
    "max_wall_seconds",
    "command_timeout",
    "context_window",
    "reserved_output_tokens",
    "model_output_limit",
    "session_output_limit",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable public CLI parser without starting any dependency."""

    parser = argparse.ArgumentParser(
        prog="coding-agent-neo",
        description="Run a small, inspectable coding agent in interactive or one-shot mode.",
        epilog=(
            "Exit codes: 0 completed, 1 failed, 2 usage/configuration, "
            "3 limit reached, 130 interrupted. Local bash is not a sandbox."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--task", help="Run one task non-interactively; otherwise read stdin or prompt."
    )
    parser.add_argument(
        "--config", metavar="PATH", help="Local TOML path (default: .coding-agent-neo.toml)."
    )
    parser.add_argument("--model")
    parser.add_argument("--api-base")
    parser.add_argument(
        "--api-key-env",
        help="Environment variable containing the API key; key values are never CLI arguments.",
    )
    parser.add_argument("--workspace")
    parser.add_argument("--session-dir")
    parser.add_argument(
        "--resume",
        metavar="SESSION",
        help="Resume a linear session by ID or JSONL path.",
    )
    parser.add_argument("--approval-mode", choices=("ask", "auto", "deny"))
    parser.add_argument("--yolo", action="store_true", help="Alias for --approval-mode auto.")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-wall-seconds", type=float)
    parser.add_argument("--command-timeout", type=float)
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--reserved-output-tokens", type=int)
    parser.add_argument("--model-output-limit", type=int)
    parser.add_argument("--session-output-limit", type=int)
    return parser


def _cli_config_values(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        name: getattr(args, name) for name in _CONFIG_OPTIONS if getattr(args, name) is not None
    }
    if args.yolo:
        if values.get("approval_mode") not in {None, "auto"}:
            raise ConfigError("--yolo conflicts with --approval-mode")
        values["approval_mode"] = "auto"
    return values


def exit_code_for(result: RuntimeState | Any) -> int:
    """Derive a process exit code from ``AgentBackend.last_state`` (T10 contract)."""

    state = result if isinstance(result, RuntimeState) else getattr(result, "state", None)
    return {
        RuntimeState.COMPLETED_TURN: EXIT_SUCCESS,
        RuntimeState.LIMIT_REACHED: EXIT_LIMIT_REACHED,
        RuntimeState.INTERRUPTED: EXIT_INTERRUPTED,
        RuntimeState.FAILED: EXIT_FAILED,
    }.get(state, EXIT_FAILED)


def format_resume_hint(
    session_id: str,
    *,
    session_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Return the copy-paste command that continues a linear session."""

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    parts = ["coding-agent-neo", "--resume", session_id.strip()]
    if session_dir is not None and _session_dir_needs_flag(Path(session_dir)):
        parts.extend(["--session-dir", os.fspath(session_dir)])
    return "To continue this session, run: " + " ".join(shlex.quote(part) for part in parts)


def _session_dir_needs_flag(session_dir: Path) -> bool:
    if session_dir == _DEFAULT_SESSION_DIR:
        return False
    try:
        return session_dir.resolve() != _DEFAULT_SESSION_DIR.resolve()
    except OSError:
        return True


def _event_session_id(event: Any) -> str | None:
    value = getattr(event, "session_id", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _write_resume_hint(
    stream: TextIO,
    session_id: str | None,
    *,
    session_dir: Path,
) -> None:
    if not session_id:
        return
    stream.write(f"{format_resume_hint(session_id, session_dir=session_dir)}\n")
    stream.flush()


def _interactive_task(input_stream: TextIO, output: TextIO, prompt: str) -> str | None:
    output.write(prompt)
    output.flush()
    line = input_stream.readline()
    if not line:
        return None
    value = line.strip()
    return value or None


def _prompt_approval(event: Any, output: TextIO, input_stream: TextIO) -> bool:
    payload = event.payload if isinstance(getattr(event, "payload", None), Mapping) else {}
    tool = payload.get("tool_name", "tool")
    summary = payload.get("arguments_summary", "")
    if tool == "bash":
        output.write(f"Approve bash command {summary}? [y/N] ")
    else:
        output.write(f"Approve {tool} {json.dumps(summary, ensure_ascii=False)}? [y/N] ")
    output.flush()
    answer = input_stream.readline()
    return answer.strip().casefold() in {"y", "yes"}


def _event_name(event: Any) -> str:
    return str(getattr(event, "type", ""))


def _consume_turn(
    backend: AgentBackend,
    renderer: TerminalRenderer,
    *,
    cursor: int,
    interactive: bool,
    input_stream: TextIO,
    event_stream: TextIO,
) -> tuple[int, str, str | None]:
    assistant_text = ""
    session_id = None
    for event in backend.events(since=cursor):
        cursor = event.sequence
        if session_id is None:
            session_id = _event_session_id(event)
        renderer.render(event)
        name = _event_name(event)
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if name == EventType.ASSISTANT_MESSAGE.value:
            text = payload.get("text")
            if text:
                assistant_text = str(text)
        if name == EventType.APPROVAL_REQUEST.value:
            request_id = str(payload.get("request_id") or event.correlation_id or "")
            approved = _prompt_approval(event, event_stream, input_stream) if interactive else False
            backend.send(ApprovalResponse(request_id, approved))
        if name == EventType.TURN_END.value:
            text = payload.get("assistant_text")
            if text:
                assistant_text = str(text)
            return cursor, assistant_text, session_id
    return cursor, assistant_text, session_id


def _drain_events(
    backend: AgentBackend,
    renderer: TerminalRenderer,
    *,
    cursor: int,
) -> tuple[int, str | None]:
    session_id = None
    try:
        for event in backend.events(since=cursor):
            cursor = event.sequence
            if session_id is None:
                session_id = _event_session_id(event)
            renderer.render(event)
            if _event_name(event) in {
                EventType.SESSION_END.value,
                EventType.TURN_END.value,
            }:
                if (
                    backend.last_state
                    in {
                        RuntimeState.INTERRUPTED,
                        RuntimeState.FAILED,
                        RuntimeState.LIMIT_REACHED,
                        RuntimeState.COMPLETED_TURN,
                    }
                    and _event_name(event) == EventType.SESSION_END.value
                ):
                    break
    except (KeyboardInterrupt, BackendClosedError):
        return cursor, session_id
    return cursor, session_id


def run_cli(
    config: AppConfig,
    *,
    task: str | None,
    interactive: bool,
    input_stream: TextIO,
    output_stream: TextIO,
    error_stream: TextIO,
    backend_factory: Callable[..., AgentBackend] | None = None,
    resume: str | None = None,
) -> int:
    """Run after configuration and task-source validation have completed."""

    event_stream = output_stream if interactive else error_stream
    factory = build_in_process_adapter if backend_factory is None else backend_factory
    backend = factory(config, interactive=interactive, resume=resume)
    renderer = TerminalRenderer(event_stream, output_limit=min(config.model_output_limit, 8_000))
    cursor = int(getattr(backend, "resume_last_sequence", 0) or 0)
    for diagnostic in getattr(backend, "resume_diagnostics", ()):
        line_number = getattr(diagnostic, "line_number", None)
        message = getattr(diagnostic, "message", "ignored an incomplete final JSONL record")
        location = f" (line {line_number})" if line_number is not None else ""
        error_stream.write(f"session diagnostic: {message}{location}\n")
        error_stream.flush()
    ran_turn = False
    assistant_text = ""
    session_id: str | None = None
    try:
        current = task
        if interactive and current is None:
            prompt = "follow-up> " if resume is not None else "task> "
            current = _interactive_task(input_stream, output_stream, prompt)
        while current is not None:
            backend.send(SubmitTask(current))
            ran_turn = True
            cursor, assistant_text, seen = _consume_turn(
                backend,
                renderer,
                cursor=cursor,
                interactive=interactive,
                input_stream=input_stream,
                event_stream=event_stream,
            )
            if session_id is None:
                session_id = seen
            if not interactive or backend.last_state is not RuntimeState.COMPLETED_TURN:
                break
            current = _interactive_task(input_stream, output_stream, "follow-up> ")
        if not ran_turn:
            return EXIT_SUCCESS
        if not interactive and assistant_text:
            output_stream.write(f"{assistant_text}\n")
            output_stream.flush()
        _write_resume_hint(event_stream, session_id, session_dir=config.session_dir)
        return exit_code_for(backend.last_state)
    except KeyboardInterrupt:
        try:
            backend.send(Interrupt("keyboard_interrupt"))
        except BackendClosedError:
            pass
        try:
            backend.send(CloseSession("keyboard_interrupt"))
        except BackendClosedError:
            pass
        _, seen = _drain_events(backend, renderer, cursor=cursor)
        if session_id is None:
            session_id = seen
        if ran_turn:
            _write_resume_hint(event_stream, session_id, session_dir=config.session_dir)
        return EXIT_INTERRUPTED
    finally:
        try:
            backend.send(CloseSession("cli_exit"))
        except BackendClosedError:
            pass
        backend.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, validate without side effects, then run the selected CLI mode."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(_cli_config_values(args), config_path=args.config)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return EXIT_CONFIG

    if args.task is not None:
        task = args.task.strip()
        interactive = False
    elif not sys.stdin.isatty():
        task = sys.stdin.read().strip()
        interactive = False
    else:
        task = None
        interactive = True
    if not interactive and not task:
        print("usage error: provide a non-empty --task or stdin task", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return run_cli(
            config,
            task=task,
            interactive=interactive,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
            error_stream=sys.stderr,
            resume=args.resume,
        )
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    except (SessionResumeError, SessionFormatError, SessionError) as error:
        print(f"startup failure: {type(error).__name__}", file=sys.stderr)
        return EXIT_FAILED
    except Exception as error:
        print(f"startup failure: {type(error).__name__}", file=sys.stderr)
        return EXIT_FAILED


__all__ = [
    "EXIT_CONFIG",
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_LIMIT_REACHED",
    "EXIT_SUCCESS",
    "build_parser",
    "exit_code_for",
    "format_resume_hint",
    "main",
    "run_cli",
]
