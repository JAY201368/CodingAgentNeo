"""Canonical runtime events and synchronous subscriber fan-out.

Event producers publish :class:`PendingEvent`, an existing
:class:`~coding_agent_neo.models.EventEnvelope`, or the T05 executor's
``ToolLifecycleEvent`` shape.  A :class:`SessionStore`-compatible primary
subscriber assigns the session sequence and returns the one canonical
envelope that every other subscriber observes.

Payload conversion is intentionally conservative: only JSON primitives and
ordinary mappings/collections are traversed.  Unknown objects are replaced by
a fixed marker without calling their ``str`` or ``repr`` implementation.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from coding_agent_neo.models import (
    AgentId,
    CorrelationId,
    EventEnvelope,
    EventId,
    EventType,
    IdFactoryLike,
    ProviderToolCallId,
    SessionId,
    UUIDIdFactory,
    new_id,
    utc_now,
)

SCHEMA_VERSION = 1
REDACTED = "<redacted>"
UNSUPPORTED_OBJECT = "<unsupported-object>"
MAX_SAFE_DEPTH = 32

_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|passwd|api[_-]?key|authorization|credential|cookie|"
    r"private[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)
_NON_SECRET_TOKEN_METRICS = frozenset(
    {
        "accepted_prediction_tokens",
        "audio_tokens",
        "cached_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "rejected_prediction_tokens",
        "token_budget",
        "token_count",
        "token_limit",
        "tokens_remaining",
        "tokens_used",
        "total_tokens",
    }
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|authorization)\s*([:=])\s*([^\s,;]+)"
)


class EventSerializationError(ValueError):
    """An event could not be converted without crossing the safe boundary."""


def _redact_string(value: str) -> str:
    """Redact common inline credential forms without logging their values."""

    value = _BEARER_VALUE.sub("Bearer <redacted>", value)
    return _ASSIGNMENT_SECRET.sub(r"\1\2<redacted>", value)


def _is_sensitive_key(value: str) -> bool:
    """Distinguish credential-bearing token fields from usage counters."""

    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if normalized in _NON_SECRET_TOKEN_METRICS:
        return False
    return _SENSITIVE_KEY.search(value) is not None


def safe_json_value(value: Any, *, _seen: set[int] | None = None, _depth: int = 0) -> Any:
    """Return a detached, redacted JSON-compatible representation.

    The function never calls ``str`` or ``repr`` on an unsupported value.
    Cycles and overly deep containers receive the same fixed unsupported
    marker, keeping serialization bounded and diagnostic-safe.
    """

    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if _depth >= MAX_SAFE_DEPTH:
        return UNSUPPORTED_OBJECT

    seen = set() if _seen is None else _seen
    identity = id(value)
    if identity in seen:
        return UNSUPPORTED_OBJECT

    if isinstance(value, Mapping):
        seen.add(identity)
        try:
            result: dict[str, Any] = {}
            unsupported_index = 0
            for key, item in value.items():
                if isinstance(key, str):
                    rendered_key = key
                    unsupported_key = False
                else:
                    rendered_key = f"<unsupported-key-{unsupported_index}>"
                    unsupported_index += 1
                    unsupported_key = True
                if rendered_key in result:
                    rendered_key = f"<duplicate-key-{len(result)}>"
                if unsupported_key or _is_sensitive_key(rendered_key):
                    result[rendered_key] = REDACTED
                else:
                    result[rendered_key] = safe_json_value(
                        item,
                        _seen=seen,
                        _depth=_depth + 1,
                    )
            return result
        except Exception:
            raise EventSerializationError("event mapping could not be serialized safely") from None
        finally:
            seen.discard(identity)

    if isinstance(value, (list, tuple)):
        seen.add(identity)
        try:
            return [safe_json_value(item, _seen=seen, _depth=_depth + 1) for item in value]
        except Exception:
            raise EventSerializationError(
                "event collection could not be serialized safely"
            ) from None
        finally:
            seen.discard(identity)

    if isinstance(value, (set, frozenset)):
        seen.add(identity)
        try:
            safe_items = [safe_json_value(item, _seen=seen, _depth=_depth + 1) for item in value]
            return sorted(safe_items, key=_canonical_json)
        except Exception:
            raise EventSerializationError(
                "event collection could not be serialized safely"
            ) from None
        finally:
            seen.discard(identity)

    # Bytes and arbitrary provider SDK objects deliberately take the same
    # content-free path.  Even a hostile ``__str__``/``__repr__`` is never run.
    return UNSUPPORTED_OBJECT


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """A standard event before the Session Store assigns its sequence."""

    session_id: SessionId | str
    agent_id: AgentId | str
    type: str | EventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: EventId | str | None = None
    timestamp: datetime | str | None = None
    parent_agent_id: AgentId | str | None = None
    correlation_id: CorrelationId | str | None = None
    provider_tool_call_id: ProviderToolCallId | str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("only event schema version 1 is supported")

    @property
    def event_type(self) -> str:
        return str(self.type)


# Short aliases keep later assembly code readable without creating another
# schema.  ``PendingEvent`` remains the descriptive canonical spelling.
Event = PendingEvent
EventRecord = PendingEvent


def _field(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def has_explicit_event_id(event: Any) -> bool:
    """Return whether the producer supplied a stable event ID."""

    return _field(event, "event_id") is not None


def canonicalize_event(
    event: PendingEvent | EventEnvelope | Mapping[str, Any] | Any,
    *,
    sequence: int,
    id_factory: IdFactoryLike | None = None,
    clock: Callable[[], datetime] = utc_now,
    event_id: EventId | str | None = None,
) -> EventEnvelope:
    """Adapt a standard or lifecycle event into one schema-v1 envelope."""

    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("sequence must be a non-negative integer")
    schema_version = _field(event, "schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise ValueError("only event schema version 1 is supported")

    session_id = _field(event, "session_id")
    agent_id = _field(event, "agent_id")
    event_type = _field(event, "type", _field(event, "event_type"))
    payload = _field(event, "payload", {})
    if session_id is None or agent_id is None or event_type is None:
        raise ValueError("event requires session_id, agent_id and type")
    if not isinstance(payload, Mapping):
        raise TypeError("event payload must be a mapping")

    selected_event_id = event_id if event_id is not None else _field(event, "event_id")
    if selected_event_id is None:
        factory = UUIDIdFactory() if id_factory is None else id_factory
        selected_event_id = EventId(new_id(factory, "event"))
    timestamp = _field(event, "timestamp")
    if timestamp is None:
        timestamp = clock()

    try:
        safe_payload = safe_json_value(payload)
    except EventSerializationError:
        raise
    except Exception:
        raise EventSerializationError("event payload could not be serialized safely") from None
    if not isinstance(safe_payload, Mapping):
        raise EventSerializationError("event payload must serialize to an object")

    return EventEnvelope(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        event_id=selected_event_id,
        agent_id=agent_id,
        sequence=sequence,
        type=event_type,
        timestamp=timestamp,
        parent_agent_id=_field(event, "parent_agent_id"),
        correlation_id=_field(event, "correlation_id"),
        provider_tool_call_id=_field(event, "provider_tool_call_id"),
        payload=safe_payload,
    )


@runtime_checkable
class EventSubscriber(Protocol):
    """A synchronous observer of canonical event envelopes."""

    def publish(self, event: EventEnvelope) -> None:
        """Handle one canonical event."""


@runtime_checkable
class SequenceStore(Protocol):
    """Primary subscriber that assigns sequence and persists the event."""

    def append(self, event: Any) -> EventEnvelope:
        """Persist and return the canonical event envelope."""


class DeliveryStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SubscriberDelivery:
    """One subscriber's explicit processing result."""

    name: str
    status: DeliveryStatus
    error_type: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is DeliveryStatus.SUCCESS

    @property
    def attempted(self) -> bool:
        return self.status is not DeliveryStatus.SKIPPED


