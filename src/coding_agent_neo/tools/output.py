"""Model-visible and persistence projections for ``ToolResult`` values."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from coding_agent_neo.models import ToolResult, ToolResultStatus


@dataclass(frozen=True, slots=True)
class OutputSlice:
    """A bounded output string and the facts needed to explain truncation."""

    text: str
    truncated: bool
    original_length: int | None


def _validate_limit(limit: int | None, name: str = "limit") -> None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise TypeError(f"{name} must be a non-negative integer or None")
    if limit is not None and limit < 0:
        raise ValueError(f"{name} must be non-negative")


def head_tail_truncate(
    text: str,
    limit: int | None = None,
    *,
    original_length: int | None = None,
    max_length: int | None = None,
    max_chars: int | None = None,
) -> OutputSlice:
    """Keep the head and tail of text while reporting its original length.

    The returned string is at most ``limit`` characters.  When the limit is
    too small for the complete marker, the marker is itself bounded; the
    top-level ``original_length`` remains authoritative.
    """

    aliases = [value for value in (max_length, max_chars) if value is not None]
    if len(aliases) > 1 or (aliases and limit is not None):
        raise ValueError("use only one output limit argument")
    if aliases:
        limit = aliases[0]
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    _validate_limit(limit)
    if original_length is not None:
        if isinstance(original_length, bool) or not isinstance(original_length, int):
            raise TypeError("original_length must be a non-negative integer or None")
        if original_length < 0:
            raise ValueError("original_length must be non-negative")
    source_length = max(len(text), original_length or 0)
    if limit is None or len(text) <= limit:
        return OutputSlice(text=text, truncated=False, original_length=original_length)
    if limit == 0:
        return OutputSlice(text="", truncated=True, original_length=source_length)

    marker = f"\n... output truncated; original length: {source_length} ...\n"
    if len(marker) >= limit:
        return OutputSlice(text=marker[:limit], truncated=True, original_length=source_length)
    available = limit - len(marker)
    head_length = (available + 1) // 2
    tail_length = available - head_length
    tail = text[-tail_length:] if tail_length else ""
    bounded = f"{text[:head_length]}{marker}{tail}"
    return OutputSlice(text=bounded, truncated=True, original_length=source_length)


# Shorter names are convenient at call sites and preserve one implementation.
truncate_output = head_tail_truncate
truncate_text = head_tail_truncate


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class ToolResultProjection(dict[str, Any]):
    """A JSON-friendly view of a ``ToolResult`` with bounded text."""

    correlation_id: str
    provider_tool_call_id: str | None
    status: ToolResultStatus
    text: str
    metadata: Mapping[str, Any]
    truncated: bool
    original_length: int | None
    duration_seconds: float | None
    exit_code: int | None
    timed_out: bool
    path: str | None

    def __post_init__(self) -> None:
        # Populate the dict base so the projection can be passed directly to
        # JSON encoders as well as consumed through its typed attributes.
        dict.__init__(self, self.to_dict())

    @classmethod
    def from_result(cls, result: ToolResult, *, limit: int | None = None) -> ToolResultProjection:
        if not isinstance(result, ToolResult):
            raise TypeError("result must be a ToolResult")
        source_length = result.original_length
        sliced = head_tail_truncate(result.text, limit, original_length=source_length)
        already_truncated = result.truncated
        was_truncated = already_truncated or sliced.truncated
        effective_length = sliced.original_length
        if was_truncated and effective_length is None:
            effective_length = len(result.text)
        metadata = dict(result.metadata)
        if was_truncated:
            # Keep truncation facts visible in either projection without
            # replacing operation-specific metadata supplied by the backend.
            metadata.setdefault("truncated", True)
            if effective_length is not None:
                metadata.setdefault("original_length", effective_length)
        return cls(
            correlation_id=str(result.correlation_id),
            provider_tool_call_id=(
                None if result.provider_tool_call_id is None else str(result.provider_tool_call_id)
            ),
            status=ToolResultStatus(result.status),
            text=sliced.text,
            metadata=metadata,
            truncated=was_truncated,
            original_length=effective_length,
            duration_seconds=result.duration_seconds,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            path=result.path,
        )

    @property
    def model_text(self) -> str:
        return self.text

    @property
    def output(self) -> str:
        return self.text

    @property
    def timeout(self) -> bool:
        return self.timed_out

    @property
    def ok(self) -> bool:
        return self.status is ToolResultStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        """Return a plain JSON-compatible projection."""

        return {
            "correlation_id": self.correlation_id,
            "provider_tool_call_id": self.provider_tool_call_id,
            "status": str(self.status),
            "text": self.text,
            "metadata": _json_value(self.metadata),
            "truncated": self.truncated,
            "original_length": self.original_length,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "path": self.path,
        }

    as_dict = to_dict

    def to_model_message(self) -> dict[str, Any]:
        """Return the content shape expected by a tool-result model message."""

        return {
            "role": "tool",
            "tool_call_id": self.provider_tool_call_id,
            "content": self.text,
        }

    def to_tool_result(self) -> ToolResult:
        """Materialize the projection as a normal immutable ``ToolResult``."""

        return ToolResult(
            correlation_id=self.correlation_id,
            provider_tool_call_id=self.provider_tool_call_id,
            status=self.status,
            text=self.text,
            metadata=self.metadata,
            truncated=self.truncated,
            original_length=self.original_length,
            duration_seconds=self.duration_seconds,
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            path=self.path,
        )

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("projection is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def project_tool_result(
    result: ToolResult,
    limit: int | None = None,
    *,
    max_chars: int | None = None,
    max_output: int | None = None,
    max_bytes: int | None = None,
) -> ToolResultProjection:
    """Project one result with a caller-selected output bound.

    ``max_chars``, ``max_output`` and ``max_bytes`` are accepted aliases for
    integration code that names the setting after its configuration field.
    The output contract is character-bounded; callers needing byte-level
    storage can choose a conservative character limit before projection.
    """

    aliases = [value for value in (max_chars, max_output, max_bytes) if value is not None]
    if len(aliases) > 1 or (aliases and limit is not None):
        raise ValueError("use only one output limit alias")
    effective_limit = aliases[0] if aliases else limit
    return ToolResultProjection.from_result(result, limit=effective_limit)


def project_for_model(
    result: ToolResult,
    limit: int | None = None,
    *,
    max_chars: int | None = None,
    max_output: int | None = None,
) -> ToolResultProjection:
    return project_tool_result(result, limit, max_chars=max_chars, max_output=max_output)


def project_for_persistence(
    result: ToolResult,
    limit: int | None = None,
    *,
    max_chars: int | None = None,
    max_output: int | None = None,
) -> ToolResultProjection:
    return project_tool_result(result, limit, max_chars=max_chars, max_output=max_output)


project_model_output = project_for_model
project_persistent_output = project_for_persistence
model_visible_projection = project_for_model
persistent_projection = project_for_persistence
project_output = project_tool_result
OutputProjection = ToolResultProjection


class OutputProjector:
    """Reusable pair of model and persistence output bounds."""

    def __init__(
        self,
        model_limit: int | None = None,
        persistence_limit: int | None = None,
        *,
        model_output_limit: int | None = None,
        persistence_output_limit: int | None = None,
        model_max_chars: int | None = None,
        persistence_max_chars: int | None = None,
    ) -> None:
        if model_output_limit is not None and model_max_chars is not None:
            raise ValueError("use model_output_limit or model_max_chars, not both")
        if persistence_output_limit is not None and persistence_max_chars is not None:
            raise ValueError("use persistence_output_limit or persistence_max_chars, not both")
        if model_max_chars is not None:
            model_output_limit = model_max_chars
        if persistence_max_chars is not None:
            persistence_output_limit = persistence_max_chars
        if model_limit is not None and model_output_limit is not None:
            raise ValueError("use model_limit or model_output_limit, not both")
        if persistence_limit is not None and persistence_output_limit is not None:
            raise ValueError("use persistence_limit or persistence_output_limit, not both")
        self.model_limit = model_output_limit if model_output_limit is not None else model_limit
        self.persistence_limit = (
            persistence_output_limit if persistence_output_limit is not None else persistence_limit
        )
        _validate_limit(self.model_limit, "model_limit")
        _validate_limit(self.persistence_limit, "persistence_limit")

    def for_model(self, result: ToolResult) -> ToolResultProjection:
        return project_tool_result(result, self.model_limit)

    def for_persistence(self, result: ToolResult) -> ToolResultProjection:
        return project_tool_result(result, self.persistence_limit)

    def project(self, result: ToolResult, *, target: str = "model") -> ToolResultProjection:
        if target == "model":
            return self.for_model(result)
        if target in {"persistence", "persistent", "session"}:
            return self.for_persistence(result)
        raise ValueError("target must be 'model' or 'persistence'")

    __call__ = project

    model = for_model
    persistent = for_persistence
    project_model = for_model
    project_persistence = for_persistence
    model_visible = for_model
    persisted = for_persistence


__all__ = [
    "OutputProjector",
    "OutputProjection",
    "OutputSlice",
    "ToolResultProjection",
    "head_tail_truncate",
    "model_visible_projection",
    "persistent_projection",
    "project_for_model",
    "project_for_persistence",
    "project_model_output",
    "project_output",
    "project_persistent_output",
    "project_tool_result",
    "truncate_output",
    "truncate_text",
]
