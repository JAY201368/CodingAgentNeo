"""One-call tool execution lifecycle.

``ToolExecutor`` is the boundary between a model tool call and the registry.
It owns the operation correlation ID, validates through the T04 registry,
applies the runtime policy, optionally asks an injected approval port, and
normalizes every outcome to one model-visible :class:`ToolResult`.

The module intentionally does not implement the T06 event emitter or session
store.  ``ToolLifecycleEvent`` and ``EventPublisher`` are the smallest
backend-neutral hand-off needed by those later components; a T06 adapter can
assign sequence numbers and persist the event without changing this module.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from coding_agent_neo.models import (
    AgentId,
    CorrelationId,
    EventEnvelope,
    EventId,
    EventType,
    ProviderToolCallId,
    SessionId,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    _timestamp_to_iso,
    new_id,
    utc_now,
)
from coding_agent_neo.policy import (
    ApprovalPort,
    ApprovalRequest,
    ExecutionPolicy,
    PolicyDecision,
    PolicyDecisionRecord,
    _invoke_approval_callable,
)
from coding_agent_neo.runtime import AgentRuntime, ToolExecutionContext
from coding_agent_neo.tools.output import project_tool_result
from coding_agent_neo.tools.registry import ToolRegistry
from coding_agent_neo.tools.schema import ProtocolErrorCode, ToolProtocolError

_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|passwd|api[_-]?key|authorization|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_SAFE_SKIP_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _json_safe(value: Any) -> Any:
    """Detach a value into ordinary JSON-compatible containers.

    Event payloads must not retain dataclass internals, mapping proxies or
    provider SDK objects.  Unknown values are represented by a type label;
    this is deliberately safer than calling an arbitrary object's ``str``.
    """

    if isinstance(value, Mapping):
        safe_mapping: dict[str, Any] = {}
        for key, item in value.items():
            rendered_key = str(key)
            safe_mapping[rendered_key] = (
                "<redacted>" if _SENSITIVE_KEY.search(rendered_key) else _json_safe(item)
            )
        return safe_mapping
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else None
    return f"<{type(value).__name__}>"


@dataclass(frozen=True, slots=True)
class ToolLifecycleEvent(Mapping[str, Any]):
    """Minimal event value handed to an injected publisher.

    T06 owns session sequence allocation, so ``sequence`` is optional here.
    Event IDs are nevertheless generated per event and ownership/correlation
    fields are explicit.  The object supports both attribute access and
    mapping-style access, making it convenient for a recorder or a future
    ``EventEnvelope`` adapter.
    """

    schema_version: int
    session_id: SessionId
    event_id: EventId
    agent_id: AgentId
    type: str
    timestamp: str
    correlation_id: CorrelationId
    provider_tool_call_id: ProviderToolCallId | None
    payload: Mapping[str, Any]
    parent_agent_id: AgentId | None = None
    sequence: int | None = None

    @property
    def event_type(self) -> str:
        return self.type

    @property
    def kind(self) -> str:
        return self.type

    @property
    def provider_id(self) -> ProviderToolCallId | None:
        return self.provider_tool_call_id

    @property
    def correlation(self) -> CorrelationId:
        return self.correlation_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": str(self.session_id),
            "event_id": str(self.event_id),
            "agent_id": str(self.agent_id),
            "parent_agent_id": (
                None if self.parent_agent_id is None else str(self.parent_agent_id)
            ),
            "sequence": self.sequence,
            "type": self.type,
            "correlation_id": str(self.correlation_id),
            "provider_tool_call_id": (
                None if self.provider_tool_call_id is None else str(self.provider_tool_call_id)
            ),
            "timestamp": self.timestamp,
            "payload": _json_safe(self.payload),
        }

    as_dict = to_dict

    def to_envelope(self, sequence: int) -> EventEnvelope:
        """Adapt to the canonical T02 envelope once T06 assigns a sequence."""

        return EventEnvelope(
            schema_version=self.schema_version,
            session_id=self.session_id,
            event_id=self.event_id,
            agent_id=self.agent_id,
            sequence=sequence,
            type=self.type,
            timestamp=self.timestamp,
            parent_agent_id=self.parent_agent_id,
            correlation_id=self.correlation_id,
            provider_tool_call_id=self.provider_tool_call_id,
            payload=self.payload,
        )

    as_envelope = to_envelope

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@runtime_checkable
class EventPublisher(Protocol):
    """Small event publication port owned by the executor boundary."""

    def publish(self, event: ToolLifecycleEvent) -> None:
        """Observe one event; sequence assignment/persistence is downstream."""


EventSink = EventPublisher
EventEmitter = EventPublisher


class ToolEventPublicationError(RuntimeError):
    """Strict publication failed without retaining an observer's message."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__("tool lifecycle event could not be published")


