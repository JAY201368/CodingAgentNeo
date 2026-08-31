"""Assemble the shared backend service and explicit in-process binding.

The assembly layer is the only place that constructs the explicit system
prompt and the backend object graph (Runtime, Environment, Store, Loop,
Event Stream, Channel Approval Port).  ``build_agent_backend`` returns the
shared service port; ``build_in_process_adapter`` wraps it in the thin Python
binding used by the CLI.  Frontends must not hold the inner objects.

Injectable timeouts (defaults are documented in ``backend.py``):

- ``approval_timeout_seconds``
- ``worker_shutdown_timeout_seconds``
- ``event_poll_timeout_seconds``

Tests may inject a fake ``model_client`` / ``environment`` and smaller
timeouts. Production callers omit those and receive Local Environment plus
the OpenAI-compatible client.

Linear session resume is also owned here: ``resume`` selects a JSONL file,
rebuilds root Runtime state, and opens the same Store for sequence
continuation. Historical tool side effects are never replayed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from coding_agent_neo.agent_loop import AgentLoop
from coding_agent_neo.backend import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    AgentBackend,
)
from coding_agent_neo.backend_service import (
    AgentBackendService,
    ApprovalChannel,
    ChannelApprovalPort,
    EventStreamBuffer,
)
from coding_agent_neo.config import AppConfig, ConfigError
from coding_agent_neo.context import (
    DEGRADED_CONTEXT_NOTICE,
    ContextIntegrityError,
    MessageGroup,
    detach_message,
)
from coding_agent_neo.environment.local import LocalExecutionEnvironment
from coding_agent_neo.events import EventDispatchError, EventEmitter, PendingEvent
from coding_agent_neo.model_client import OpenAICompatibleModelClient
from coding_agent_neo.models import AgentId, EventEnvelope, EventType, SessionId
from coding_agent_neo.policy import DefaultExecutionPolicy, NonInteractiveApprovalPort
from coding_agent_neo.runtime import AgentRuntime, BudgetTracker, ContextState
from coding_agent_neo.session import (
    SessionDiagnostic,
    SessionError,
    SessionFormatError,
    SessionStore,
    discard_incomplete_tail,
    read_session,
    resolve_resume_path,
)
from coding_agent_neo.tools.registry import default_tool_registry
from coding_agent_neo.transports.in_process import InProcessAdapter

SYSTEM_PROMPT = """You are CodingAgentNeo, a local coding assistant. Work only on the user's task.
Use native tool calls to inspect, edit, and validate the configured workspace. Prefer small,
verifiable changes and report tests honestly. Structured file tools are workspace-bound. The bash
tool starts in the workspace but is not an operating-system sandbox and inherits the launching
user's permissions. Never reveal credentials or claim an unperformed action."""


class AgentBackendFactory(Protocol):
    """Composition seam shared by adapters that need a backend port."""

    def __call__(
        self,
        config: AppConfig,
        *,
        interactive: bool,
        resume: str | os.PathLike[str] | None = None,
        model_client: Any | None = None,
        environment: Any | None = None,
        approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
        fsync: bool = True,
    ) -> AgentBackend: ...


_INVALID_PROVIDER_DIAGNOSTICS = frozenset(
    {
        "missing_tool_call_id",
        "invalid_tool_call_id",
        "duplicate_tool_call_id",
    }
)
_PROTOCOL_ERROR_NOTICE = (
    "Protocol error: return non-empty assistant text or one or more "
    "native tool calls. Please correct the response."
)


class SessionResumeError(RuntimeError):
    """The selected session cannot be resumed as a linear root Agent."""


@dataclass(frozen=True, slots=True)
class SessionResumePlan:
    """Rebuilt root-agent facts used to assemble a resumed backend."""

    path: Path
    session_id: SessionId
    agent_id: AgentId
    active_tools: frozenset[str]
    model_steps: int
    tool_calls: int
    protocol_errors: int
    input_tokens: int
    output_tokens: int
    latest_summary: str | None
    covered_through_sequence: int
    degraded_through_sequence: int
    degraded_notice: str | None
    messages: tuple[tuple[Mapping[str, Any], int], ...]
    diagnostics: tuple[SessionDiagnostic, ...]
    last_sequence: int