@dataclass(frozen=True, slots=True)
class EmissionReport:
    """Canonical event plus the result of every configured delivery."""

    event: EventEnvelope | None
    deliveries: tuple[SubscriberDelivery, ...]

    @property
    def succeeded(self) -> bool:
        return bool(self.deliveries) and all(item.succeeded for item in self.deliveries)

    @property
    def ok(self) -> bool:
        return self.succeeded

    @property
    def failures(self) -> tuple[SubscriberDelivery, ...]:
        return tuple(item for item in self.deliveries if not item.succeeded)


class EventDispatchError(RuntimeError):
    """Raised after fan-out when one or more deliveries did not succeed."""

    def __init__(self, report: EmissionReport) -> None:
        super().__init__("one or more event subscribers did not process the event")
        self.report = report


def _subscriber_name(subscriber: Any, index: int, explicit: str | None = None) -> str:
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError("subscriber name must be a non-empty string")
        return explicit
    class_name = type(subscriber).__name__
    return f"{class_name or 'subscriber'}[{index}]"


def _notify(subscriber: Any, event: EventEnvelope) -> None:
    for method_name in (
        "handle_event",
        "on_event",
        "publish",
        "emit",
        "render_event",
        "render",
        "record_event",
        "append",
    ):
        method = getattr(subscriber, method_name, None)
        if callable(method):
            method(event)
            return
    if callable(subscriber):
        subscriber(event)
        return
    raise TypeError("event subscriber has no supported handler")