@dataclass(slots=True)
class EventRecorder:
    """A side-effect-free event publisher useful in tests and adapters."""

    events: list[ToolLifecycleEvent] = field(default_factory=list)

    def publish(self, event: ToolLifecycleEvent) -> None:
        self.events.append(event)

    emit = publish

    append = publish

    @property
    def records(self) -> list[ToolLifecycleEvent]:
        return self.events


InMemoryEventPublisher = EventRecorder
RecordingEventPublisher = EventRecorder
FakeEventPublisher = EventRecorder


_INVALID_TOOL_NAME = "<invalid-tool-name>"
_REDACTED_ARGUMENT_KEY = "<redacted>"


def _event_id(runtime: AgentRuntime) -> EventId:
    """Generate an event ID through the runtime's injectable factory.

    Event publication is observational, so a broken test or alternate ID
    factory must not make an otherwise valid tool call fail.  The UUID path is
    intentionally kept as a local fallback for that case.
    """

    try:
        return EventId(new_id(runtime.id_factory, "event"))
    except Exception:
        return EventId(f"event_{uuid4().hex}")


def _event_timestamp(runtime: AgentRuntime) -> str:
    try:
        value = runtime.clock()
        if isinstance(value, datetime):
            return _timestamp_to_iso(value)
        if isinstance(value, str):
            return _timestamp_to_iso(value)
    except Exception:
        pass
    return _timestamp_to_iso(utc_now())


def _event(
    runtime: AgentRuntime,
    event_type: EventType | str,
    *,
    correlation_id: CorrelationId,
    provider_tool_call_id: ProviderToolCallId | None,
    payload: Mapping[str, Any],
    event_id: EventId | None = None,
) -> ToolLifecycleEvent:
    event_name = event_type.value if isinstance(event_type, EventType) else str(event_type)
    return ToolLifecycleEvent(
        schema_version=1,
        session_id=SessionId(runtime.session_id),
        event_id=event_id if event_id is not None else _event_id(runtime),
        agent_id=AgentId(runtime.agent_id),
        parent_agent_id=(
            None if runtime.parent_agent_id is None else AgentId(runtime.parent_agent_id)
        ),
        type=event_name,
        timestamp=_event_timestamp(runtime),
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        payload=_json_safe(payload),
    )


def _safe_error_message(error: ToolProtocolError) -> str:
    """Keep protocol diagnostics useful without echoing argument contents."""

    # T04's standard messages are already value-free.  Custom validators can
    # raise arbitrary paths, so expose only the stable code and a root path.
    code = str(error.code)
    return f"invalid tool arguments ({code} at $)"


def _protocol_result(
    correlation_id: CorrelationId,
    provider_tool_call_id: ProviderToolCallId | None,
    *,
    code: ProtocolErrorCode | str,
    message: str,
    tool_name: str | None = None,
    duration_seconds: float | None = None,
) -> ToolResult:
    metadata: dict[str, Any] = {"error_code": str(code)}
    if tool_name is not None:
        metadata["tool_name"] = tool_name
    return ToolResult(
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        status=ToolResultStatus.INVALID,
        text=message,
        metadata=metadata,
        duration_seconds=duration_seconds,
    )


def _executor_error_result(
    correlation_id: CorrelationId,
    provider_tool_call_id: ProviderToolCallId | None,
    *,
    tool_name: str | None,
    code: str = "executor_error",
    duration_seconds: float | None = None,
) -> ToolResult:
    metadata: dict[str, Any] = {"error_code": code}
    if tool_name is not None:
        metadata["tool_name"] = tool_name
    return ToolResult(
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        status=ToolResultStatus.ERROR,
        text="tool execution failed",
        metadata=metadata,
        duration_seconds=duration_seconds,
    )


def _denied_result(
    correlation_id: CorrelationId,
    provider_tool_call_id: ProviderToolCallId | None,
    *,
    tool_name: str | None,
    code: str = "policy_denied",
    text: str = "tool execution denied",
    reason: str | None = None,
    duration_seconds: float | None = None,
) -> ToolResult:
    metadata: dict[str, Any] = {"error_code": code}
    if tool_name is not None:
        metadata["tool_name"] = tool_name
    if reason is not None:
        metadata["reason"] = reason
    return ToolResult(
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        status=ToolResultStatus.DENIED,
        text=text,
        metadata=metadata,
        duration_seconds=duration_seconds,
    )


