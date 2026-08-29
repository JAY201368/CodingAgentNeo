"""Runtime-local model context projection and conservative token budgeting.

The builder consumes only values supplied by its caller: an explicit system
prompt, active tool schemas, and one :class:`~coding_agent_neo.runtime.AgentRuntime`.
It never discovers prompt fragments, reads the workspace, or scans external
resources.  Session history remains append-only; this module only builds a
detached request projection.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from coding_agent_neo.runtime import AgentRuntime, ContextState

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_RESERVED_OUTPUT_TOKENS = 4_096
DEFAULT_COMPACTION_THRESHOLD = 0.85
DEFAULT_RECENT_GROUPS = 2
DEGRADED_CONTEXT_NOTICE = (
    "Context compaction failed. Some earlier conversation is not loaded; "
    "use the retained summary and recent complete interactions, and verify "
    "important assumptions before acting."
)


class ContextError(RuntimeError):
    """Base error for safe context construction."""


class ContextIntegrityError(ContextError):
    """Stored model messages cannot be grouped without breaking tool calls."""


class ContextWindowExceeded(ContextError):
    """Even a bounded projection cannot fit the configured context window."""

    def __init__(self, estimate: ContextEstimate, *, reason: str) -> None:
        self.estimate = estimate
        self.reason = reason
        super().__init__(reason)


def _detach_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _detach_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_detach_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("context messages must contain JSON-compatible values")


def detach_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached JSON-compatible model message."""

    detached = _detach_value(message)
    if not isinstance(detached, dict):  # pragma: no cover - Mapping becomes dict.
        raise TypeError("context message must be an object")
    role = detached.get("role")
    if not isinstance(role, str) or not role:
        raise ContextIntegrityError("context message requires a non-empty role")
    return detached


@dataclass(frozen=True, slots=True)
class ApproximateTokenEstimator:
    """Conservative tokenizer-independent estimate suitable for preflight use.

    UTF-8 bytes are divided by three rather than the commonly quoted four
    characters per token, then a safety multiplier and per-item framing cost
    are applied.  It is intentionally an estimate, not a provider tokenizer.
    """

    bytes_per_token: float = 3.0
    safety_multiplier: float = 1.12
    item_overhead: int = 4

    def __post_init__(self) -> None:
        if (
            isinstance(self.bytes_per_token, bool)
            or not isinstance(self.bytes_per_token, (int, float))
            or not math.isfinite(self.bytes_per_token)
            or self.bytes_per_token <= 0
        ):
            raise ValueError("bytes_per_token must be a positive finite number")
        if (
            isinstance(self.safety_multiplier, bool)
            or not isinstance(self.safety_multiplier, (int, float))
            or not math.isfinite(self.safety_multiplier)
            or self.safety_multiplier < 1
        ):
            raise ValueError("safety_multiplier must be finite and at least 1")
        if (
            isinstance(self.item_overhead, bool)
            or not isinstance(self.item_overhead, int)
            or self.item_overhead < 0
        ):
            raise ValueError("item_overhead must be a non-negative integer")

    def text(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("token estimation text must be a string")
        raw = len(value.encode("utf-8")) / self.bytes_per_token
        return max(1, math.ceil(raw * self.safety_multiplier))

    def json_value(self, value: Any) -> int:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return self.text(serialized) + self.item_overhead

    def request(
        self,
        messages: Sequence[Mapping[str, Any]],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> int:
        message_tokens = sum(self.json_value(message) for message in messages)
        tool_tokens = sum(self.json_value(schema) for schema in tool_schemas)
        return message_tokens + tool_tokens + self.item_overhead


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    """Input, output reserve, trigger, and hard-window accounting."""

    input_tokens: int
    reserved_output_tokens: int
    total_tokens: int
    trigger_tokens: int
    context_window: int

    @property
    def needs_compaction(self) -> bool:
        return self.total_tokens > self.trigger_tokens

    @property
    def exceeds_window(self) -> bool:
        return self.total_tokens > self.context_window


@dataclass(frozen=True, slots=True)
class MessageGroup:
    """An indivisible model interaction and its persisted sequence bounds."""

    messages: tuple[Mapping[str, Any], ...]
    sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        messages = tuple(detach_message(message) for message in self.messages)
        sequences = tuple(self.sequences)
        if not messages or len(messages) != len(sequences):
            raise ValueError("a message group requires equally sized non-empty values")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in sequences
        ):
            raise ValueError("message group sequences must be non-negative integers")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "sequences", sequences)

    @property
    def start_sequence(self) -> int:
        return min(self.sequences)

    @property
    def end_sequence(self) -> int:
        return max(self.sequences)


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """Detached messages and the complete preflight budget estimate."""

    messages: tuple[Mapping[str, Any], ...]
    estimate: ContextEstimate
    groups: tuple[MessageGroup, ...]

    def as_messages(self) -> list[dict[str, Any]]:
        return [detach_message(message) for message in self.messages]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """A prefix of complete groups eligible for one incremental summary."""

    old_summary: str | None
    groups: tuple[MessageGroup, ...]
    forced: bool = False
    previous_covered_sequence: int = 0

    def __post_init__(self) -> None:
        groups = tuple(self.groups)
        if not groups or not all(isinstance(group, MessageGroup) for group in groups):
            raise ValueError("a compaction plan requires complete message groups")
        if self.old_summary is not None and not isinstance(self.old_summary, str):
            raise TypeError("old_summary must be a string or None")
        if not isinstance(self.forced, bool):
            raise TypeError("forced must be a boolean")
        if (
            isinstance(self.previous_covered_sequence, bool)
            or not isinstance(self.previous_covered_sequence, int)
            or self.previous_covered_sequence < 0
        ):
            raise ValueError("previous_covered_sequence must be non-negative")
        object.__setattr__(self, "groups", groups)

    @property
    def source_start_sequence(self) -> int:
        return self.groups[0].start_sequence

    @property
    def covered_through_sequence(self) -> int:
        return self.groups[-1].end_sequence