class EventEmitter:
    """Persist one event, then synchronously fan it out to all observers.

    The store is deliberately first and unique because it owns sequence
    allocation.  Observer failures are collected while remaining observers
    are still attempted; an aggregate :class:`EventDispatchError` exposes the
    complete report after fan-out.
    """

    def __init__(
        self,
        store: SequenceStore,
        subscribers: Iterable[Any] | None = None,
    ) -> None:
        if not callable(getattr(store, "append", None)):
            raise TypeError("store must provide append")
        self.store = store
        self._subscribers: list[tuple[str, Any]] = []
        self.last_report: EmissionReport | None = None
        if subscribers is not None:
            for subscriber in subscribers:
                self.subscribe(subscriber)

    @property
    def subscribers(self) -> tuple[Any, ...]:
        return (self.store, *(subscriber for _, subscriber in self._subscribers))

    def subscribe(self, subscriber: Any, *, name: str | None = None) -> Callable[[], None]:
        if subscriber is self.store:
            raise ValueError("the sequence store is already subscribed")
        if any(existing is subscriber for _, existing in self._subscribers):
            raise ValueError("subscriber is already registered")
        rendered_name = _subscriber_name(subscriber, len(self._subscribers) + 1, name)
        self._subscribers.append((rendered_name, subscriber))

        def unsubscribe() -> None:
            self.unsubscribe(subscriber)

        return unsubscribe

    def unsubscribe(self, subscriber: Any) -> bool:
        for index, (_, existing) in enumerate(self._subscribers):
            if existing is subscriber:
                del self._subscribers[index]
                return True
        return False

    def publish(self, event: Any) -> EmissionReport:
        deliveries: list[SubscriberDelivery] = []
        canonical: EventEnvelope | None = None
        try:
            canonical = self.store.append(event)
        except Exception as exc:
            deliveries.append(
                SubscriberDelivery(
                    name="SessionStore[0]",
                    status=DeliveryStatus.FAILED,
                    error_type=type(exc).__name__,
                )
            )
            deliveries.extend(
                SubscriberDelivery(name=name, status=DeliveryStatus.SKIPPED)
                for name, _ in self._subscribers
            )
        else:
            deliveries.append(
                SubscriberDelivery(name="SessionStore[0]", status=DeliveryStatus.SUCCESS)
            )
            for name, subscriber in tuple(self._subscribers):
                try:
                    _notify(subscriber, canonical)
                except Exception as exc:
                    deliveries.append(
                        SubscriberDelivery(
                            name=name,
                            status=DeliveryStatus.FAILED,
                            error_type=type(exc).__name__,
                        )
                    )
                else:
                    deliveries.append(SubscriberDelivery(name=name, status=DeliveryStatus.SUCCESS))

        report = EmissionReport(canonical, tuple(deliveries))
        self.last_report = report
        if not report.succeeded:
            raise EventDispatchError(report)
        return report

    def emit(self, event: Any | None = None, /, **fields: Any) -> EmissionReport:
        if event is not None and fields:
            raise ValueError("provide an event or event fields, not both")
        if event is None:
            event = PendingEvent(**fields)
        return self.publish(event)

    dispatch = publish


__all__ = [
    "DeliveryStatus",
    "EmissionReport",
    "Event",
    "EventDispatchError",
    "EventEmitter",
    "EventEnvelope",
    "EventRecord",
    "EventSerializationError",
    "EventSubscriber",
    "EventType",
    "PendingEvent",
    "REDACTED",
    "SCHEMA_VERSION",
    "SequenceStore",
    "SubscriberDelivery",
    "UNSUPPORTED_OBJECT",
    "canonicalize_event",
    "has_explicit_event_id",
    "safe_json_value",
]