def _coerce_policy_decision(value: Any) -> PolicyDecision | None:
    if isinstance(value, PolicyDecisionRecord):
        return PolicyDecision(value.decision)
    if isinstance(value, PolicyDecision):
        return value
    if isinstance(value, str):
        try:
            return PolicyDecision(value.lower())
        except ValueError:
            return None
    for attribute in ("decision", "action", "value"):
        candidate = getattr(value, attribute, None)
        if candidate is not None and candidate is not value:
            result = _coerce_policy_decision(candidate)
            if result is not None:
                return result
    return None


def _policy_callable(policy: Any) -> Callable[..., Any] | None:
    decide = getattr(policy, "decide", None)
    if callable(decide):
        return decide
    if callable(policy):
        return policy
    return None


def _call_policy(
    policy: Any,
    tool_name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> Any:
    decide = _policy_callable(policy)
    if decide is None:
        raise TypeError("execution policy must provide decide")
    try:
        signature = inspect.signature(decide)
    except (TypeError, ValueError):
        # Protocol-style policies use the context as an optional third
        # positional argument; opaque callables are given the same shape.
        return decide(tool_name, arguments, context)
    parameters = list(signature.parameters.values())
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    )
    if has_varargs or len(positional) >= 3:
        return decide(tool_name, arguments, context)
    if any(parameter.name == "context" for parameter in parameters):
        return decide(tool_name, arguments, context=context)
    if len(positional) >= 2:
        return decide(tool_name, arguments)
    if len(positional) == 1:
        return decide(tool_name)
    return decide()


def _approval_callable(port: Any) -> Callable[..., Any] | None:
    for name in ("request_approval", "approve", "confirm", "ask"):
        candidate = getattr(port, name, None)
        if callable(candidate):
            return candidate
    if callable(port):
        return port
    return None


def _safe_provider(value: Any) -> tuple[ProviderToolCallId | None, bool]:
    if value is None:
        return None, True
    try:
        return ProviderToolCallId(value), True
    except (TypeError, ValueError):
        return None, False


def _safe_name(value: Any, registry: Any | None = None) -> str:
    if not isinstance(value, str) or not value:
        return _INVALID_TOOL_NAME
    if registry is None:
        return value
    try:
        registered_names = getattr(registry, "registered_names", None)
        if registered_names is not None:
            return value if value in registered_names else _INVALID_TOOL_NAME
        registered = getattr(registry, "registered", None)
        if isinstance(registered, Mapping):
            return value if value in registered else _INVALID_TOOL_NAME
        getter = getattr(registry, "get", None)
        if callable(getter):
            return value if getter(value) is not None else _INVALID_TOOL_NAME
        contains = getattr(type(registry), "__contains__", None)
        if callable(contains):
            return value if contains(registry, value) else _INVALID_TOOL_NAME
    except Exception:
        pass
    # A registry with no introspection surface cannot establish that a name is
    # safe to expose, so fail closed at the diagnostic boundary.
    return _INVALID_TOOL_NAME