def _tool_call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
        raise ContextIntegrityError("assistant tool_calls must be a sequence")
    call_ids: list[str] = []
    for call in raw_calls:
        if not isinstance(call, Mapping):
            raise ContextIntegrityError("assistant tool_calls must contain objects")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ContextIntegrityError("assistant tool call requires an ID")
        if call_id in call_ids:
            raise ContextIntegrityError("assistant tool call IDs must be unique")
        call_ids.append(call_id)
    return tuple(call_ids)


def group_messages(
    messages: Sequence[Mapping[str, Any]],
    sequences: Sequence[int],
) -> tuple[MessageGroup, ...]:
    """Group every assistant tool-call message with all matching results.

    An incomplete or orphaned tool result is rejected instead of being
    silently split across a compaction boundary.
    """

    if len(messages) != len(sequences):
        raise ValueError("messages and sequences must have equal length")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in sequences
    ):
        raise ValueError("message sequences must be non-negative integers")

    detached = [detach_message(message) for message in messages]
    groups: list[MessageGroup] = []
    index = 0
    while index < len(detached):
        message = detached[index]
        role = message.get("role")
        if role == "tool":
            raise ContextIntegrityError("tool result has no preceding assistant call")
        expected_ids = _tool_call_ids(message) if role == "assistant" else ()
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
                raise ContextIntegrityError("tool result does not match its assistant call")
            if result_id in seen_ids:
                raise ContextIntegrityError("tool call has more than one result")
            seen_ids.add(result_id)
            grouped_messages.append(result)
            grouped_sequences.append(sequences[index])
            index += 1
        if seen_ids != set(expected_ids):
            raise ContextIntegrityError("assistant tool calls are missing results")
        groups.append(MessageGroup(tuple(grouped_messages), tuple(grouped_sequences)))
    return tuple(groups)