def build_system_prompt(config: AppConfig) -> str:
    """Create the explicit prompt; Context Builder never discovers external prompt sources."""

    return f"{SYSTEM_PROMPT}\nConfigured logical workspace: {config.workspace.name or '.'}."


def _payload(event: EventEnvelope) -> Mapping[str, Any]:
    payload = event.payload
    return payload if isinstance(payload, Mapping) else {}


def _context_tool_call_id(call: Mapping[str, Any]) -> str | None:
    diagnostics = tuple(call.get("diagnostics") or ())
    provider = call.get("provider_tool_call_id")
    correlation = call.get("correlation_id")
    if (
        isinstance(provider, str)
        and provider
        and not any(code in _INVALID_PROVIDER_DIAGNOSTICS for code in diagnostics)
    ):
        return provider
    if isinstance(correlation, str) and correlation:
        return correlation
    if isinstance(provider, str) and provider:
        return provider
    return None


def _assistant_context_message(event: EventEnvelope) -> dict[str, Any]:
    payload = _payload(event)
    text = payload.get("text")
    message: dict[str, Any] = {"role": "assistant", "content": "" if text is None else str(text)}
    raw_calls = payload.get("tool_calls") or ()
    if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
        return message
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, Mapping):
            continue
        context_id = _context_tool_call_id(call)
        if context_id is None:
            continue
        name = call.get("name") or "invalid_tool_name"
        if name == "<invalid-tool-name>":
            name = "invalid_tool_name"
        arguments = call.get("raw_arguments")
        tool_calls.append(
            {
                "id": context_id,
                "type": "function",
                "function": {
                    "name": str(name),
                    "arguments": "" if arguments is None else str(arguments),
                },
            }
        )
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_context_message(event: EventEnvelope, tool_call_id: str) -> dict[str, Any]:
    payload = _payload(event)
    result_data = payload.get("result")
    if not isinstance(result_data, Mapping):
        result_data = payload.get("tool_result")
    if isinstance(result_data, Mapping):
        content = json.dumps(
            dict(result_data),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    else:
        content = str(payload.get("text") or "")
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _complete_message_groups(
    pairs: Sequence[tuple[Mapping[str, Any], int]],
) -> tuple[MessageGroup, ...]:
    """Group projected messages, ignoring only a trailing incomplete tool group."""

    if not pairs:
        return ()
    detached = [detach_message(message) for message, _sequence in pairs]
    sequences = [sequence for _message, sequence in pairs]
    groups: list[MessageGroup] = []
    index = 0
    while index < len(detached):
        message = detached[index]
        role = message.get("role")
        if role == "tool":
            if index == 0:
                raise SessionResumeError("session context has an orphaned tool result")
            raise SessionResumeError("session context splits a tool interaction")
        raw_calls = message.get("tool_calls") if role == "assistant" else None
        expected_ids: tuple[str, ...] = ()
        if raw_calls:
            try:
                expected_ids = tuple(
                    str(call["id"])
                    for call in raw_calls
                    if isinstance(call, Mapping) and call.get("id")
                )
            except (TypeError, KeyError) as error:
                raise SessionResumeError("session assistant tool calls are invalid") from error
        if not expected_ids:
            groups.append(MessageGroup((message,), (sequences[index],)))
            index += 1
            continue
        grouped_messages: list[Mapping[str, Any]] = [message]
        grouped_sequences = [sequences[index]]
        seen_ids: set[str] = set()
        index += 1
        while index < len(detached) and len(seen_ids) < len(expected_ids):
            result = detached[index]
            if result.get("role") != "tool":
                break
            result_id = result.get("tool_call_id")
            if not isinstance(result_id, str) or result_id not in expected_ids:
                raise SessionResumeError("session tool result does not match its assistant call")
            if result_id in seen_ids:
                raise SessionResumeError("session tool call has more than one result")
            seen_ids.add(result_id)
            grouped_messages.append(result)
            grouped_sequences.append(sequences[index])
            index += 1
        if seen_ids != set(expected_ids):
            if index >= len(detached):
                break
            raise SessionResumeError("session assistant tool calls are missing results")
        groups.append(MessageGroup(tuple(grouped_messages), tuple(grouped_sequences)))
    return tuple(groups)


def _locate_resume_file(config: AppConfig, resume: str | os.PathLike[str]) -> Path:
    try:
        path = resolve_resume_path(resume, config.session_dir)
    except ValueError as error:
        raise ConfigError("resume target is invalid") from error
    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
    except OSError as error:
        raise ConfigError("session file could not be read") from error
    if not exists:
        raise ConfigError("session file was not found")
    if not is_file:
        raise ConfigError("resume target is not a session file")
    return path


def _root_agent(events: Sequence[EventEnvelope]) -> tuple[SessionId, AgentId, frozenset[str]]:
    session_id = events[0].session_id
    root: EventEnvelope | None = None
    active: frozenset[str] = frozenset()
    for event in events:
        if str(event.type) != EventType.AGENT_START.value:
            continue
        if event.parent_agent_id is not None:
            continue
        root = event
        payload = _payload(event)
        tools = payload.get("active_tools")
        if isinstance(tools, Sequence) and not isinstance(tools, (str, bytes)):
            names = [str(name) for name in tools if isinstance(name, str) and name]
            if names:
                active = frozenset(names)
    if root is None:
        raise SessionResumeError("session is missing a root agent_start")
    return session_id, root.agent_id, active


def _budget_from_events(
    events: Sequence[EventEnvelope],
    agent_id: AgentId,
) -> tuple[int, int, int, int, int]:
    snapshot: Mapping[str, Any] | None = None
    model_steps = 0
    tool_calls = 0
    protocol_errors = 0
    input_tokens = 0
    output_tokens = 0
    for event in events:
        if event.agent_id != agent_id:
            continue
        payload = _payload(event)
        name = str(event.type)
        if name == EventType.ASSISTANT_MESSAGE.value:
            model_steps += 1
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0)
        elif name == EventType.COMPACTION.value:
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0)
        elif name == EventType.TOOL_RESULT.value:
            result = payload.get("result")
            metadata = result.get("metadata") if isinstance(result, Mapping) else None
            executed = True if not isinstance(metadata, Mapping) else metadata.get("executed", True)
            if executed is not False:
                tool_calls += 1
        elif name == EventType.ERROR.value and payload.get("reason") == "empty_assistant_response":
            protocol_errors = int(payload.get("consecutive_protocol_errors") or protocol_errors + 1)
        budget = payload.get("budget")
        if isinstance(budget, Mapping) and name in {
            EventType.TURN_END.value,
            EventType.AGENT_END.value,
            EventType.SESSION_END.value,
        }:
            snapshot = budget
    if snapshot is not None:
        return (
            int(snapshot.get("model_steps") or 0),
            int(snapshot.get("tool_calls") or 0),
            int(snapshot.get("protocol_errors") or 0),
            int(snapshot.get("input_tokens") or 0),
            int(snapshot.get("output_tokens") or 0),
        )
    return model_steps, tool_calls, protocol_errors, input_tokens, output_tokens


