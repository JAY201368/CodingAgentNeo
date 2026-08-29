"""Interactive and one-shot command-line assembly for CodingAgentNeo."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from coding_agent_neo import __version__
from coding_agent_neo.agent_loop import AgentLoop, AgentLoopResult
from coding_agent_neo.config import AppConfig, ConfigError, load_config
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.events import EventDispatchError, EventEmitter, PendingEvent
from coding_agent_neo.model_client import OpenAICompatibleModelClient
from coding_agent_neo.models import AgentId, RuntimeState, SessionId
from coding_agent_neo.policy import (
    DefaultExecutionPolicy,
    InteractiveApprovalPort,
    NonInteractiveApprovalPort,
)
from coding_agent_neo.renderer import TerminalRenderer
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools.registry import default_tool_registry

EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_LIMIT_REACHED = 3
EXIT_INTERRUPTED = 130

SYSTEM_PROMPT = """You are CodingAgentNeo, a local coding assistant. Work only on the user's task.
Use native tool calls to inspect, edit, and validate the configured workspace. Prefer small,
verifiable changes and report tests honestly. Structured file tools are workspace-bound. The bash
tool starts in the workspace but is not an operating-system sandbox and inherits the launching
user's permissions. Never reveal credentials or claim an unperformed action."""

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
        help="Reserved for T11 session recovery; not supported by this release.",
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


def build_system_prompt(config: AppConfig) -> str:
    """Create the explicit prompt; Context Builder never discovers external prompt sources."""

    return f"{SYSTEM_PROMPT}\nConfigured logical workspace: {config.workspace.name or '.'}."


def _session_path(config: AppConfig, session_id: SessionId) -> Path:
    return config.session_dir / f"{session_id}.jsonl"


def _approval_callback(output: TextIO, input_stream: TextIO) -> Callable[[Any], bool]:
    def approve(request: Any) -> bool:
        arguments = getattr(request, "arguments", {})
        command = arguments.get("command", "") if isinstance(arguments, Mapping) else ""
        command = command if len(command) <= 300 else f"{command[:297]}…"
        output.write(f"Approve bash command {json.dumps(command, ensure_ascii=False)}? [y/N] ")
        output.flush()
        answer = input_stream.readline()
        return answer.strip().casefold() in {"y", "yes"}

    return approve


@dataclass(slots=True)
class CliSession:
    loop: AgentLoop
    store: SessionStore
    session_path: Path

    def close(self) -> None:
        self.loop.close()
        self.store.close()


def assemble_session(
    config: AppConfig,
    *,
    interactive: bool,
    input_stream: TextIO,
    event_stream: TextIO,
    model_client: Any | None = None,
) -> CliSession:
    """Explicitly assemble one root Runtime and its source-agnostic dependencies."""

    session_id = SessionId(f"session_{uuid4().hex}")
    agent_id = AgentId(f"agent_{uuid4().hex}")
    registry = default_tool_registry()
    environment = LocalExecutionEnvironment(
        config.workspace,
        command_timeout=config.command_timeout,
        max_output_bytes=config.model_output_limit,
    )
    policy = DefaultExecutionPolicy(config.approval_mode, interactive=interactive)
    budget = BudgetTracker(
        max_steps=config.max_steps,
        max_tool_calls=config.max_tool_calls,
        max_wall_seconds=config.max_wall_seconds,
    )
    runtime = AgentRuntime(
        agent_id=agent_id,
        session_id=session_id,
        environment=environment,
        execution_policy=policy,
        budget=budget,
        active_tools=set(registry.active_names),
    )
    path = _session_path(config, session_id)
    store = SessionStore(path, session_id, max_payload_bytes=config.session_output_limit)
    renderer = TerminalRenderer(event_stream, output_limit=min(config.model_output_limit, 8_000))
    emitter = EventEmitter(store, [renderer])

    def publish_retry(payload: Mapping[str, Any]) -> None:
        try:
            emitter.publish(
                PendingEvent(
                    session_id=session_id,
                    agent_id=agent_id,
                    type="retry",
                    payload=payload,
                )
            )
        except EventDispatchError as error:
            if error.report.event is None:
                raise

    client = model_client or OpenAICompatibleModelClient(
        model=config.model,
        api_key=config.api_key,
        base_url=config.api_base,
        retry_observer=publish_retry,
    )
    approval_port = (
        InteractiveApprovalPort(_approval_callback(event_stream, input_stream))
        if interactive
        else NonInteractiveApprovalPort()
    )
    loop = AgentLoop(
        client,
        registry,
        emitter,
        runtime,
        system_prompt=build_system_prompt(config),
        approval_port=approval_port,
        interactive=interactive,
        model_output_limit=config.model_output_limit,
        context_window=config.context_window,
        reserved_output_tokens=config.reserved_output_tokens,
    )
    return CliSession(loop, store, path)


def exit_code_for(result: AgentLoopResult) -> int:
    return {
        RuntimeState.COMPLETED_TURN: EXIT_SUCCESS,
        RuntimeState.LIMIT_REACHED: EXIT_LIMIT_REACHED,
        RuntimeState.INTERRUPTED: EXIT_INTERRUPTED,
        RuntimeState.FAILED: EXIT_FAILED,
    }.get(result.state, EXIT_FAILED)


def _interactive_task(input_stream: TextIO, output: TextIO, prompt: str) -> str | None:
    output.write(prompt)
    output.flush()
    line = input_stream.readline()
    if not line:
        return None
    value = line.strip()
    return value or None


def run_cli(
    config: AppConfig,
    *,
    task: str | None,
    interactive: bool,
    input_stream: TextIO,
    output_stream: TextIO,
    error_stream: TextIO,
    session_factory: Callable[..., CliSession] | None = None,
) -> int:
    """Run after configuration and task-source validation have completed."""

    event_stream = output_stream if interactive else error_stream
    factory = assemble_session if session_factory is None else session_factory
    session = factory(
        config,
        interactive=interactive,
        input_stream=input_stream,
        event_stream=event_stream,
    )
    last_result: AgentLoopResult | None = None
    try:
        current = task
        if interactive and current is None:
            current = _interactive_task(input_stream, output_stream, "task> ")
        while current is not None:
            last_result = session.loop.run_turn(current)
            if not interactive or last_result.state is not RuntimeState.COMPLETED_TURN:
                break
            current = _interactive_task(input_stream, output_stream, "follow-up> ")
        if last_result is None:
            return EXIT_SUCCESS
        if not interactive and last_result.assistant_text:
            output_stream.write(f"{last_result.assistant_text}\n")
            output_stream.flush()
        return exit_code_for(last_result)
    except KeyboardInterrupt:
        session.loop.runtime.cancellation.cancel("keyboard_interrupt")
        session.loop.state = RuntimeState.INTERRUPTED
        return EXIT_INTERRUPTED
    finally:
        try:
            session.loop.close(reason="cli_exit")
        finally:
            session.store.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, validate without side effects, then run the selected CLI mode."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.resume is not None:
        parser.error("--resume is reserved for T11 and is not implemented")
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
        )
    except Exception as error:
        print(f"startup failure: {type(error).__name__}", file=sys.stderr)
        return EXIT_FAILED


__all__ = [
    "CliSession",
    "EXIT_CONFIG",
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_LIMIT_REACHED",
    "EXIT_SUCCESS",
    "assemble_session",
    "build_parser",
    "build_system_prompt",
    "exit_code_for",
    "main",
    "run_cli",
]
