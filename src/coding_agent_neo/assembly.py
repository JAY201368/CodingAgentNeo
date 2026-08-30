"""Assemble an in-process ``AgentBackend`` from resolved configuration.

The assembly layer is the only place that constructs the explicit system
prompt and the backend object graph (Runtime, Environment, Store, Loop,
Event Stream, Channel Approval Port). Frontends call
``build_local_backend`` and must not hold those objects.

Injectable timeouts (defaults are documented in ``backend.py``):

- ``approval_timeout_seconds``
- ``worker_shutdown_timeout_seconds``
- ``event_poll_timeout_seconds``

Tests may inject a fake ``model_client`` / ``environment`` and smaller
timeouts. Production callers omit those and receive Local Environment plus
the OpenAI-compatible client.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.backend import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    AgentBackend,
    ApprovalChannel,
    ChannelApprovalPort,
    EventStreamBuffer,
    LocalAgentBackend,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.events import EventDispatchError, EventEmitter, PendingEvent
from coding_agent_neo.model_client import OpenAICompatibleModelClient
from coding_agent_neo.models import AgentId, SessionId
from coding_agent_neo.policy import DefaultExecutionPolicy, NonInteractiveApprovalPort
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker
from coding_agent_neo.session import SessionStore
from coding_agent_neo.tools.registry import default_tool_registry

SYSTEM_PROMPT = """You are CodingAgentNeo, a local coding assistant. Work only on the user's task.
Use native tool calls to inspect, edit, and validate the configured workspace. Prefer small,
verifiable changes and report tests honestly. Structured file tools are workspace-bound. The bash
tool starts in the workspace but is not an operating-system sandbox and inherits the launching
user's permissions. Never reveal credentials or claim an unperformed action."""


def build_system_prompt(config: AppConfig) -> str:
    """Create the explicit prompt; Context Builder never discovers external prompt sources."""

    return f"{SYSTEM_PROMPT}\nConfigured logical workspace: {config.workspace.name or '.'}."


def build_local_backend(
    config: AppConfig,
    *,
    interactive: bool,
    model_client: Any | None = None,
    environment: Any | None = None,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    fsync: bool = True,
) -> AgentBackend:
    """Build the in-process backend. Frontends must not keep the inner objects."""

    if not isinstance(interactive, bool):
        raise TypeError("interactive must be a boolean")
    session_id = SessionId(f"session_{uuid4().hex}")
    agent_id = AgentId(f"agent_{uuid4().hex}")
    registry = default_tool_registry()
    selected_environment = environment or LocalExecutionEnvironment(
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
        environment=selected_environment,
        execution_policy=policy,
        budget=budget,
        active_tools=set(registry.active_names),
    )
    path = config.session_dir / f"{session_id}.jsonl"
    store = SessionStore(
        path,
        session_id,
        max_payload_bytes=config.session_output_limit,
        fsync=fsync,
    )
    stream = EventStreamBuffer()
    emitter = EventEmitter(store, [stream])

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
    approval_channel = ApprovalChannel()
    if interactive:
        approval_port: Any = ChannelApprovalPort(
            emitter,
            approval_channel,
            session_id=str(session_id),
            agent_id=str(agent_id),
            timeout_seconds=approval_timeout_seconds,
        )
    else:
        approval_port = NonInteractiveApprovalPort()
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
    return LocalAgentBackend(
        loop,
        store,
        event_stream=stream,
        approval_channel=approval_channel,
        worker_shutdown_timeout_seconds=worker_shutdown_timeout_seconds,
        event_poll_timeout_seconds=event_poll_timeout_seconds,
    )


__all__ = [
    "SYSTEM_PROMPT",
    "build_local_backend",
    "build_system_prompt",
]
