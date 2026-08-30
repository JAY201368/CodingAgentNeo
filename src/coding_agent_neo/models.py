"""Backend-neutral domain models used by the runtime and environments.

This module intentionally contains data contracts only.  In particular, the
environment request/response models describe logical paths and command
results; they do not contain a local filesystem path, Docker container ID, or
any other implementation detail of a particular execution backend.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, Self
from uuid import uuid4

JSONMapping = Mapping[str, Any]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class FrozenDict(dict[str, Any]):
    """JSON-serializable mapping that rejects mutation after construction."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("mapping is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze_value(value: Any) -> Any:
    """Detach JSON-like nested values while keeping them serializable."""

    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


class Identifier(str):
    """A validated, printable identifier.

    Distinct subclasses below make it difficult to accidentally use an agent
    ID as a correlation ID while still retaining normal string behaviour for
    JSON serializers and callers.
    """

    def __new__(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__} must be a string")
        if isinstance(value, Identifier) and not isinstance(value, cls):
            raise TypeError(f"{cls.__name__} cannot wrap {type(value).__name__}")
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(
                f"{cls.__name__} must be 1-128 characters of letters, digits, '_', '.', ':' or '-'"
            )
        return str.__new__(cls, value)


class AgentId(Identifier):
    """Stable identity of the agent that owns a runtime or event."""


class SessionId(Identifier):
    """Identity of a linear session."""


class EventId(Identifier):
    """Identity of one event envelope."""


class CorrelationId(Identifier):
    """Internal identity of one operation chain, such as a tool call."""


class ProviderToolCallId(Identifier):
    """Opaque provider ID retained separately from :class:`CorrelationId`."""

    def __new__(cls, value: str) -> Self:
        """Preserve any non-empty provider string without internal ID rules."""

        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__} must be a string")
        if isinstance(value, Identifier) and not isinstance(value, cls):
            raise TypeError(f"{cls.__name__} cannot wrap {type(value).__name__}")
        if not value or "\x00" in value:
            raise ValueError(f"{cls.__name__} must be non-empty and NUL-free")
        return str.__new__(cls, value)


def _coerce_identifier[IdentifierType: Identifier](
    value: str | Identifier, identifier_type: type[IdentifierType]
) -> IdentifierType:
    """Validate a plain string and convert it to its semantic ID type."""

    if isinstance(value, identifier_type):
        return value
    if isinstance(value, Identifier):
        raise TypeError(f"expected {identifier_type.__name__}, got {type(value).__name__}")
    return identifier_type(value)


class IdFactory(Protocol):
    """Injection point for deterministic IDs in tests and alternate runtimes."""

    def new_id(self, kind: str) -> str:
        """Return a new ID for ``kind`` (for example ``"event"``)."""


class UUIDIdFactory:
    """Default ID factory with no shared mutable state."""

    def new_id(self, kind: str) -> str:
        if not isinstance(kind, str) or not _IDENTIFIER_PATTERN.fullmatch(kind):
            raise ValueError("ID kind must be a non-empty identifier")
        return f"{kind}_{uuid4().hex}"


IdFactoryLike = IdFactory | Callable[[str], str]


def new_id(factory: IdFactoryLike, kind: str) -> str:
    """Call either a protocol-style or callable ID factory."""

    if hasattr(factory, "new_id"):
        creator = factory.new_id  # type: ignore[union-attr]
    else:
        creator = factory
    try:
        return creator(kind)
    except TypeError as exc:
        # A no-argument callable is also a useful deterministic test seam.
        try:
            return creator()  # type: ignore[call-arg]
        except TypeError:
            raise exc


def utc_now() -> datetime:
    """Return an aware UTC datetime for injectable event clocks."""

    return datetime.now(UTC)