class ToolExecutor:
    """Execute one call through registry, policy, approval and events."""

    def __init__(
        self,
        runtime: AgentRuntime | None = None,
        registry: ToolRegistry | None = None,
        *,
        tool_registry: ToolRegistry | None = None,
        policy: ExecutionPolicy | Any | None = None,
        approval_port: ApprovalPort | Callable[..., bool] | Any | None = None,
        approval: ApprovalPort | Callable[..., bool] | Any | None = None,
        approver: ApprovalPort | Callable[..., bool] | Any | None = None,
        approval_callback: ApprovalPort | Callable[..., bool] | Any | None = None,
        interactive: bool | None = None,
        event_publisher: EventPublisher | Callable[[ToolLifecycleEvent], None] | Any | None = None,
        event_sink: EventPublisher | Callable[[ToolLifecycleEvent], None] | Any | None = None,
        event_emitter: EventPublisher | Callable[[ToolLifecycleEvent], None] | Any | None = None,
        publisher: EventPublisher | Callable[[ToolLifecycleEvent], None] | Any | None = None,
        events: EventPublisher | Callable[[ToolLifecycleEvent], None] | Any | None = None,
        id_factory: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        model_output_limit: int | None = None,
        output_limit: int | None = None,
        non_interactive: bool | None = None,
        strict_event_publishing: bool = False,
    ) -> None:
        # Accept the equally natural ``ToolExecutor(registry, runtime)``
        # ordering while keeping the documented runtime-first form.
        if not isinstance(runtime, AgentRuntime) and isinstance(registry, AgentRuntime):
            runtime, registry = registry, runtime  # type: ignore[assignment]
        if registry is not None and tool_registry is not None:
            raise ValueError("provide only one registry")
        registry = registry if registry is not None else tool_registry
        if approval_port is not None and approval is not None:
            raise ValueError("provide only one approval port")
        if approval_port is None:
            approval_port = approval
        if approval_port is not None and approver is not None:
            raise ValueError("provide only one approval port")
        if approval_port is None:
            approval_port = approver
        if approval_port is not None and approval_callback is not None:
            raise ValueError("provide only one approval port")
        if approval_port is None:
            approval_port = approval_callback
        selected_event_publisher = [
            item
            for item in (event_publisher, event_sink, event_emitter, publisher, events)
            if item is not None
        ]
        if len(selected_event_publisher) > 1:
            raise ValueError("provide only one event publisher")
        event_publisher = selected_event_publisher[0] if selected_event_publisher else None
        if runtime is None:
            raise TypeError("runtime is required")
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an AgentRuntime")
        if registry is None or not callable(getattr(registry, "validate", None)):
            raise TypeError("registry must provide validate")
        self.runtime = runtime
        self.registry = registry
        self.policy = policy if policy is not None else runtime.execution_policy
        self.approval_port = approval_port
        self.event_publisher = event_publisher
        if not isinstance(strict_event_publishing, bool):
            raise TypeError("strict_event_publishing must be a boolean")
        self.strict_event_publishing = strict_event_publishing
        self._id_factory_explicit = id_factory is not None
        self.id_factory = id_factory if id_factory is not None else runtime.id_factory
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.clock = clock
        if model_output_limit is not None and output_limit is not None:
            raise ValueError("use model_output_limit or output_limit, not both")
        self.model_output_limit = (
            model_output_limit if model_output_limit is not None else output_limit
        )
        if self.model_output_limit is not None:
            if isinstance(self.model_output_limit, bool) or not isinstance(
                self.model_output_limit, int
            ):
                raise TypeError("model_output_limit must be a non-negative integer or None")
            if self.model_output_limit < 0:
                raise ValueError("model_output_limit must be non-negative")
        if non_interactive is not None:
            if not isinstance(non_interactive, bool):
                raise TypeError("non_interactive must be a boolean or None")
            requested_interactive = not non_interactive
            if interactive is not None and interactive != requested_interactive:
                raise ValueError("interactive and non_interactive disagree")
            interactive = requested_interactive
        if interactive is None:
            port_interactive = getattr(self.approval_port, "interactive", None)
            policy_interactive = getattr(self.policy, "interactive", None)
            if isinstance(port_interactive, bool):
                interactive = port_interactive
            elif isinstance(policy_interactive, bool):
                interactive = policy_interactive
            else:
                # A missing approval port must never cause an implicit stdin
                # read.  A supplied callback is assumed interactive unless it
                # explicitly declares otherwise.
                interactive = self.approval_port is not None
        if not isinstance(interactive, bool):
            raise TypeError("interactive must be a boolean or None")
        self.interactive = interactive
        self._issued_correlations: set[CorrelationId] = set()
        self._issued_event_ids: set[EventId] = set()
        self.event_errors: list[str] = []
        self.last_result: ToolResult | None = None
        self.last_result_published = False

    @property
    def correlation_ids(self) -> frozenset[CorrelationId]:
        return frozenset(self._issued_correlations)

    def _new_correlation_id(self) -> CorrelationId:
        """Generate a unique semantic ID even for a poor test factory."""

        for attempt in range(16):
            try:
                candidate = CorrelationId(new_id(self.id_factory, "correlation"))
            except Exception:
                candidate = CorrelationId(f"correlation_{uuid4().hex}")
            if candidate not in self._issued_correlations:
                self._issued_correlations.add(candidate)
                return candidate
            # A deterministic factory may intentionally return the same value
            # more than once.  Keep the operation IDs distinct rather than
            # silently joining two lifecycle traces.
            suffix = attempt + 1
            try:
                candidate = CorrelationId(f"{candidate}_{suffix}")
            except ValueError:
                candidate = CorrelationId(f"correlation_{uuid4().hex}")
            if candidate not in self._issued_correlations:
                self._issued_correlations.add(candidate)
                return candidate
        candidate = CorrelationId(f"correlation_{uuid4().hex}")
        while candidate in self._issued_correlations:
            candidate = CorrelationId(f"correlation_{uuid4().hex}")
        self._issued_correlations.add(candidate)
        return candidate

    def _new_event_id(self) -> EventId:
        """Generate a unique event ID while tolerating deterministic factories."""

        candidate = _event_id(self.runtime)
        if candidate not in self._issued_event_ids:
            self._issued_event_ids.add(candidate)
            return candidate
        # A deterministic factory may intentionally return the same value for
        # every event.  Keep each lifecycle record distinct while retaining
        # the factory-produced value as the stable base.  Derive suffixes from
        # this one factory result so one lifecycle event consumes one ID call.
        for suffix in range(1, 17):
            try:
                suffixed = EventId(f"{candidate}_{suffix}")
            except (TypeError, ValueError):
                break
            if suffixed not in self._issued_event_ids:
                self._issued_event_ids.add(suffixed)
                return suffixed
        fallback = EventId(f"event_{uuid4().hex}")
        while fallback in self._issued_event_ids:
            fallback = EventId(f"event_{uuid4().hex}")
        self._issued_event_ids.add(fallback)
        return fallback

    def _publish(self, event: ToolLifecycleEvent) -> None:
        publisher = self.event_publisher
        if publisher is None:
            return
        try:
            publish = getattr(publisher, "publish", None)
            if not callable(publish):
                publish = getattr(publisher, "emit", None)
            if not callable(publish):
                publish = getattr(publisher, "record", None)
            if not callable(publish):
                publish = getattr(publisher, "on_event", None)
            if not callable(publish):
                publish = getattr(publisher, "record_event", None)
            if not callable(publish) and callable(publisher):
                publish = publisher
            if not callable(publish):
                append = getattr(publisher, "append", None)
                if callable(append):
                    append(event)
                    return
                raise TypeError("event publisher must provide publish")
            publish(event)
        except Exception as exc:
            # An observer must not cause duplicate execution/results.  Keep a
            # type-only diagnostic for callers that want to inspect failures;
            # do not expose the exception message or payload values.
            self.event_errors.append(type(exc).__name__)
            if self.strict_event_publishing:
                raise ToolEventPublicationError(type(exc).__name__) from None

    def _emit(
        self,
        event_type: EventType | str,
        correlation_id: CorrelationId,
        provider_tool_call_id: ProviderToolCallId | None,
        payload: Mapping[str, Any],
    ) -> None:
        try:
            event = _event(
                self.runtime,
                event_type,
                correlation_id=correlation_id,
                provider_tool_call_id=provider_tool_call_id,
                payload=payload,
                event_id=self._new_event_id(),
            )
        except Exception as exc:
            self.event_errors.append(type(exc).__name__)
            return
        self._publish(event)

    @staticmethod
    def _argument_keys(arguments: Any) -> list[str]:
        count = 0
        if isinstance(arguments, Mapping):
            try:
                count = len(arguments)
            except Exception:
                count = 0
        elif isinstance(arguments, str):
            try:
                decoded = json.loads(arguments)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
            if isinstance(decoded, Mapping):
                count = len(decoded)
        return [_REDACTED_ARGUMENT_KEY] * max(0, count)

    def _tool_call_event(
        self,
        name: str,
        arguments: Any,
        correlation_id: CorrelationId,
        provider_tool_call_id: ProviderToolCallId | None,
    ) -> None:
        self._emit(
            EventType.TOOL_CALL,
            correlation_id,
            provider_tool_call_id,
            {
                "tool_name": name,
                "name": name,
                "argument_keys": self._argument_keys(arguments),
                "arguments": {key: "<redacted>" for key in self._argument_keys(arguments)},
                "arguments_length": len(arguments) if isinstance(arguments, str) else None,
            },
        )

    def _policy_event(
        self,
        name: str,
        correlation_id: CorrelationId,
        provider_tool_call_id: ProviderToolCallId | None,
        *,
        requested: PolicyDecision,
        decision: PolicyDecision,
        approved: bool | None,
        reason: str,
    ) -> None:
        self._emit(
            EventType.POLICY_DECISION,
            correlation_id,
            provider_tool_call_id,
            {
                "tool_name": name,
                "name": name,
                "requested": requested.value,
                "requested_decision": requested.value,
                "decision": decision.value,
                "action": decision.value,
                "approved": approved,
                "reason": reason[:160],
            },
        )

    def _result_event(
        self,
        name: str,
        result: ToolResult,
    ) -> None:
        projected = project_tool_result(result, self.model_output_limit)
        result_data = projected.to_dict()
        self._emit(
            EventType.TOOL_RESULT,
            CorrelationId(result.correlation_id),
            (
                None
                if result.provider_tool_call_id is None
                else ProviderToolCallId(result.provider_tool_call_id)
            ),
            {
                "tool_name": name,
                "name": name,
                "status": str(result.status),
                "text": projected.text,
                "truncated": projected.truncated,
                "original_length": projected.original_length,
                "result": result_data,
                "tool_result": result_data,
            },
        )

    def _approval(self, request: ApprovalRequest) -> bool:
        port = self.approval_port
        if port is None:
            raise RuntimeError("approval port is unavailable")
        callback = _approval_callable(port)
        if callback is None:
            raise TypeError("approval port must provide request_approval")
        value = _invoke_approval_callable(callback, request)
        if not isinstance(value, bool):
            raise TypeError("approval response must be a boolean")
        return value

    def _normalize_result(
        self,
        result: Any,
        correlation_id: CorrelationId,
        provider_tool_call_id: ProviderToolCallId | None,
        *,
        name: str,
        duration_seconds: float,
    ) -> ToolResult:
        if not isinstance(result, ToolResult):
            return _executor_error_result(
                correlation_id,
                provider_tool_call_id,
                tool_name=name,
                code="invalid_tool_result",
                duration_seconds=duration_seconds,
            )
        # T04 already normalizes IDs, but the executor reasserts the boundary
        # so a custom registry cannot return a result tied to another call.
        try:
            if (
                result.correlation_id != correlation_id
                or result.provider_tool_call_id != provider_tool_call_id
            ):
                result = replace(
                    result,
                    correlation_id=correlation_id,
                    provider_tool_call_id=provider_tool_call_id,
                )
            if result.duration_seconds is None:
                result = replace(result, duration_seconds=max(0.0, duration_seconds))
        except Exception:
            return _executor_error_result(
                correlation_id,
                provider_tool_call_id,
                tool_name=name,
                code="malformed_tool_result",
                duration_seconds=duration_seconds,
            )
        # Registry's generic exception adapter includes the exception's text.
        # Keep only the stable type/code at this outer boundary.
        if (
            result.metadata.get("error_code") == str(ProtocolErrorCode.INTERNAL_TOOL_ERROR)
            or "error_type" in result.metadata
        ):
            metadata = {
                "error_code": str(
                    result.metadata.get("error_code", ProtocolErrorCode.INTERNAL_TOOL_ERROR)
                ),
                "tool_name": name,
            }
            result = replace(result, text="tool execution failed", metadata=metadata)
        safe_metadata = _json_safe(result.metadata)
        if safe_metadata != dict(result.metadata):
            result = replace(result, metadata=safe_metadata)
        if self.model_output_limit is not None:
            result = project_tool_result(result, self.model_output_limit).to_tool_result()
        return result

    def execute(
        self,
        tool_or_call: str | ToolCall,
        arguments: str | Mapping[str, Any] | None = None,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
        correlation_id: CorrelationId | str | None = None,
    ) -> ToolResult:
        """Run one tool call and return exactly one normalized result.

        For a ``ToolCall`` input the provider ID and raw arguments are reused.
        Its existing internal correlation ID is retained when no explicit ID
        factory is supplied; direct name/argument calls, or calls using an
        injected factory, receive a fresh executor-owned ID.
        """

        self.last_result = None
        self.last_result_published = False
        try:
            started = self.clock()
        except Exception:
            started = 0.0
        if isinstance(tool_or_call, ToolCall):
            name_value = tool_or_call.name
            raw_arguments: Any = tool_or_call.raw_arguments
            provider_value: Any = (
                provider_tool_call_id
                if provider_tool_call_id is not None
                else tool_or_call.provider_tool_call_id
            )
        else:
            name_value = tool_or_call
            raw_arguments = arguments if arguments is not None else ""
            provider_value = provider_tool_call_id
        name = _safe_name(name_value, self.registry)
        if correlation_id is None:
            # A normalized ToolCall may already carry the internal ID assigned
            # by an upstream loop.  Preserve that ID when the executor uses
            # the runtime's default factory; callers that inject a factory
            # explicitly ask the executor to own generation for every call.
            supplied_call_id = (
                tool_or_call.correlation_id
                if isinstance(tool_or_call, ToolCall) and not self._id_factory_explicit
                else None
            )
            if supplied_call_id is not None:
                try:
                    operation_id = CorrelationId(supplied_call_id)
                except (TypeError, ValueError):
                    operation_id = self._new_correlation_id()
                else:
                    if operation_id in self._issued_correlations:
                        operation_id = self._new_correlation_id()
                    else:
                        self._issued_correlations.add(operation_id)
            else:
                operation_id = self._new_correlation_id()
        else:
            try:
                operation_id = CorrelationId(correlation_id)
            except (TypeError, ValueError):
                operation_id = self._new_correlation_id()
            else:
                # Explicit IDs still cannot be reused silently.
                if operation_id in self._issued_correlations:
                    operation_id = self._new_correlation_id()
                else:
                    self._issued_correlations.add(operation_id)
        provider_id, provider_valid = _safe_provider(provider_value)
        self._tool_call_event(name, raw_arguments, operation_id, provider_id)
        result: ToolResult | None = None
        policy_emitted = False
        try:
            if not provider_valid:
                self._policy_event(
                    name,
                    operation_id,
                    None,
                    requested=PolicyDecision.DENY,
                    decision=PolicyDecision.DENY,
                    approved=None,
                    reason="invalid_provider_tool_call_id",
                )
                policy_emitted = True
                result = _protocol_result(
                    operation_id,
                    None,
                    code=ProtocolErrorCode.INVALID_VALUE,
                    message="invalid provider tool-call ID",
                    tool_name=name,
                )
            elif not isinstance(name_value, str) or not name_value:
                self._policy_event(
                    name,
                    operation_id,
                    provider_id,
                    requested=PolicyDecision.DENY,
                    decision=PolicyDecision.DENY,
                    approved=None,
                    reason="invalid_tool_name",
                )
                policy_emitted = True
                result = _protocol_result(
                    operation_id,
                    provider_id,
                    code=ProtocolErrorCode.UNKNOWN_TOOL,
                    message="tool name is required",
                    tool_name=None,
                )
            else:
                # Validate before policy and before constructing an execution
                # request.  The T04 registry guarantees this is side-effect
                # free and catches malformed JSON/schema arguments.
                try:
                    parsed = self.registry.validate(name_value, raw_arguments)
                    if not isinstance(parsed, Mapping):
                        raise ToolProtocolError(
                            ProtocolErrorCode.INVALID_VALUE,
                            "tool validator did not return an object",
                        )
                    parsed = dict(parsed)
                except ToolProtocolError as exc:
                    self._policy_event(
                        name,
                        operation_id,
                        provider_id,
                        requested=PolicyDecision.DENY,
                        decision=PolicyDecision.DENY,
                        approved=None,
                        reason=f"validation_{str(exc.code)}",
                    )
                    policy_emitted = True
                    result = _protocol_result(
                        operation_id,
                        provider_id,
                        code=exc.code,
                        message=_safe_error_message(exc),
                        tool_name=name,
                    )
                except Exception:
                    self._policy_event(
                        name,
                        operation_id,
                        provider_id,
                        requested=PolicyDecision.DENY,
                        decision=PolicyDecision.DENY,
                        approved=None,
                        reason="registry_validation_error",
                    )
                    policy_emitted = True
                    result = _executor_error_result(
                        operation_id,
                        provider_id,
                        tool_name=name,
                        code="registry_validation_error",
                    )

                if result is None:
                    context = ToolExecutionContext(
                        agent_id=self.runtime.agent_id,
                        correlation_id=operation_id,
                        provider_tool_call_id=provider_id,
                        environment=self.runtime.environment,
                        cancellation=self.runtime.cancellation,
                    )
                    try:
                        requested_value = _call_policy(self.policy, name_value, parsed, context)
                        requested = _coerce_policy_decision(requested_value)
                        if requested is None:
                            raise ValueError("invalid policy decision")
                        decision = requested
                        approved: bool | None = None
                        reason = f"policy_{requested.value}"
                        if requested is PolicyDecision.ASK:
                            port_interactive = getattr(self.approval_port, "interactive", None)
                            if not self.interactive or port_interactive is False:
                                decision = PolicyDecision.DENY
                                reason = "non_interactive_approval_required"
                            elif self.approval_port is None:
                                decision = PolicyDecision.DENY
                                reason = "approval_unavailable"
                            else:
                                request = ApprovalRequest(name_value, parsed, context)
                                try:
                                    approved = self._approval(request)
                                except Exception:
                                    approved = False
                                    decision = PolicyDecision.DENY
                                    reason = "approval_error"
                                else:
                                    decision = (
                                        PolicyDecision.ALLOW if approved else PolicyDecision.DENY
                                    )
                                    reason = "approved" if approved else "user_rejected"
                        elif requested is PolicyDecision.DENY:
                            reason = "policy_denied"
                        self._policy_event(
                            name,
                            operation_id,
                            provider_id,
                            requested=requested,
                            decision=decision,
                            approved=approved,
                            reason=reason,
                        )
                        policy_emitted = True
                        if decision is not PolicyDecision.ALLOW:
                            if reason == "user_rejected":
                                denied_text = "tool execution denied by user"
                            elif reason == "non_interactive_approval_required":
                                denied_text = "tool execution denied: approval is unavailable"
                            elif reason == "approval_error":
                                denied_text = "tool execution denied: approval failed"
                            elif reason == "policy_denied":
                                denied_text = "tool execution denied by policy"
                            else:
                                denied_text = "tool execution denied"
                            result = _denied_result(
                                operation_id,
                                provider_id,
                                tool_name=name,
                                text=denied_text,
                                reason=reason,
                            )
                        else:
                            result = self.registry.execute(name_value, parsed, context)
                    except Exception:
                        if not policy_emitted:
                            self._policy_event(
                                name,
                                operation_id,
                                provider_id,
                                requested=PolicyDecision.DENY,
                                decision=PolicyDecision.DENY,
                                approved=None,
                                reason="policy_error",
                            )
                            policy_emitted = True
                            result = _denied_result(
                                operation_id,
                                provider_id,
                                tool_name=name,
                                code="policy_error",
                                reason="policy_error",
                            )
                        else:
                            result = _executor_error_result(
                                operation_id,
                                provider_id,
                                tool_name=name,
                                code="executor_error",
                            )
        except Exception:
            if not policy_emitted:
                self._policy_event(
                    name,
                    operation_id,
                    provider_id,
                    requested=PolicyDecision.DENY,
                    decision=PolicyDecision.DENY,
                    approved=None,
                    reason="executor_error",
                )
            result = _executor_error_result(
                operation_id,
                provider_id,
                tool_name=name,
                code="executor_error",
            )
        finally:
            try:
                elapsed = max(0.0, self.clock() - started)
            except Exception:
                elapsed = 0.0
            if result is None:
                result = _executor_error_result(
                    operation_id,
                    provider_id,
                    tool_name=name,
                    code="executor_error",
                    duration_seconds=elapsed,
                )
            result = self._normalize_result(
                result,
                operation_id,
                provider_id,
                name=name,
                duration_seconds=elapsed,
            )
            self.last_result = result
            self._result_event(name, result)
            self.last_result_published = True
        # ``finally`` always assigns a ToolResult, but retaining this guard
        # keeps static type checkers and unusual interpreter exits honest.
        assert result is not None
        return result

    def skip(
        self,
        tool_name: str,
        arguments: str | Mapping[str, Any],
        *,
        reason: str,
        status: ToolResultStatus = ToolResultStatus.DENIED,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
        correlation_id: CorrelationId | str | None = None,
    ) -> ToolResult:
        """Publish one complete lifecycle without executing a Tool.

        Agent-level limits and cancellation can occur after an assistant
        response has declared several calls.  This method pairs each remaining
        declaration with explicit call/policy/result facts while guaranteeing
        that Registry dispatch and Environment side effects are not reached.
        """

        self.last_result = None
        self.last_result_published = False
        if not isinstance(reason, str) or _SAFE_SKIP_REASON.fullmatch(reason) is None:
            raise ValueError("skip reason must be a stable lowercase code")
        try:
            result_status = ToolResultStatus(status)
        except (TypeError, ValueError):
            raise ValueError("skip status must be a ToolResultStatus") from None
        if result_status is ToolResultStatus.SUCCESS:
            raise ValueError("a skipped tool call cannot have success status")

        name = _safe_name(tool_name, self.registry)
        if correlation_id is None:
            operation_id = self._new_correlation_id()
        else:
            try:
                operation_id = CorrelationId(correlation_id)
            except (TypeError, ValueError):
                operation_id = self._new_correlation_id()
            else:
                # ``skip`` completes a declaration whose correlation was
                # assigned before dispatch.  Preserve it even when an earlier
                # strict publication attempt reserved the same ID but failed.
                self._issued_correlations.add(operation_id)
        provider_id, _provider_valid = _safe_provider(provider_tool_call_id)
        safe_reason = reason[:160]
        self._tool_call_event(name, arguments, operation_id, provider_id)
        self._policy_event(
            name,
            operation_id,
            provider_id,
            requested=PolicyDecision.DENY,
            decision=PolicyDecision.DENY,
            approved=None,
            reason=f"not_executed_{safe_reason}"[:160],
        )
        result = ToolResult(
            correlation_id=operation_id,
            provider_tool_call_id=provider_id,
            status=result_status,
            text=f"tool call was not executed: {safe_reason}",
            metadata={
                "error_code": "not_executed",
                "reason": safe_reason,
                "executed": False,
                "tool_name": name,
            },
        )
        self.last_result = result
        self._result_event(name, result)
        self.last_result_published = True
        return result

    execute_tool = execute
    execute_call = execute
    run = execute
    invoke = execute
    dispatch = execute


ToolExecutionError = RuntimeError
Executor = ToolExecutor


__all__ = [
    "EventEmitter",
    "EventPublisher",
    "EventRecorder",
    "EventSink",
    "Executor",
    "FakeEventPublisher",
    "InMemoryEventPublisher",
    "RecordingEventPublisher",
    "ToolExecutionError",
    "ToolEventPublicationError",
    "ToolExecutor",
    "ToolLifecycleEvent",
]