def _context_from_events(
    events: Sequence[EventEnvelope],
    agent_id: AgentId,
) -> tuple[str | None, int, int, str | None, tuple[tuple[Mapping[str, Any], int], ...]]:
    summary: str | None = None
    covered = 0
    degraded = 0
    notice: str | None = None
    pairs: list[tuple[Mapping[str, Any], int]] = []
    pending_ids: dict[str, str] = {}
    for event in events:
        if event.agent_id != agent_id:
            continue
        name = str(event.type)
        payload = _payload(event)
        if name == EventType.COMPACTION.value:
            status = payload.get("status")
            if status == "success":
                text = payload.get("summary")
                summary = None if text is None else str(text)
                covered = int(payload.get("covered_through_sequence") or 0)
                degraded = 0
                notice = None
            else:
                degraded = int(payload.get("degraded_through_sequence") or degraded)
                notice = DEGRADED_CONTEXT_NOTICE
            continue
        if name == EventType.USER_MESSAGE.value:
            pairs.append(
                (
                    {"role": "user", "content": str(payload.get("text") or "")},
                    event.sequence,
                )
            )
            continue
        if name == EventType.ASSISTANT_MESSAGE.value:
            message = _assistant_context_message(event)
            pending_ids = {}
            for call in _payload(event).get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                correlation = call.get("correlation_id")
                context_id = _context_tool_call_id(call)
                if isinstance(correlation, str) and context_id is not None:
                    pending_ids[correlation] = context_id
            pairs.append((message, event.sequence))
            continue
        if name == EventType.TOOL_RESULT.value:
            correlation = None if event.correlation_id is None else str(event.correlation_id)
            context_id = pending_ids.get(correlation or "") if correlation else None
            if context_id is None and event.provider_tool_call_id is not None:
                context_id = str(event.provider_tool_call_id)
            if context_id is None and correlation:
                context_id = correlation
            if context_id is None:
                continue
            pairs.append((_tool_context_message(event, context_id), event.sequence))
            continue
        if name == EventType.ERROR.value and payload.get("reason") == "empty_assistant_response":
            pairs.append(({"role": "system", "content": _PROTOCOL_ERROR_NOTICE}, event.sequence))

    cutoff = max(covered, degraded)
    remaining = [(message, sequence) for message, sequence in pairs if sequence > cutoff]
    try:
        groups = _complete_message_groups(remaining)
    except ContextIntegrityError as error:
        raise SessionResumeError("session context cannot be grouped") from error
    flattened: list[tuple[Mapping[str, Any], int]] = []
    for group in groups:
        flattened.extend(zip(group.messages, group.sequences, strict=True))
    return summary, covered, degraded, notice, tuple(flattened)