def _timestamp_to_iso(value: datetime | str) -> str:
    """Validate and canonicalize an aware UTC timestamp to an ISO ``Z`` value."""

    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be a valid ISO 8601 datetime") from exc
    else:
        raise TypeError("timestamp must be a datetime or ISO 8601 string")

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    timestamp = timestamp.astimezone(UTC)
    # Keep microseconds when present, while always using the required UTC Z
    # suffix.  ``isoformat`` is deterministic and JSON-friendly.
    rendered = timestamp.isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"


def _validate_nonnegative(value: int | float | None, name: str, *, integer: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        expected = "integer" if integer else "number"
        raise TypeError(f"{name} must be a non-negative {expected} or None")
    if integer and not isinstance(value, int):
        raise TypeError(f"{name} must be a non-negative integer or None")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_logical_path(value: str, name: str = "path", *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")


class EventType(StrEnum):
    """Standard event names reserved by the session/event contract."""

    SESSION_START = "session_start"
    AGENT_START = "agent_start"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    POLICY_DECISION = "policy_decision"
    APPROVAL_REQUEST = "approval_request"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    RETRY = "retry"
    TURN_END = "turn_end"
    ERROR = "error"
    AGENT_END = "agent_end"
    SESSION_END = "session_end"


class RuntimeState(StrEnum):
    """States/terminal reasons shared by the runtime and future loop."""

    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED_TURN = "COMPLETED_TURN"
    LIMIT_REACHED = "LIMIT_REACHED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"


class EnvironmentStatus(StrEnum):
    """Backend-neutral outcome for an environment operation."""

    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class ToolResultStatus(StrEnum):
    """Outcome vocabulary reserved for the later tool executor."""

    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    INVALID = "invalid"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class EnvironmentRequest:
    """Marker base for logical requests accepted by an environment."""

    __slots__ = ()


class EnvironmentResponse:
    """Marker base for structured, backend-neutral environment results."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ReadFileRequest(EnvironmentRequest):
    """Read a logical text path, optionally bounded by lines or bytes."""

    path: str
    start_line: int | None = None
    end_line: int | None = None
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        _validate_logical_path(self.path)
        _validate_nonnegative(self.start_line, "start_line", integer=True)
        _validate_nonnegative(self.end_line, "end_line", integer=True)
        _validate_nonnegative(self.max_bytes, "max_bytes", integer=True)
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be greater than or equal to start_line")


@dataclass(frozen=True, slots=True)
class ListFilesRequest(EnvironmentRequest):
    """List entries below a logical path with a bounded result count."""

    path: str = ""
    recursive: bool = False
    max_entries: int = 100

    def __post_init__(self) -> None:
        _validate_logical_path(self.path, allow_empty=True)
        if not isinstance(self.recursive, bool):
            raise TypeError("recursive must be a boolean")
        _validate_nonnegative(self.max_entries, "max_entries", integer=True)


@dataclass(frozen=True, slots=True)
class SearchRequest(EnvironmentRequest):
    """Search a logical path using backend-neutral text/regex options."""

    query: str
    path: str = ""
    use_regex: bool = False
    max_results: int = 100
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query:
            raise ValueError("query must not be empty")
        if "\x00" in self.query:
            raise ValueError("query must not contain NUL")
        _validate_logical_path(self.path, allow_empty=True)
        if not isinstance(self.use_regex, bool):
            raise TypeError("use_regex must be a boolean")
        _validate_nonnegative(self.max_results, "max_results", integer=True)
        _validate_nonnegative(self.max_bytes, "max_bytes", integer=True)


@dataclass(frozen=True, slots=True)
class WriteFileRequest(EnvironmentRequest):
    """Create or replace text at a logical path."""

    path: str
    content: str
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        _validate_logical_path(self.path)
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if "\x00" in self.content:
            raise ValueError("content must not contain NUL")
        _validate_nonnegative(self.max_bytes, "max_bytes", integer=True)


@dataclass(frozen=True, slots=True)
class EditFileRequest(EnvironmentRequest):
    """Replace one or more exact text occurrences at a logical path."""

    path: str
    old_text: str
    new_text: str
    expected_replacements: int = 1

    def __post_init__(self) -> None:
        _validate_logical_path(self.path)
        if not isinstance(self.old_text, str) or not self.old_text:
            raise ValueError("old_text must not be empty")
        if not isinstance(self.new_text, str):
            raise TypeError("new_text must be a string")
        if "\x00" in self.old_text or "\x00" in self.new_text:
            raise ValueError("edit text must not contain NUL")
        _validate_nonnegative(self.expected_replacements, "expected_replacements", integer=True)
        if self.expected_replacements == 0:
            raise ValueError("expected_replacements must be greater than zero")


@dataclass(frozen=True, slots=True)
class RunCommandRequest(EnvironmentRequest):
    """Run a logical command with optional time/output limits."""

    command: str
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    working_directory: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("command must not be empty")
        if "\x00" in self.command:
            raise ValueError("command must not contain NUL")
        _validate_nonnegative(self.timeout_seconds, "timeout_seconds")
        _validate_nonnegative(self.max_output_bytes, "max_output_bytes", integer=True)
        if self.working_directory is not None:
            _validate_logical_path(self.working_directory, "working_directory", allow_empty=True)


# ``CommandRequest`` is the shorter spelling used by some environment clients.
CommandRequest = RunCommandRequest


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """A backend-neutral search match."""

    path: str
    line_number: int
    text: str

    def __post_init__(self) -> None:
        _validate_logical_path(self.path)
        _validate_nonnegative(self.line_number, "line_number", integer=True)
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")


@dataclass(frozen=True, slots=True)
class EnvironmentResult(EnvironmentResponse):
    """Common metadata shared by all environment operation results."""

    status: EnvironmentStatus = EnvironmentStatus.SUCCESS
    message: str = ""
    metadata: JSONMapping = field(default_factory=dict)
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", EnvironmentStatus(self.status))
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_value(self.metadata))
        _validate_nonnegative(self.duration_seconds, "duration_seconds")

    @property
    def ok(self) -> bool:
        return self.status is EnvironmentStatus.SUCCESS

    @property
    def success(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class FileResult(EnvironmentResult):
    """Result for read, write, and edit operations."""

    path: str | None = None
    content: str = ""
    truncated: bool = False
    original_length: int | None = None

    def __post_init__(self) -> None:
        EnvironmentResult.__post_init__(self)
        if self.path is not None:
            _validate_logical_path(self.path)
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        _validate_nonnegative(self.original_length, "original_length", integer=True)

    @property
    def text(self) -> str:
        return self.content


@dataclass(frozen=True, slots=True)
class ListResult(EnvironmentResult):
    """Result containing logical paths returned by a list operation."""

    entries: Sequence[str] = field(default_factory=tuple)
    truncated: bool = False
    original_length: int | None = None

    def __post_init__(self) -> None:
        EnvironmentResult.__post_init__(self)
        entries = tuple(self.entries)
        for entry in entries:
            _validate_logical_path(entry)
        object.__setattr__(self, "entries", entries)
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        _validate_nonnegative(self.original_length, "original_length", integer=True)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(self.entries)


@dataclass(frozen=True, slots=True)
class SearchResult(EnvironmentResult):
    """Result containing backend-neutral search matches."""

    matches: Sequence[SearchMatch] = field(default_factory=tuple)
    truncated: bool = False
    original_length: int | None = None

    def __post_init__(self) -> None:
        EnvironmentResult.__post_init__(self)
        matches = tuple(self.matches)
        if not all(isinstance(match, SearchMatch) for match in matches):
            raise TypeError("matches must contain SearchMatch values")
        object.__setattr__(self, "matches", matches)
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        _validate_nonnegative(self.original_length, "original_length", integer=True)

    @property
    def items(self) -> tuple[SearchMatch, ...]:
        return tuple(self.matches)


@dataclass(frozen=True, slots=True)
class CommandResult(EnvironmentResult):
    """Result for a command operation without backend-specific details."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    truncated: bool = False
    original_output_length: int | None = None

    def __post_init__(self) -> None:
        EnvironmentResult.__post_init__(self)
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise TypeError("stdout and stderr must be strings")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise TypeError("exit_code must be an integer or None")
        if not isinstance(self.timed_out, bool):
            raise TypeError("timed_out must be a boolean")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        _validate_nonnegative(self.original_output_length, "original_output_length", integer=True)

    @property
    def timeout(self) -> bool:
        return self.timed_out


# Operation-specific aliases keep call sites descriptive without creating
# multiple result schemas for the same backend-neutral payload.
ReadFileResult = FileResult
WriteFileResult = FileResult
EditFileResult = FileResult
RunCommandResult = CommandResult


@dataclass(frozen=True, slots=True, init=False)
class ToolCall:
    """Normalized tool call fields shared by model and executor boundaries."""

    correlation_id: CorrelationId | str
    name: str
    raw_arguments: str
    provider_tool_call_id: ProviderToolCallId | str | None = None
    parsed_arguments: JSONMapping | None = None

    def __init__(
        self,
        correlation_id: CorrelationId | str,
        name: str,
        raw_arguments: str | JSONMapping | None = None,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
        parsed_arguments: JSONMapping | None = None,
        *,
        arguments: str | JSONMapping | None = None,
    ) -> None:
        """Accept explicit raw/parsed fields or the convenient ``arguments`` alias."""

        if raw_arguments is None:
            raw_arguments = arguments
        elif arguments is not None:
            raise ValueError("use raw_arguments or arguments, not both")
        if raw_arguments is None:
            raise ValueError("raw_arguments or arguments is required")

        if isinstance(raw_arguments, Mapping):
            if parsed_arguments is not None and dict(parsed_arguments) != dict(raw_arguments):
                raise ValueError("parsed_arguments must match mapping arguments")
            parsed_arguments = raw_arguments
            raw_arguments = json.dumps(raw_arguments, ensure_ascii=False, separators=(",", ":"))
        elif not isinstance(raw_arguments, str):
            raise TypeError("raw_arguments must be a string or mapping")
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "raw_arguments", raw_arguments)
        object.__setattr__(self, "provider_tool_call_id", provider_tool_call_id)
        object.__setattr__(self, "parsed_arguments", parsed_arguments)
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "correlation_id", _coerce_identifier(self.correlation_id, CorrelationId)
        )
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("tool name must not be empty")
        if not isinstance(self.raw_arguments, str):
            raise TypeError("raw_arguments must be a string")
        if self.provider_tool_call_id is not None:
            object.__setattr__(
                self,
                "provider_tool_call_id",
                _coerce_identifier(self.provider_tool_call_id, ProviderToolCallId),
            )
        if self.parsed_arguments is not None:
            if not isinstance(self.parsed_arguments, Mapping):
                raise TypeError("parsed_arguments must be a mapping or None")
            object.__setattr__(self, "parsed_arguments", _freeze_value(self.parsed_arguments))

    @property
    def arguments(self) -> JSONMapping | str:
        """Parsed arguments when available, otherwise the provider's raw JSON."""

        return self.parsed_arguments if self.parsed_arguments is not None else self.raw_arguments


