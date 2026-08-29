"""Append-only UTF-8 JSONL session persistence.

``SessionStore`` is the sole owner of sequence allocation.  It canonicalizes
one pending event, bounds its already-redacted payload, appends exactly one
JSON object plus a newline, flushes it, and optionally calls ``fsync`` before
reporting success.  Existing bytes are never opened for update or rewritten.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from coding_agent_neo.events import (
    SCHEMA_VERSION,
    canonicalize_event,
    has_explicit_event_id,
)
from coding_agent_neo.models import (
    EventEnvelope,
    EventId,
    IdFactoryLike,
    SessionId,
    UUIDIdFactory,
    utc_now,
)

DEFAULT_MAX_PAYLOAD_BYTES = 1_000_000
MIN_MAX_PAYLOAD_BYTES = 128


class SessionError(RuntimeError):
    """Base error for safe session persistence and reading failures."""


class SessionFormatError(SessionError):
    """A complete JSONL record violates the schema or session invariants."""

    def __init__(self, message: str, *, line_number: int, byte_offset: int) -> None:
        super().__init__(message)
        self.line_number = line_number
        self.byte_offset = byte_offset


class SessionWriteError(SessionError):
    """An append did not reach the configured flush/durability boundary."""


class DuplicateEventIdError(SessionError):
    """A producer attempted to reuse a stable event ID in one session."""


class IncompleteSessionTailError(SessionError):
    """Appending was refused because the file already has an incomplete tail."""


@dataclass(frozen=True, slots=True)
class SessionDiagnostic:
    """Location and stable classification of an ignored incomplete tail."""

    code: str
    line_number: int
    byte_offset: int
    byte_length: int
    message: str


@dataclass(frozen=True, slots=True)
class SessionReadResult:
    """All complete records and any safely ignored tail diagnostic."""

    events: tuple[EventEnvelope, ...]
    diagnostics: tuple[SessionDiagnostic, ...] = ()
    ended_with_newline: bool = True

    @property
    def records(self) -> tuple[EventEnvelope, ...]:
        return self.events

    @property
    def tail_diagnostic(self) -> SessionDiagnostic | None:
        return self.diagnostics[-1] if self.diagnostics else None

    @property
    def last_valid_sequence(self) -> int | None:
        return self.events[-1].sequence if self.events else None


def _json_object(raw: bytes) -> Mapping[str, Any]:
    text = raw.decode("utf-8")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-standard JSON constant")

    value = json.loads(text, parse_constant=reject_constant)
    if not isinstance(value, Mapping):
        raise ValueError("session record must be a JSON object")
    return value


def _envelope_from_record(record: Mapping[str, Any]) -> EventEnvelope:
    required = {
        "schema_version",
        "session_id",
        "event_id",
        "agent_id",
        "sequence",
        "type",
        "timestamp",
        "payload",
    }
    if not required.issubset(record):
        raise ValueError("session record is missing required fields")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported event schema version")
    payload = record["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be a JSON object")
    return EventEnvelope(
        schema_version=record["schema_version"],
        session_id=record["session_id"],
        event_id=record["event_id"],
        agent_id=record["agent_id"],
        parent_agent_id=record.get("parent_agent_id"),
        sequence=record["sequence"],
        type=record["type"],
        correlation_id=record.get("correlation_id"),
        provider_tool_call_id=record.get("provider_tool_call_id"),
        timestamp=record["timestamp"],
        payload=payload,
    )


def read_session(
    path: str | os.PathLike[str],
    *,
    expected_session_id: SessionId | str | None = None,
) -> SessionReadResult:
    """Read every complete event and report, but do not consume, a bad tail.

    Only an invalid final line that lacks a newline is classified as an
    interrupted append.  Invalid UTF-8/JSON/schema in a complete line, or in
    any middle line, is a hard :class:`SessionFormatError`.
    """

    session_path = Path(path)
    if not session_path.exists():
        return SessionReadResult(())
    try:
        data = session_path.read_bytes()
    except OSError:
        raise SessionError("session file could not be read") from None
    if not data:
        return SessionReadResult(())

    expected = None if expected_session_id is None else SessionId(expected_session_id)
    inferred_session: SessionId | None = expected
    events: list[EventEnvelope] = []
    diagnostics: list[SessionDiagnostic] = []
    event_ids: set[EventId] = set()
    previous_sequence: int | None = None
    lines = data.splitlines(keepends=True)
    offset = 0

    for index, physical_line in enumerate(lines, start=1):
        complete = physical_line.endswith(b"\n")
        content = physical_line[:-1] if complete else physical_line
        if content.endswith(b"\r"):
            content = content[:-1]
        is_last = index == len(lines)
        try:
            if not content:
                raise ValueError("empty JSONL record")
            event = _envelope_from_record(_json_object(content))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            if is_last and not complete:
                diagnostics.append(
                    SessionDiagnostic(
                        code="incomplete_tail",
                        line_number=index,
                        byte_offset=offset,
                        byte_length=len(physical_line),
                        message="ignored an incomplete final JSONL record",
                    )
                )
                break
            raise SessionFormatError(
                "invalid complete session record",
                line_number=index,
                byte_offset=offset,
            ) from None
        try:
            if inferred_session is None:
                inferred_session = event.session_id
            elif event.session_id != inferred_session:
                raise ValueError("session ID changed within one file")
            if event.event_id in event_ids:
                raise ValueError("event ID is duplicated")
            if previous_sequence is not None and event.sequence <= previous_sequence:
                raise ValueError("event sequence is not strictly increasing")
        except ValueError:
            raise SessionFormatError(
                "session record violates identity or sequence invariants",
                line_number=index,
                byte_offset=offset,
            ) from None
        events.append(event)
        event_ids.add(event.event_id)
        previous_sequence = event.sequence
        offset += len(physical_line)

    return SessionReadResult(
        events=tuple(events),
        diagnostics=tuple(diagnostics),
        ended_with_newline=data.endswith(b"\n"),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _bounded_payload(
    payload: Mapping[str, Any],
    max_payload_bytes: int,
) -> Mapping[str, Any]:
    serialized = _canonical_json(payload)
    original_length = len(serialized.encode("utf-8"))
    if original_length <= max_payload_bytes:
        return payload

    def preview(character_count: int) -> dict[str, Any]:
        head_count = (character_count + 1) // 2
        tail_count = character_count // 2
        return {
            "truncated": True,
            "original_length": original_length,
            "limit": max_payload_bytes,
            "encoding": "utf-8",
            "head": serialized[:head_count],
            "tail": serialized[-tail_count:] if tail_count else "",
        }

    low = 0
    high = len(serialized)
    best = preview(0)
    while low <= high:
        middle = (low + high) // 2
        candidate = preview(middle)
        size = len(_canonical_json(candidate).encode("utf-8"))
        if size <= max_payload_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if len(_canonical_json(best).encode("utf-8")) > max_payload_bytes:
        raise ValueError("max_payload_bytes is too small for truncation metadata")
    return best


class SessionStore:
    """Thread-safe, append-only owner of one session's JSONL sequence."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        session_id: SessionId | str | None = None,
        *,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        session_output_limit: int | None = None,
        max_event_bytes: int | None = None,
        id_factory: IdFactoryLike | None = None,
        clock: Callable[[], datetime] = utc_now,
        fsync: bool = True,
    ) -> None:
        supplied_limits = [
            value for value in (session_output_limit, max_event_bytes) if value is not None
        ]
        if supplied_limits:
            if max_payload_bytes != DEFAULT_MAX_PAYLOAD_BYTES or len(supplied_limits) > 1:
                raise ValueError("provide only one session payload limit")
            max_payload_bytes = supplied_limits[0]
        if isinstance(max_payload_bytes, bool) or not isinstance(max_payload_bytes, int):
            raise TypeError("max_payload_bytes must be an integer")
        if max_payload_bytes < MIN_MAX_PAYLOAD_BYTES:
            raise ValueError(f"max_payload_bytes must be at least {MIN_MAX_PAYLOAD_BYTES} bytes")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(fsync, bool):
            raise TypeError("fsync must be a boolean")

        self.path = Path(path)
        self.session_id = None if session_id is None else SessionId(session_id)
        self.max_payload_bytes = max_payload_bytes
        self.id_factory = UUIDIdFactory() if id_factory is None else id_factory
        self.clock = clock
        self.fsync = fsync
        self._lock = RLock()
        self._closed = False
        self._write_failed = False

        result = read_session(self.path, expected_session_id=self.session_id)
        if self.session_id is None and result.events:
            self.session_id = result.events[0].session_id
        self._event_ids = {event.event_id for event in result.events}
        self._next_sequence = (
            1 if result.last_valid_sequence is None else result.last_valid_sequence + 1
        )
        self._tail_diagnostic = result.tail_diagnostic
        self._needs_separator = bool(result.events) and not result.ended_with_newline

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def tail_diagnostic(self) -> SessionDiagnostic | None:
        return self._tail_diagnostic

    def _unique_generated_id(self, base: EventId) -> EventId:
        if base not in self._event_ids:
            return base
        for suffix in range(1, 1025):
            try:
                candidate = EventId(f"{base}_{suffix}")
            except ValueError:
                break
            if candidate not in self._event_ids:
                return candidate
        candidate = EventId(f"event_{uuid4().hex}")
        while candidate in self._event_ids:
            candidate = EventId(f"event_{uuid4().hex}")
        return candidate

    def _prepare(self, event: Any, sequence: int) -> EventEnvelope:
        envelope = canonicalize_event(
            event,
            sequence=sequence,
            id_factory=self.id_factory,
            clock=self.clock,
        )
        if self.session_id is None:
            self.session_id = envelope.session_id
        elif envelope.session_id != self.session_id:
            raise ValueError("event session ID does not match this store")

        if envelope.event_id in self._event_ids:
            if has_explicit_event_id(event):
                raise DuplicateEventIdError("event ID is already present in this session")
            replacement_id = self._unique_generated_id(envelope.event_id)
            envelope = canonicalize_event(
                event,
                sequence=sequence,
                id_factory=self.id_factory,
                clock=self.clock,
                event_id=replacement_id,
            )

        bounded = _bounded_payload(envelope.payload, self.max_payload_bytes)
        if bounded is envelope.payload:
            return envelope
        return EventEnvelope(
            schema_version=envelope.schema_version,
            session_id=envelope.session_id,
            event_id=envelope.event_id,
            agent_id=envelope.agent_id,
            parent_agent_id=envelope.parent_agent_id,
            sequence=envelope.sequence,
            type=envelope.type,
            correlation_id=envelope.correlation_id,
            provider_tool_call_id=envelope.provider_tool_call_id,
            timestamp=envelope.timestamp,
            payload=bounded,
        )

    def _write_line(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        descriptor = os.open(self.path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as handle:
                descriptor = -1
                handle.write(line)
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def append(self, event: Any) -> EventEnvelope:
        """Assign the next sequence and durably append one canonical event."""

        with self._lock:
            if self._closed:
                raise SessionWriteError("session store is closed")
            if self._write_failed:
                raise SessionWriteError("session store requires reopening after a failed append")
            if self._tail_diagnostic is not None:
                raise IncompleteSessionTailError(
                    "refusing to append after an incomplete JSONL tail"
                )

            sequence = self._next_sequence
            envelope = self._prepare(event, sequence)
            line = (
                ("\n" if self._needs_separator else "") + _canonical_json(envelope.to_dict()) + "\n"
            )
            try:
                self._write_line(line)
            except Exception:
                self._write_failed = True
                raise SessionWriteError("session append did not complete") from None
            self._event_ids.add(envelope.event_id)
            self._next_sequence += 1
            self._needs_separator = False
            return envelope

    publish = append
    emit = append

    def append_many(self, events: Iterable[Any]) -> tuple[EventEnvelope, ...]:
        return tuple(self.append(event) for event in events)

    def read(self) -> SessionReadResult:
        return read_session(self.path, expected_session_id=self.session_id)

    load = read

    def read_events(self) -> tuple[EventEnvelope, ...]:
        return self.read().events

    def flush(self) -> None:
        """Compatibility no-op: every successful append is already flushed."""

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


__all__ = [
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DuplicateEventIdError",
    "IncompleteSessionTailError",
    "MIN_MAX_PAYLOAD_BYTES",
    "SessionDiagnostic",
    "SessionError",
    "SessionFormatError",
    "SessionReadResult",
    "SessionStore",
    "SessionWriteError",
    "read_session",
]