def recover_session_plan(path: Path) -> SessionResumePlan:
    """Read one JSONL session and rebuild root Runtime facts without side effects."""

    try:
        result = read_session(path)
    except SessionFormatError:
        raise
    except SessionError as error:
        raise ConfigError("session file could not be read") from error
    if not result.events:
        raise SessionResumeError("session file is empty")
    session_id, agent_id, active_tools = _root_agent(result.events)
    model_steps, tool_calls, protocol_errors, input_tokens, output_tokens = _budget_from_events(
        result.events,
        agent_id,
    )
    summary, covered, degraded, notice, messages = _context_from_events(result.events, agent_id)
    last_sequence = result.last_valid_sequence
    if last_sequence is None:
        raise SessionResumeError("session file is empty")
    return SessionResumePlan(
        path=path,
        session_id=session_id,
        agent_id=agent_id,
        active_tools=active_tools,
        model_steps=model_steps,
        tool_calls=tool_calls,
        protocol_errors=protocol_errors,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latest_summary=summary,
        covered_through_sequence=covered,
        degraded_through_sequence=degraded,
        degraded_notice=notice,
        messages=messages,
        diagnostics=result.diagnostics,
        last_sequence=last_sequence,
    )


def _context_state_from_plan(plan: SessionResumePlan) -> ContextState:
    state = ContextState(
        latest_summary=plan.latest_summary,
        covered_through_sequence=plan.covered_through_sequence,
        degraded_through_sequence=plan.degraded_through_sequence,
        degraded_notice=plan.degraded_notice,
    )
    for message, sequence in plan.messages:
        state.append_message(message, sequence=sequence)
    return state