_MODEL_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|authorization|password|passwd|secret|credential|cookie|"
    r"private[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|token)\b"
    r"[\"']?\s*[:=]\s*)"
    r"([\"'])([^\"']*)\2"
)
_MODEL_SECRET_UNQUOTED = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|authorization|password|passwd|secret|credential|cookie|"
    r"private[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|token)\b"
    r"[\"']?\s*[:=]\s*)"
    r"(?![\"'])([^\s,;}]+)"
)
_MODEL_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;\"'}]+")


def _redact_model_text(value: str) -> str:
    """Redact common credentials before text crosses the model boundary."""

    value = _MODEL_BEARER.sub("Bearer <redacted>", value)
    value = _MODEL_SECRET_ASSIGNMENT.sub(r'\1"<redacted>"', value)
    return _MODEL_SECRET_UNQUOTED.sub(r"\1<redacted>", value)


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """Provider-neutral token counters returned by a model call."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer or None")

    @property
    def prompt_tokens(self) -> int | None:
        """OpenAI-compatible spelling for input token count."""

        return self.input_tokens

    @property
    def completion_tokens(self) -> int | None:
        """OpenAI-compatible spelling for output token count."""

        return self.output_tokens

    def to_dict(self) -> dict[str, int]:
        """Return only counters that the provider supplied."""

        return {
            name: value
            for name, value in (
                ("input_tokens", self.input_tokens),
                ("output_tokens", self.output_tokens),
                ("total_tokens", self.total_tokens),
            )
            if value is not None
        }

    as_dict = to_dict


# Descriptive aliases keep integration code compatible with common naming.
ModelUsage = NormalizedUsage
Usage = NormalizedUsage


@dataclass(frozen=True, slots=True)
class NormalizedToolCall:
    """A structured provider tool call before an internal correlation ID exists.

    The Agent Loop owns creation of an internal :class:`CorrelationId`; this
    transport DTO therefore deliberately does not contain one.  ``raw_arguments``
    remains the provider's JSON text (with credentials redacted at the boundary),
    while ``arguments_valid`` and ``diagnostics`` let the protocol boundary
    decide how malformed calls should be reported.
    """

    provider_tool_call_id: ProviderToolCallId | None = None
    name: str | None = None
    raw_arguments: str = ""
    arguments_valid: bool = False
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provider_tool_call_id is not None:
            if not isinstance(self.provider_tool_call_id, str):
                raise TypeError("provider_tool_call_id must be a string or None")
            provider_value = _redact_model_text(self.provider_tool_call_id)
            object.__setattr__(
                self,
                "provider_tool_call_id",
                _coerce_identifier(provider_value, ProviderToolCallId),
            )
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("tool name must be a non-empty string or None")
        if self.name is not None:
            object.__setattr__(self, "name", _redact_model_text(self.name))
        if not isinstance(self.raw_arguments, str):
            raise TypeError("raw_arguments must be a string")
        object.__setattr__(self, "raw_arguments", _redact_model_text(self.raw_arguments))
        if not isinstance(self.arguments_valid, bool):
            raise TypeError("arguments_valid must be a boolean")
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, str) and item for item in diagnostics):
            raise TypeError("diagnostics must contain non-empty strings")
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def provider_id(self) -> ProviderToolCallId | None:
        """Short alias for the opaque provider identifier."""

        return self.provider_tool_call_id

    @property
    def id(self) -> ProviderToolCallId | None:
        """OpenAI-compatible spelling for the provider identifier."""

        return self.provider_tool_call_id

    @property
    def arguments(self) -> str:
        """Original (safe, redacted) argument JSON text."""

        return self.raw_arguments

    @property
    def tool_call_id(self) -> ProviderToolCallId | None:
        """OpenAI-compatible alias for the provider identifier."""

        return self.provider_tool_call_id

    @property
    def raw_args(self) -> str:
        """Short alias for the original argument JSON text."""

        return self.raw_arguments

    @property
    def valid(self) -> bool:
        """Whether this call has structurally valid arguments."""

        return self.arguments_valid and not self.diagnostics

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible transport representation."""

        return {
            "provider_tool_call_id": (
                None if self.provider_tool_call_id is None else str(self.provider_tool_call_id)
            ),
            "name": self.name,
            "raw_arguments": self.raw_arguments,
            "arguments_valid": self.arguments_valid,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class NormalizedAssistantResponse:
    """Provider-neutral assistant output returned by :class:`ModelClient`."""

    text: str = ""
    tool_calls: tuple[NormalizedToolCall, ...] = ()
    usage: NormalizedUsage | None = None
    finish_reason: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("assistant text must be a string")
        object.__setattr__(self, "text", _redact_model_text(self.text))
        tool_calls = tuple(self.tool_calls)
        if not all(isinstance(call, NormalizedToolCall) for call in tool_calls):
            raise TypeError("tool_calls must contain NormalizedToolCall values")
        object.__setattr__(self, "tool_calls", tool_calls)
        if self.usage is not None and not isinstance(self.usage, NormalizedUsage):
            raise TypeError("usage must be NormalizedUsage or None")
        if self.finish_reason is not None and not isinstance(self.finish_reason, str):
            raise TypeError("finish_reason must be a string or None")
        object.__setattr__(
            self,
            "finish_reason",
            _redact_model_text(self.finish_reason) if self.finish_reason is not None else None,
        )
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, str) and item for item in diagnostics):
            raise TypeError("diagnostics must contain non-empty strings")
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def assistant_text(self) -> str:
        """Descriptive alias for the normalized assistant text."""

        return self.text

    @property
    def message(self) -> str:
        """Compatibility alias used by simple loop integrations."""

        return self.text

    @property
    def content(self) -> str:
        """OpenAI-compatible alias for assistant text."""

        return self.text

    @property
    def calls(self) -> tuple[NormalizedToolCall, ...]:
        """Short alias for normalized tool calls."""

        return self.tool_calls

    @property
    def finish_reason_code(self) -> str | None:
        """Descriptive alias for the provider finish reason."""

        return self.finish_reason

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible normalized response."""

        return {
            "text": self.text,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "usage": None if self.usage is None else self.usage.to_dict(),
            "finish_reason": self.finish_reason,
            "diagnostics": list(self.diagnostics),
        }

    as_dict = to_dict


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Unified tool result shape for the later executor."""

    correlation_id: CorrelationId | str
    status: ToolResultStatus = ToolResultStatus.SUCCESS
    text: str = ""
    provider_tool_call_id: ProviderToolCallId | str | None = None
    metadata: JSONMapping = field(default_factory=dict)
    truncated: bool = False
    original_length: int | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    timed_out: bool = False
    path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "correlation_id", _coerce_identifier(self.correlation_id, CorrelationId)
        )
        object.__setattr__(self, "status", ToolResultStatus(self.status))
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if self.provider_tool_call_id is not None:
            object.__setattr__(
                self,
                "provider_tool_call_id",
                _coerce_identifier(self.provider_tool_call_id, ProviderToolCallId),
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_value(self.metadata))
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        _validate_nonnegative(self.original_length, "original_length", integer=True)
        _validate_nonnegative(self.duration_seconds, "duration_seconds")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise TypeError("exit_code must be an integer or None")
        if not isinstance(self.timed_out, bool):
            raise TypeError("timed_out must be a boolean")
        if self.path is not None:
            _validate_logical_path(self.path)

    @property
    def ok(self) -> bool:
        return self.status is ToolResultStatus.SUCCESS

    @property
    def model_text(self) -> str:
        return self.text

    @property
    def output(self) -> str:
        return self.text

    @property
    def timeout(self) -> bool:
        return self.timed_out


