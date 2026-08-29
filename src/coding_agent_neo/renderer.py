"""Bounded, fact-preserving terminal rendering for canonical events."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TextIO

from coding_agent_neo.models import EventEnvelope, EventType


def _bounded(value: Any, limit: int) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=lambda _value: "<unsupported-object>")
    )
    if len(text) <= limit:
        return text
    marker = f"\n… <terminal truncated; original_length={len(text)}> …\n"
    available = max(0, limit - len(marker))
    head = (available + 1) // 2
    tail = available // 2
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}"


def _event_name(event: EventEnvelope) -> str:
    return str(event.type)


@dataclass(slots=True)
class RenderStats:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class TerminalRenderer:
    """An EventEmitter subscriber that never changes the canonical fact."""

    def __init__(self, stream: TextIO | None = None, *, output_limit: int = 2_000) -> None:
        if (
            isinstance(output_limit, bool)
            or not isinstance(output_limit, int)
            or output_limit < 128
        ):
            raise ValueError("output_limit must be an integer of at least 128")
        self.stream = sys.stderr if stream is None else stream
        self.output_limit = output_limit
        self.stats = RenderStats()

    def _write(self, text: str) -> None:
        self.stream.write(f"{text}\n")
        self.stream.flush()

    def publish(self, event: EventEnvelope) -> None:
        payload = event.payload
        name = _event_name(event)
        if name == EventType.ASSISTANT_MESSAGE.value:
            self.stats.model_calls += 1
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                self.stats.input_tokens += int(usage.get("input_tokens") or 0)
                self.stats.output_tokens += int(usage.get("output_tokens") or 0)
            text = payload.get("text")
            if text:
                self._write(f"assistant> {_bounded(text, self.output_limit)}")
            calls = payload.get("tool_calls")
            if isinstance(calls, (list, tuple)):
                for call in calls:
                    if not isinstance(call, Mapping):
                        continue
                    tool = call.get("name", "<unknown>")
                    arguments = call.get("raw_arguments", "{}")
                    self._write(f"tool> {tool} {_bounded(arguments, min(400, self.output_limit))}")
            return
        if name == EventType.TOOL_CALL.value:
            self.stats.tool_calls += 1
            self._write(f"tool-call> {payload.get('tool_name', '<unknown>')}")
            return
        if name == EventType.POLICY_DECISION.value:
            requested = payload.get("requested", "")
            decision = payload.get("decision", "")
            approved = payload.get("approved")
            suffix = "" if approved is None else f" approved={str(bool(approved)).lower()}"
            self._write(f"approval> requested={requested} decision={decision}{suffix}")
            return
        if name == EventType.TOOL_RESULT.value:
            result = payload.get("result")
            result = result if isinstance(result, Mapping) else payload
            details = [f"status={result.get('status', payload.get('status', 'unknown'))}"]
            if result.get("exit_code") is not None:
                details.append(f"exit_code={result['exit_code']}")
            if result.get("duration_seconds") is not None:
                details.append(f"duration={float(result['duration_seconds']):.3f}s")
            if result.get("timed_out"):
                details.append("timed_out=true")
            if result.get("truncated"):
                details.append(f"truncated=true original_length={result.get('original_length')}")
            self._write(f"result> {' '.join(details)}")
            text = result.get("text")
            if text:
                self._write(_bounded(text, self.output_limit))
            return
        if name == EventType.COMPACTION.value:
            self.stats.model_calls += 1
            self._write(
                "compaction> "
                f"status={payload.get('status')} forced={payload.get('forced')} "
                f"covered={payload.get('covered_through_sequence')}"
            )
            return
        if name == EventType.RETRY.value:
            self.stats.model_calls += 1
            self._write(
                f"retry> reason={payload.get('reason')} "
                f"attempt={payload.get('attempt')}/{payload.get('max_attempts')}"
            )
            return
        if name == EventType.TURN_END.value:
            budget = payload.get("budget")
            budget = budget if isinstance(budget, Mapping) else {}
            limit = payload.get("limit_reason")
            self._write(
                f"turn> state={payload.get('state')} reason={payload.get('reason')}"
                + (f" limit={limit}" if limit else "")
                + f" steps={budget.get('model_steps', self.stats.model_calls)}"
                + f" tools={budget.get('tool_calls', self.stats.tool_calls)}"
            )
            return
        if name == EventType.ERROR.value:
            reason = payload.get("reason", payload.get("message", ""))
            self._write(
                f"error> state={payload.get('state', '')} "
                f"type={payload.get('error_type', '')} reason={reason}"
            )
            return
        if name in {EventType.AGENT_END.value, EventType.SESSION_END.value}:
            budget = payload.get("budget")
            budget = budget if isinstance(budget, Mapping) else {}
            self._write(
                f"final> state={payload.get('state')} reason={payload.get('reason')} "
                f"model_calls={self.stats.model_calls} "
                f"tool_calls={self.stats.tool_calls} "
                f"tokens={budget.get('input_tokens', self.stats.input_tokens)}+"
                f"{budget.get('output_tokens', self.stats.output_tokens)} "
                f"elapsed={float(budget.get('elapsed_seconds') or 0.0):.3f}s"
            )

    render = publish
    on_event = publish


__all__ = ["RenderStats", "TerminalRenderer"]