def build_agent_backend(
    config: AppConfig,
    *,
    interactive: bool,
    resume: str | os.PathLike[str] | None = None,
    model_client: Any | None = None,
    environment: Any | None = None,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    fsync: bool = True,
) -> AgentBackendService:
    """Build the shared ``AgentBackendService`` from resolved configuration.

    ``model_client``, ``environment``, timeout, and ``fsync`` parameters are
    test/embedded seams.  Normal frontends should use
    :func:`build_in_process_adapter` and leave them at their defaults.
    """

    if not isinstance(interactive, bool):
        raise TypeError("interactive must be a boolean")

    resume_plan: SessionResumePlan | None = None
    if resume is not None:
        resume_path = _locate_resume_file(config, resume)
        resume_plan = recover_session_plan(resume_path)
        if resume_plan.diagnostics:
            discard_incomplete_tail(resume_path)
        session_id = resume_plan.session_id
        agent_id = resume_plan.agent_id
        path = resume_plan.path
    else:
        session_id = SessionId(f"session_{uuid4().hex}")
        agent_id = AgentId(f"agent_{uuid4().hex}")
        path = config.session_dir / f"{session_id}.jsonl"

    registry = default_tool_registry()
    if resume_plan is not None and resume_plan.active_tools:
        unknown = sorted(resume_plan.active_tools - set(registry.registered_names))
        if unknown:
            raise SessionResumeError("session active tools are not registered")
        registry.set_active(resume_plan.active_tools)
        active_tools = set(resume_plan.active_tools)
    else:
        active_tools = set(registry.active_names)

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
        model_steps=0 if resume_plan is None else resume_plan.model_steps,
        tool_calls=0 if resume_plan is None else resume_plan.tool_calls,
        protocol_errors=0 if resume_plan is None else resume_plan.protocol_errors,
        input_tokens=0 if resume_plan is None else resume_plan.input_tokens,
        output_tokens=0 if resume_plan is None else resume_plan.output_tokens,
    )
    runtime = AgentRuntime(
        agent_id=agent_id,
        session_id=session_id,
        environment=selected_environment,
        execution_policy=policy,
        budget=budget,
        active_tools=active_tools,
        context_state=(
            ContextState() if resume_plan is None else _context_state_from_plan(resume_plan)
        ),
    )
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
    return AgentBackendService(
        loop,
        store,
        event_stream=stream,
        approval_channel=approval_channel,
        worker_shutdown_timeout_seconds=worker_shutdown_timeout_seconds,
        event_poll_timeout_seconds=event_poll_timeout_seconds,
        resume_diagnostics=() if resume_plan is None else resume_plan.diagnostics,
        resume_last_sequence=0 if resume_plan is None else resume_plan.last_sequence,
    )


def build_in_process_adapter(
    config: AppConfig,
    *,
    interactive: bool,
    resume: str | os.PathLike[str] | None = None,
    model_client: Any | None = None,
    environment: Any | None = None,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    fsync: bool = True,
) -> InProcessAdapter:
    """Build the CLI's explicit in-process adapter composition.

    The shared backend factory is invoked first and the returned port is then
    injected into a thin :class:`InProcessAdapter`.  Keeping this wrapper in
    the composition root prevents CLI callers from depending on service
    implementation objects.
    """

    backend = build_agent_backend(
        config,
        interactive=interactive,
        resume=resume,
        model_client=model_client,
        environment=environment,
        approval_timeout_seconds=approval_timeout_seconds,
        worker_shutdown_timeout_seconds=worker_shutdown_timeout_seconds,
        event_poll_timeout_seconds=event_poll_timeout_seconds,
        fsync=fsync,
    )
    return InProcessAdapter(backend)


def build_local_backend(
    config: AppConfig,
    *,
    interactive: bool,
    resume: str | os.PathLike[str] | None = None,
    model_client: Any | None = None,
    environment: Any | None = None,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    fsync: bool = True,
) -> AgentBackend:
    """Compatibility facade for baseline callers.

    This legacy name intentionally returns the same ``AgentBackendService``
    produced by the shared factory; it does not maintain a second backend
    implementation.  New CLI/composition callers should use
    :func:`build_in_process_adapter` to make the binding explicit.
    """

    return build_agent_backend(
        config,
        interactive=interactive,
        resume=resume,
        model_client=model_client,
        environment=environment,
        approval_timeout_seconds=approval_timeout_seconds,
        worker_shutdown_timeout_seconds=worker_shutdown_timeout_seconds,
        event_poll_timeout_seconds=event_poll_timeout_seconds,
        fsync=fsync,
    )


__all__ = [
    "AgentBackendFactory",
    "SYSTEM_PROMPT",
    "SessionResumeError",
    "SessionResumePlan",
    "build_agent_backend",
    "build_in_process_adapter",
    "build_local_backend",
    "build_system_prompt",
    "recover_session_plan",
]