@dataclass(frozen=True, slots=True, init=False)
class EventEnvelope:
    """Versioned event envelope with explicit ownership and correlation IDs."""

    schema_version: int
    session_id: SessionId
    event_id: EventId
    agent_id: AgentId
    sequence: int
    type: str
    timestamp: str
    parent_agent_id: AgentId | None
    correlation_id: CorrelationId | None
    provider_tool_call_id: ProviderToolCallId | None
    payload: JSONMapping

    def __init__(
        self,
        schema_version: int,
        session_id: SessionId | str,
        event_id: EventId | str,
        agent_id: AgentId | str,
        sequence: int,
        type: str | EventType | None = None,
        timestamp: datetime | str | None = None,
        payload: JSONMapping | None = None,
        parent_agent_id: AgentId | str | None = None,
        correlation_id: CorrelationId | str | None = None,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
        *,
        event_type: str | EventType | None = None,
    ) -> None:
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if type is None:
            type = event_type
        elif event_type is not None and str(type) != str(event_type):
            raise ValueError("type and event_type must match when both are supplied")
        if type is None or not isinstance(type, (str, EventType)) or not str(type):
            raise ValueError("event type is required")
        if timestamp is None:
            raise ValueError("timestamp is required")
        if payload is not None and not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping or None")

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "session_id", _coerce_identifier(session_id, SessionId))
        object.__setattr__(self, "event_id", _coerce_identifier(event_id, EventId))
        object.__setattr__(self, "agent_id", _coerce_identifier(agent_id, AgentId))
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "type", str(type))
        object.__setattr__(self, "timestamp", _timestamp_to_iso(timestamp))
        object.__setattr__(
            self,
            "parent_agent_id",
            None if parent_agent_id is None else _coerce_identifier(parent_agent_id, AgentId),
        )
        object.__setattr__(
            self,
            "correlation_id",
            None if correlation_id is None else _coerce_identifier(correlation_id, CorrelationId),
        )
        object.__setattr__(
            self,
            "provider_tool_call_id",
            None
            if provider_tool_call_id is None
            else _coerce_identifier(provider_tool_call_id, ProviderToolCallId),
        )
        object.__setattr__(self, "payload", _freeze_value({} if payload is None else payload))

    @property
    def event_type(self) -> str:
        """Compatibility spelling for callers that avoid the built-in ``type``."""

        return self.type

    @classmethod
    def create(
        cls,
        *,
        session_id: SessionId | str,
        agent_id: AgentId | str,
        sequence: int,
        type: str | EventType,
        id_factory: IdFactoryLike | None = None,
        clock: Callable[[], datetime] = utc_now,
        event_id: EventId | str | None = None,
        parent_agent_id: AgentId | str | None = None,
        correlation_id: CorrelationId | str | None = None,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
        payload: JSONMapping | None = None,
        schema_version: int = 1,
    ) -> EventEnvelope:
        """Construct an envelope with injectable ID and UTC clock sources."""

        factory = id_factory if id_factory is not None else UUIDIdFactory()
        generated_event_id = event_id or EventId(new_id(factory, "event"))
        timestamp = clock()
        return cls(
            schema_version=schema_version,
            session_id=session_id,
            event_id=generated_event_id,
            agent_id=agent_id,
            sequence=sequence,
            type=type,
            timestamp=timestamp,
            payload=payload,
            parent_agent_id=parent_agent_id,
            correlation_id=correlation_id,
            provider_tool_call_id=provider_tool_call_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-oriented copy without leaking mapping proxy objects."""

        return {
            "schema_version": self.schema_version,
            "session_id": str(self.session_id),
            "event_id": str(self.event_id),
            "agent_id": str(self.agent_id),
            "parent_agent_id": None if self.parent_agent_id is None else str(self.parent_agent_id),
            "sequence": self.sequence,
            "type": self.type,
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "provider_tool_call_id": (
                None if self.provider_tool_call_id is None else str(self.provider_tool_call_id)
            ),
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }

    as_dict = to_dict


__all__ = [
    "AgentId",
    "CommandRequest",
    "CommandResult",
    "CorrelationId",
    "EditFileRequest",
    "EnvironmentRequest",
    "EnvironmentResult",
    "EnvironmentResponse",
    "EnvironmentStatus",
    "EventEnvelope",
    "EventId",
    "EventType",
    "FileResult",
    "FrozenDict",
    "Identifier",
    "IdFactory",
    "IdFactoryLike",
    "ListFilesRequest",
    "ListResult",
    "ModelUsage",
    "NormalizedAssistantResponse",
    "NormalizedToolCall",
    "NormalizedUsage",
    "ProviderToolCallId",
    "ReadFileRequest",
    "ReadFileResult",
    "EditFileResult",
    "RunCommandRequest",
    "RunCommandResult",
    "RuntimeState",
    "SearchMatch",
    "SearchRequest",
    "SearchResult",
    "SessionId",
    "ToolCall",
    "ToolResult",
    "ToolResultStatus",
    "Usage",
    "UUIDIdFactory",
    "WriteFileRequest",
    "WriteFileResult",
    "new_id",
    "utc_now",
]