class ContextBuilder:
    """Build a bounded projection for exactly one explicitly supplied Runtime."""

    def __init__(
        self,
        system_prompt: str,
        *,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
        compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD,
        keep_recent_groups: int = DEFAULT_RECENT_GROUPS,
        estimator: ApproximateTokenEstimator | None = None,
    ) -> None:
        if not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be a string")
        if isinstance(context_window, bool) or not isinstance(context_window, int):
            raise TypeError("context_window must be an integer")
        if context_window <= 0:
            raise ValueError("context_window must be positive")
        if isinstance(reserved_output_tokens, bool) or not isinstance(reserved_output_tokens, int):
            raise TypeError("reserved_output_tokens must be an integer")
        if reserved_output_tokens < 0 or reserved_output_tokens >= context_window:
            raise ValueError("reserved_output_tokens must be within the context window")
        if (
            isinstance(compaction_threshold, bool)
            or not isinstance(compaction_threshold, (int, float))
            or not math.isfinite(compaction_threshold)
            or not 0 < compaction_threshold <= 1
        ):
            raise ValueError("compaction_threshold must be in (0, 1]")
        if (
            isinstance(keep_recent_groups, bool)
            or not isinstance(keep_recent_groups, int)
            or keep_recent_groups < 1
        ):
            raise ValueError("keep_recent_groups must be a positive integer")
        self.system_prompt = system_prompt
        self.context_window = context_window
        self.reserved_output_tokens = reserved_output_tokens
        self.compaction_threshold = float(compaction_threshold)
        self.keep_recent_groups = keep_recent_groups
        if estimator is not None and not isinstance(estimator, ApproximateTokenEstimator):
            raise TypeError("estimator must be an ApproximateTokenEstimator or None")
        self.estimator = estimator or ApproximateTokenEstimator()

    def _entries(self, state: ContextState) -> tuple[list[Mapping[str, Any]], list[int]]:
        messages = [detach_message(message) for message in state.recent_messages]
        if len(state.recent_message_sequences) > len(messages):
            raise ContextIntegrityError("context has more sequences than messages")
        state.recent_message_sequences.extend(
            [None] * (len(messages) - len(state.recent_message_sequences))
        )
        current = max(state.covered_through_sequence, state.degraded_through_sequence)
        sequences: list[int] = []
        for index in range(len(messages)):
            value = state.recent_message_sequences[index]
            if value is None:
                value = current + 1
                state.recent_message_sequences[index] = value
            sequences.append(value)
            current = max(current, value)
        return messages, sequences

    def all_groups(self, runtime: AgentRuntime) -> tuple[MessageGroup, ...]:
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an AgentRuntime")
        messages, sequences = self._entries(runtime.context_state)
        return group_messages(messages, sequences)

    def _effective_groups(self, runtime: AgentRuntime) -> tuple[MessageGroup, ...]:
        state = runtime.context_state
        cutoff = max(state.covered_through_sequence, state.degraded_through_sequence)
        groups: list[MessageGroup] = []
        for group in self.all_groups(runtime):
            if group.end_sequence <= cutoff:
                continue
            # If imported state ever has a cutoff inside a tool group, retain
            # the whole group. Duplication is safer than splitting a call.
            groups.append(group)
        return tuple(groups)

    def estimate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> ContextEstimate:
        input_tokens = self.estimator.request(messages, tool_schemas)
        total_tokens = input_tokens + self.reserved_output_tokens
        trigger_tokens = max(1, math.floor(self.context_window * self.compaction_threshold))
        return ContextEstimate(
            input_tokens=input_tokens,
            reserved_output_tokens=self.reserved_output_tokens,
            total_tokens=total_tokens,
            trigger_tokens=trigger_tokens,
            context_window=self.context_window,
        )

    def project(
        self,
        runtime: AgentRuntime,
        tool_schemas: Sequence[Mapping[str, Any]] = (),
    ) -> ContextProjection:
        if not isinstance(tool_schemas, Sequence):
            raise TypeError("tool_schemas must be a sequence")
        if not all(isinstance(schema, Mapping) for schema in tool_schemas):
            raise TypeError("tool_schemas must contain mappings")
        state = runtime.context_state
        messages: list[Mapping[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        if state.latest_summary is not None:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Incremental context summary through sequence "
                        f"{state.covered_through_sequence}:\n{state.latest_summary}"
                    ),
                }
            )
        if state.degraded_notice is not None:
            messages.append({"role": "system", "content": state.degraded_notice})
        groups = self._effective_groups(runtime)
        for group in groups:
            messages.extend(group.messages)
        estimate = self.estimate(messages, tool_schemas)
        return ContextProjection(tuple(messages), estimate, groups)

    def build(
        self,
        runtime: AgentRuntime,
        tool_schemas: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Compatibility convenience returning only detached messages."""

        return self.project(runtime, tool_schemas).as_messages()

    def plan_compaction(
        self,
        runtime: AgentRuntime,
        *,
        forced: bool = False,
    ) -> CompactionPlan | None:
        state = runtime.context_state
        groups = tuple(
            group
            for group in self.all_groups(runtime)
            if group.end_sequence > state.covered_through_sequence
        )
        retained_count = 1 if forced else self.keep_recent_groups
        candidate_count = len(groups) - retained_count
        if candidate_count <= 0:
            return None
        return CompactionPlan(
            old_summary=state.latest_summary,
            groups=groups[:candidate_count],
            forced=forced,
            previous_covered_sequence=state.covered_through_sequence,
        )

    def apply_degraded_fallback(
        self,
        runtime: AgentRuntime,
        plan: CompactionPlan,
    ) -> ContextProjection:
        """Omit only a complete old prefix after one failed compaction."""

        state = runtime.context_state
        state.degraded_through_sequence = max(
            state.degraded_through_sequence,
            plan.covered_through_sequence,
        )
        state.degraded_notice = DEGRADED_CONTEXT_NOTICE
        return self.project(runtime)


class SimpleContextBuilder(ContextBuilder):
    """Backward-compatible unbounded-looking builder used by older callers."""

    def __init__(self, system_prompt: str) -> None:
        super().__init__(
            system_prompt,
            context_window=2_000_000_000,
            reserved_output_tokens=0,
            compaction_threshold=1.0,
        )


__all__ = [
    "ApproximateTokenEstimator",
    "CompactionPlan",
    "ContextBuilder",
    "ContextError",
    "ContextEstimate",
    "ContextIntegrityError",
    "ContextProjection",
    "ContextWindowExceeded",
    "DEGRADED_CONTEXT_NOTICE",
    "MessageGroup",
    "SimpleContextBuilder",
    "detach_message",
    "group_messages",
]
