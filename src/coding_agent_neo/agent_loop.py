"""Explicit, bounded synchronous Agent Loop.

The loop owns orchestration only.  Model access, tool registration and
execution, event persistence, policy, cancellation, and environment side
effects remain behind their existing boundaries. Context construction and
compaction are explicit Runtime-local dependencies and do not change the
loop's source-agnostic tool protocol.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from coding_agent_neo.compactor import CompactionOutcome, Compactor
from coding_agent_neo.context import (
    ContextBuilder,
    ContextWindowExceeded,
    SimpleContextBuilder,
    detach_message,
)
from coding_agent_neo.events import EventDispatchError, EventEmitter, PendingEvent
from coding_agent_neo.executor import ToolExecutor
from coding_agent_neo.model_client import ModelClient, ModelClientError
from coding_agent_neo.models import (
    CorrelationId,
    EventEnvelope,
    EventType,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
    ToolResult,
    ToolResultStatus,
)
from coding_agent_neo.policy import ApprovalRequest, _invoke_approval_callable
from coding_agent_neo.runtime import (
    AgentRuntime,
    CancellationRequested,
    LimitReason,
)
from coding_agent_neo.tools.output import project_tool_result
from coding_agent_neo.tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 32
DEFAULT_MAX_TOOL_CALLS = 64
DEFAULT_MAX_PROTOCOL_ERRORS = 3
DEFAULT_MAX_WALL_SECONDS = 900.0

_INVALID_PROVIDER_DIAGNOSTICS = frozenset(
    {
        "missing_tool_call_id",
        "invalid_tool_call_id",
        "duplicate_tool_call_id",
    }
)


class ActiveToolsMismatchError(ValueError):
    """The Runtime and Registry do not describe one active-tool view."""


class LoopClosedError(RuntimeError):
    """A turn was requested after the Loop had closed its session."""


class _LoopLimitReached(RuntimeError):
    """Context preparation exhausted an existing per-Runtime limit."""

    def __init__(self, reason: LimitReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    """Observable outcome of one Agent turn."""

    state: RuntimeState
    assistant_text: str = ""
    reason: str | None = None
    limit_reason: LimitReason | None = None
    error_type: str | None = None
    budget: Mapping[str, int | float | None] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return self.state is RuntimeState.COMPLETED_TURN

    @property
    def text(self) -> str:
        return self.assistant_text

    @property
    def final_text(self) -> str:
        return self.assistant_text


TurnResult = AgentLoopResult


@dataclass(frozen=True, slots=True)
class _PlannedToolCall:
    call: NormalizedToolCall
    correlation_id: CorrelationId
    context_tool_call_id: str


class _StatefulApprovalPort:
    """Expose the approval wait in the Loop state without owning terminal I/O."""

    def __init__(self, loop: AgentLoop, port: Any) -> None:
        self._loop = loop
        self._port = port
        self.interactive = getattr(port, "interactive", True)

    def request_approval(self, request: ApprovalRequest) -> bool:
        callback = None
        for name in ("request_approval", "approve", "confirm", "ask"):
            candidate = getattr(self._port, name, None)
            if callable(candidate):
                callback = candidate
                break
        if callback is None and callable(self._port):
            callback = self._port
        if callback is None:
            raise TypeError("approval port must provide a callable approval method")
        self._loop.state = RuntimeState.WAITING_FOR_APPROVAL
        try:
            result = _invoke_approval_callable(callback, request)
        finally:
            if not self._loop._session_closed:
                self._loop.state = RuntimeState.RUNNING
        if not isinstance(result, bool):
            raise TypeError("approval response must be a boolean")
        return result


class _StoreFirstToolEventPublisher:
    """Make Store failure strict while tolerating observer-only failure."""

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter
        self.last_sequence: int | None = None

    def publish(self, event: Any) -> None:
        self.last_sequence = None
        try:
            report = self._emitter.publish(event)
        except EventDispatchError as exc:
            if exc.report.event is None:
                raise
            self.last_sequence = exc.report.event.sequence
            return
        if report.event is None:  # pragma: no cover - a successful Store returns an event.
            raise RuntimeError("tool event publication returned no canonical event")
        self.last_sequence = report.event.sequence


class AgentLoop:
    """A synchronous LLM -> tools -> results loop with explicit dependencies.

    ``run_turn`` leaves a successfully completed interactive session open.
    Call :meth:`close` when the assembly layer decides the session is done.
    Interrupted, limited, and failed turns are terminal and close the
    Environment automatically.
    """

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        event_emitter: EventEmitter,
        runtime: AgentRuntime,
        *,
        system_prompt: str,
        model_parameters: Mapping[str, Any] | None = None,
        approval_port: Any | None = None,
        interactive: bool = True,
        model_output_limit: int | None = None,
        context_builder: ContextBuilder | None = None,
        compactor: Compactor | None = None,
        context_window: int = 128_000,
        reserved_output_tokens: int = 4_096,
        compaction_threshold: float = 0.85,
        keep_recent_groups: int = 2,
        max_compactions_per_request: int = 8,
    ) -> None:
        if not callable(getattr(model_client, "complete", None)):
            raise TypeError("model_client must provide complete")
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if not isinstance(event_emitter, EventEmitter):
            raise TypeError("event_emitter must be an EventEmitter")
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an AgentRuntime")
        if not isinstance(interactive, bool):
            raise TypeError("interactive must be a boolean")
        if model_parameters is not None and not isinstance(model_parameters, Mapping):
            raise TypeError("model_parameters must be a mapping or None")
        if (
            isinstance(max_compactions_per_request, bool)
            or not isinstance(max_compactions_per_request, int)
            or max_compactions_per_request < 1
        ):
            raise ValueError("max_compactions_per_request must be a positive integer")

        self._validate_active_view(runtime, registry)
        store_session_id = getattr(event_emitter.store, "session_id", None)
        if store_session_id is not None and str(store_session_id) != str(runtime.session_id):
            raise ValueError("event store session ID does not match Runtime")

        self.model_client = model_client
        self.registry = registry
        self.event_emitter = event_emitter
        self.runtime = runtime
        self.context_builder = context_builder or ContextBuilder(
            system_prompt,
            context_window=context_window,
            reserved_output_tokens=reserved_output_tokens,
            compaction_threshold=compaction_threshold,
            keep_recent_groups=keep_recent_groups,
        )
        if self.context_builder.system_prompt != system_prompt:
            raise ValueError("context_builder and explicit system_prompt disagree")
        self.model_parameters = dict(model_parameters or {})
        self.compactor = compactor or Compactor(
            model_client,
            system_prompt=system_prompt,
            context_window=self.context_builder.context_window,
            reserved_output_tokens=self.context_builder.reserved_output_tokens,
            estimator=self.context_builder.estimator,
            model_parameters=self.model_parameters,
        )
        if self.compactor.system_prompt != system_prompt:
            raise ValueError("compactor and explicit system_prompt disagree")
        if (
            self.compactor.context_window != self.context_builder.context_window
            or self.compactor.reserved_output_tokens != self.context_builder.reserved_output_tokens
            or self.compactor.estimator != self.context_builder.estimator
        ):
            raise ValueError("compactor and context_builder budget configuration disagree")
        self.max_compactions_per_request = max_compactions_per_request
        self.model_output_limit = model_output_limit
        self.state = RuntimeState.RUNNING
        self._session_started = False
        self._session_closed = False
        self._environment_started = False
        self._turn_active = False
        self._last_assistant_text = ""
        self._issued_correlations: set[CorrelationId] = set()
        stateful_approval = (
            None if approval_port is None else _StatefulApprovalPort(self, approval_port)
        )
        self._tool_event_publisher = _StoreFirstToolEventPublisher(event_emitter)
        self.tool_executor = ToolExecutor(
            runtime,
            registry,
            approval_port=stateful_approval,
            interactive=interactive,
            event_publisher=self._tool_event_publisher,
            model_output_limit=model_output_limit,
            strict_event_publishing=True,
        )
        self._apply_default_limits()

    @staticmethod
    def _validate_active_view(runtime: AgentRuntime, registry: ToolRegistry) -> None:
        runtime_active = frozenset(runtime.active_tools)
        registry_active = registry.active_tools
        if runtime_active != registry_active:
            raise ActiveToolsMismatchError(
                "AgentRuntime.active_tools must equal the Registry active view"
            )

    def _apply_default_limits(self) -> None:
        budget = self.runtime.budget
        if budget.max_steps is None:
            budget.max_steps = DEFAULT_MAX_STEPS
        if budget.max_tool_calls is None:
            budget.max_tool_calls = DEFAULT_MAX_TOOL_CALLS
        if budget.max_protocol_errors is None:
            budget.max_protocol_errors = DEFAULT_MAX_PROTOCOL_ERRORS
        if budget.max_wall_seconds is None:
            budget.max_wall_seconds = DEFAULT_MAX_WALL_SECONDS
        if budget.deadline is None:
            started_at = budget.started_at
            if started_at is None:  # BudgetTracker initializes this; keep the boundary robust.
                started_at = budget.clock()
            budget.deadline = started_at + budget.max_wall_seconds

    def _new_correlation_id(self) -> CorrelationId:
        try:
            base = self.runtime.new_correlation_id()
        except Exception:
            base = CorrelationId(f"correlation_{uuid4().hex}")
        if base not in self._issued_correlations:
            self._issued_correlations.add(base)
            return base
        for suffix in range(1, 1025):
            try:
                candidate = CorrelationId(f"{base}_{suffix}")
            except ValueError:
                break
            if candidate not in self._issued_correlations:
                self._issued_correlations.add(candidate)
                return candidate
        candidate = CorrelationId(f"correlation_{uuid4().hex}")
        while candidate in self._issued_correlations:
            candidate = CorrelationId(f"correlation_{uuid4().hex}")
        self._issued_correlations.add(candidate)
        return candidate

    def _pending_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        correlation_id: CorrelationId | None = None,
        provider_tool_call_id: str | None = None,
    ) -> PendingEvent:
        return PendingEvent(
            session_id=self.runtime.session_id,
            agent_id=self.runtime.agent_id,
            parent_agent_id=self.runtime.parent_agent_id,
            timestamp=self.runtime.clock(),
            type=event_type,
            correlation_id=correlation_id,
            provider_tool_call_id=provider_tool_call_id,
            payload=payload,
        )

    def _emit(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        correlation_id: CorrelationId | None = None,
        provider_tool_call_id: str | None = None,
    ) -> EventEnvelope:
        event = self._pending_event(
            event_type,
            payload,
            correlation_id=correlation_id,
            provider_tool_call_id=provider_tool_call_id,
        )
        try:
            report = self.event_emitter.publish(event)
        except EventDispatchError as exc:
            # The canonical fact exists when only a renderer/observer failed.
            # A Store failure has no sequence and must end the Loop.
            if exc.report.event is None:
                raise
            return exc.report.event
        if report.event is None:  # pragma: no cover - successful Store contract.
            raise RuntimeError("event publication returned no canonical event")
        return report.event

    def _emit_best_effort(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
    ) -> None:
        try:
            self._emit(event_type, payload)
        except BaseException:
            return

    def _start_session(self) -> None:
        if self._session_started:
            return
        self._environment_started = True
        self.runtime.environment.start()
        self._emit(
            EventType.SESSION_START,
            {"state": RuntimeState.RUNNING.value},
        )
        self._emit(
            EventType.AGENT_START,
            {
                "state": RuntimeState.RUNNING.value,
                "active_tools": list(self.registry.active_names),
            },
        )
        self._session_started = True

    def _close_environment(self) -> None:
        if not self._environment_started:
            return
        try:
            self.runtime.environment.close()
        finally:
            self._environment_started = False

    def _finish_session(self, state: RuntimeState, reason: str) -> None:
        if self._session_closed:
            return
        self._emit_best_effort(
            EventType.AGENT_END,
            {"state": state.value, "reason": reason, "budget": self.runtime.budget.snapshot()},
        )
        self._emit_best_effort(
            EventType.SESSION_END,
            {"state": state.value, "reason": reason, "budget": self.runtime.budget.snapshot()},
        )
        try:
            self._close_environment()
        except BaseException:
            # End events are already durable when possible.  Closing is
            # best-effort on terminal paths and must not hide the turn result.
            pass
        self._session_closed = True

    def close(self, *, reason: str = "session_closed") -> None:
        """End an open session and close its Environment exactly once."""

        if not isinstance(reason, str) or not reason:
            raise ValueError("close reason must be a non-empty string")
        if self._session_closed:
            return
        state = self.state
        if state is RuntimeState.RUNNING:
            state = RuntimeState.COMPLETED_TURN
        self._finish_session(state, reason)

    def __enter__(self) -> AgentLoop:
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    def _append_message(
        self,
        message: Mapping[str, Any],
        *,
        sequence: int | None,
    ) -> None:
        self.runtime.context_state.append_message(
            detach_message(message),
            sequence=sequence,
        )

    def _plan_calls(self, calls: Sequence[NormalizedToolCall]) -> tuple[_PlannedToolCall, ...]:
        plans: list[_PlannedToolCall] = []
        for call in calls:
            correlation_id = self._new_correlation_id()
            provider_id = call.provider_tool_call_id
            context_id = str(provider_id) if provider_id is not None else str(correlation_id)
            if any(code in _INVALID_PROVIDER_DIAGNOSTICS for code in call.diagnostics):
                context_id = str(correlation_id)
            plans.append(_PlannedToolCall(call, correlation_id, context_id))
        return tuple(plans)

    @staticmethod
    def _assistant_message(
        response: NormalizedAssistantResponse,
        plans: Sequence[_PlannedToolCall],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": response.text}
        if plans:
            message["tool_calls"] = [
                {
                    "id": plan.context_tool_call_id,
                    "type": "function",
                    "function": {
                        "name": plan.call.name or "invalid_tool_name",
                        "arguments": plan.call.raw_arguments,
                    },
                }
                for plan in plans
            ]
        return message

    def _assistant_payload(
        self,
        response: NormalizedAssistantResponse,
        plans: Sequence[_PlannedToolCall],
    ) -> dict[str, Any]:
        return {
            "text": response.text,
            "finish_reason": response.finish_reason,
            "usage": None if response.usage is None else response.usage.to_dict(),
            "diagnostics": list(response.diagnostics),
            "tool_calls": [
                {
                    "correlation_id": str(plan.correlation_id),
                    "provider_tool_call_id": (
                        None
                        if plan.call.provider_tool_call_id is None
                        else str(plan.call.provider_tool_call_id)
                    ),
                    "name": (
                        plan.call.name
                        if plan.call.name in self.registry.registered_names
                        else "<invalid-tool-name>"
                    ),
                    "raw_arguments": plan.call.raw_arguments,
                    "diagnostics": list(plan.call.diagnostics),
                }
                for plan in plans
            ],
        }

    def _tool_message(
        self,
        result: ToolResult,
        context_tool_call_id: str,
    ) -> dict[str, Any]:
        projection = project_tool_result(result, self.model_output_limit)
        return {
            "role": "tool",
            "tool_call_id": context_tool_call_id,
            "content": json.dumps(
                projection.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ),
        }

    def _execute_plan(self, plan: _PlannedToolCall) -> ToolResult:
        call = plan.call
        provider_value: Any = call.provider_tool_call_id
        if any(code in _INVALID_PROVIDER_DIAGNOSTICS for code in call.diagnostics):
            # Route normalized missing/conflicting provider IDs through the
            # ToolExecutor's protocol boundary without inventing a valid ID.
            provider_value = ""
        return self.tool_executor.execute(
            call.name or "",
            call.raw_arguments,
            provider_tool_call_id=provider_value,
            correlation_id=plan.correlation_id,
        )

    def _skip_pending_plans(
        self,
        pending_plans: list[_PlannedToolCall],
        *,
        reason: str,
        status: ToolResultStatus,
    ) -> None:
        """Pair every remaining declaration without reaching Tool execution."""

        while pending_plans:
            plan = pending_plans.pop(0)
            result = self.tool_executor.skip(
                plan.call.name or "",
                plan.call.raw_arguments,
                reason=reason,
                status=status,
                provider_tool_call_id=plan.call.provider_tool_call_id,
                correlation_id=plan.correlation_id,
            )
            self._append_message(
                self._tool_message(result, plan.context_tool_call_id),
                sequence=self._tool_event_publisher.last_sequence,
            )

    def _consume_published_result(self, pending_plans: list[_PlannedToolCall]) -> None:
        """Consume a result published while a BaseException unwound execution."""

        if not pending_plans or not self.tool_executor.last_result_published:
            return
        result = self.tool_executor.last_result
        plan = pending_plans[0]
        if result is None or result.correlation_id != plan.correlation_id:
            return
        pending_plans.pop(0)
        self._append_message(
            self._tool_message(result, plan.context_tool_call_id),
            sequence=self._tool_event_publisher.last_sequence,
        )

    def _check_active_view(self) -> None:
        self._validate_active_view(self.runtime, self.registry)

    def _check_interrupt(self) -> None:
        self.runtime.cancellation.throw_if_cancelled()

    def _current_limit(self) -> LimitReason | None:
        """Return a limit that prevents another model step.

        Tool-call capacity is checked only when a declared call is about to
        execute, so a model may still summarize after using its final allowed
        tool call.  Likewise, zero protocol errors do not exhaust a
        zero-tolerance protocol budget until an error actually occurs.
        """

        budget = self.runtime.budget
        if budget.max_steps is not None and budget.model_steps >= budget.max_steps:
            return LimitReason.MODEL_STEPS
        if (
            budget.protocol_errors > 0
            and budget.max_protocol_errors is not None
            and budget.protocol_errors >= budget.max_protocol_errors
        ):
            return LimitReason.PROTOCOL_ERRORS
        if budget.deadline is not None and budget.clock() >= budget.deadline:
            return LimitReason.WALL_TIME
        return None

    def _record_usage(self, response: NormalizedAssistantResponse) -> None:
        usage = response.usage
        if usage is None:
            return
        self.runtime.budget.record_tokens(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    def _record_compaction_usage(self, outcome: CompactionOutcome) -> None:
        usage = outcome.usage
        if usage is None:
            return
        self.runtime.budget.record_tokens(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    def _check_context_preparation_allowed(self) -> None:
        self._check_interrupt()
        budget = self.runtime.budget
        if budget.deadline is not None and budget.clock() >= budget.deadline:
            raise _LoopLimitReached(LimitReason.WALL_TIME)

    def _emit_compaction(
        self,
        outcome: CompactionOutcome,
        *,
        degraded_through_sequence: int | None = None,
    ) -> None:
        self._emit(
            EventType.COMPACTION,
            {
                "status": "success" if outcome.succeeded else "failed",
                "forced": outcome.forced,
                "source_start_sequence": outcome.source_start_sequence,
                "source_end_sequence": outcome.source_end_sequence,
                "covered_through_sequence": self.runtime.context_state.covered_through_sequence,
                "degraded_through_sequence": degraded_through_sequence,
                "summary": outcome.summary,
                "error_type": outcome.error_type,
                "reason": outcome.reason,
            },
        )

    def _compact_once(self, *, forced: bool) -> bool:
        self._check_context_preparation_allowed()
        plan = self.context_builder.plan_compaction(self.runtime, forced=forced)
        if plan is None:
            return False
        state = self.runtime.context_state
        previous_projection_state = (
            state.latest_summary,
            state.covered_through_sequence,
            state.degraded_through_sequence,
            state.degraded_notice,
        )

        def restore_projection_state() -> None:
            (
                state.latest_summary,
                state.covered_through_sequence,
                state.degraded_through_sequence,
                state.degraded_notice,
            ) = previous_projection_state

        outcome = self.compactor.compact(self.runtime, plan)
        self._record_compaction_usage(outcome)
        if outcome.succeeded:
            try:
                self._emit_compaction(outcome)
            except BaseException:
                restore_projection_state()
                raise
            self._check_context_preparation_allowed()
            return True

        selected_groups = tuple(
            group for group in plan.groups if group.end_sequence <= outcome.source_end_sequence
        )
        if not selected_groups:
            selected_groups = (plan.groups[0],)
        fallback_plan = type(plan)(
            old_summary=plan.old_summary,
            groups=selected_groups,
            forced=plan.forced,
            previous_covered_sequence=plan.previous_covered_sequence,
        )
        self.context_builder.apply_degraded_fallback(self.runtime, fallback_plan)
        try:
            self._emit_compaction(
                outcome,
                degraded_through_sequence=self.runtime.context_state.degraded_through_sequence,
            )
        except BaseException:
            restore_projection_state()
            raise
        self._check_context_preparation_allowed()
        return False

    def _prepare_context(self, *, forced: bool = False) -> list[dict[str, Any]]:
        schemas = self.registry.active_schemas()
        self._check_context_preparation_allowed()
        if forced:
            self._compact_once(forced=True)
            projection = self.context_builder.project(self.runtime, schemas)
            if projection.estimate.exceeds_window:
                raise ContextWindowExceeded(
                    projection.estimate,
                    reason="context_window_exceeded_after_forced_compaction",
                )
            return projection.as_messages()

        for _attempt in range(self.max_compactions_per_request):
            self._check_context_preparation_allowed()
            projection = self.context_builder.project(self.runtime, schemas)
            if not projection.estimate.needs_compaction:
                return projection.as_messages()
            if not self._compact_once(forced=False):
                fallback = self.context_builder.project(self.runtime, schemas)
                if fallback.estimate.exceeds_window:
                    raise ContextWindowExceeded(
                        fallback.estimate,
                        reason="context_window_exceeded_after_compaction_fallback",
                    )
                return fallback.as_messages()

        projection = self.context_builder.project(self.runtime, schemas)
        if projection.estimate.needs_compaction:
            raise ContextWindowExceeded(
                projection.estimate,
                reason="context_compaction_attempt_limit_reached",
            )
        return projection.as_messages()

    def _complete_model_request(self) -> NormalizedAssistantResponse:
        schemas = self.registry.active_schemas()
        messages = self._prepare_context()
        self._check_context_preparation_allowed()
        try:
            return self.model_client.complete(messages, schemas, self.model_parameters)
        except ModelClientError as error:
            if not error.context_overflow:
                raise
        self._emit(
            EventType.RETRY,
            {
                "reason": "context_overflow",
                "forced_compaction": True,
                "attempt": 1,
                "max_attempts": 1,
            },
        )
        forced_messages = self._prepare_context(forced=True)
        self._check_context_preparation_allowed()
        return self.model_client.complete(
            forced_messages,
            schemas,
            self.model_parameters,
        )

    def _result(
        self,
        state: RuntimeState,
        *,
        reason: str | None = None,
        limit_reason: LimitReason | None = None,
        error_type: str | None = None,
    ) -> AgentLoopResult:
        self.state = state
        return AgentLoopResult(
            state=state,
            assistant_text=self._last_assistant_text,
            reason=reason,
            limit_reason=limit_reason,
            error_type=error_type,
            budget=self.runtime.budget.snapshot(),
        )

    def _end_turn(
        self,
        state: RuntimeState,
        reason: str,
        *,
        limit_reason: LimitReason | None = None,
    ) -> AgentLoopResult:
        self._emit(
            EventType.TURN_END,
            {
                "state": state.value,
                "reason": reason,
                "limit_reason": None if limit_reason is None else limit_reason.value,
                "assistant_text": self._last_assistant_text,
                "budget": self.runtime.budget.snapshot(),
            },
        )
        self._turn_active = False
        result = self._result(state, reason=reason, limit_reason=limit_reason)
        if state is not RuntimeState.COMPLETED_TURN:
            self._finish_session(state, reason)
        return result

    def _limit_result(self, reason: LimitReason) -> AgentLoopResult:
        return self._end_turn(
            RuntimeState.LIMIT_REACHED,
            f"limit_reached:{reason.value}",
            limit_reason=reason,
        )

    @staticmethod
    def _safe_error_payload(error: BaseException) -> dict[str, Any]:
        frames = traceback.extract_tb(error.__traceback__)
        payload: dict[str, Any] = {
            "state": RuntimeState.FAILED.value,
            "error_type": type(error).__name__,
            "message": "unhandled system exception",
            "stack": [
                {
                    "filename": frame.filename,
                    "line_number": frame.lineno,
                    "function": frame.name,
                }
                for frame in frames[-16:]
            ],
        }
        if isinstance(error, ModelClientError):
            payload["message"] = error.reason
            payload["model_error"] = error.as_dict()
        return payload

    def _failed_result(self, error: BaseException) -> AgentLoopResult:
        payload = self._safe_error_payload(error)
        failure_reason = (
            "context_overflow_after_forced_compaction"
            if isinstance(error, ModelClientError) and error.context_overflow
            else "unhandled_system_exception"
        )
        self._emit_best_effort(EventType.ERROR, payload)
        if self._turn_active:
            self._emit_best_effort(
                EventType.TURN_END,
                {
                    "state": RuntimeState.FAILED.value,
                    "reason": failure_reason,
                    "assistant_text": self._last_assistant_text,
                    "budget": self.runtime.budget.snapshot(),
                },
            )
        self._turn_active = False
        result = self._result(
            RuntimeState.FAILED,
            reason=failure_reason,
            error_type=type(error).__name__,
        )
        self._finish_session(RuntimeState.FAILED, result.reason or "failed")
        return result

    def _interrupted_result(self, reason: str) -> AgentLoopResult:
        if self._turn_active:
            self._emit_best_effort(
                EventType.TURN_END,
                {
                    "state": RuntimeState.INTERRUPTED.value,
                    "reason": reason,
                    "assistant_text": self._last_assistant_text,
                    "budget": self.runtime.budget.snapshot(),
                },
            )
        self._turn_active = False
        result = self._result(RuntimeState.INTERRUPTED, reason=reason)
        self._finish_session(RuntimeState.INTERRUPTED, reason)
        return result

    def _recover_empty_response(self, response: NormalizedAssistantResponse) -> None:
        budget = self.runtime.budget
        budget.record_protocol_error()
        event = self._emit(
            EventType.ERROR,
            {
                "state": RuntimeState.RUNNING.value,
                "recoverable": True,
                "reason": "empty_assistant_response",
                "diagnostics": list(response.diagnostics),
                "consecutive_protocol_errors": budget.protocol_errors,
            },
        )
        self._append_message(
            {
                "role": "system",
                "content": (
                    "Protocol error: return non-empty assistant text or one or more "
                    "native tool calls. Please correct the response."
                ),
            },
            sequence=event.sequence,
        )

    def run_turn(self, user_message: str) -> AgentLoopResult:
        """Run one user turn, leaving a successful session open for follow-up."""

        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be a non-empty string")
        if self._session_closed:
            raise LoopClosedError("Agent Loop session is closed")
        if self._turn_active:
            raise RuntimeError("Agent Loop is already running a turn")

        self.state = RuntimeState.RUNNING
        self._turn_active = True
        self._last_assistant_text = ""
        pending_plans: list[_PlannedToolCall] = []
        try:
            self._check_active_view()
            self._start_session()
            user_event = self._emit(EventType.USER_MESSAGE, {"text": user_message})
            self._append_message(
                {"role": "user", "content": user_message},
                sequence=user_event.sequence,
            )

            while True:
                self._check_interrupt()
                self._check_active_view()
                limit = self._current_limit()
                if limit is not None:
                    return self._limit_result(limit)

                self.runtime.budget.record_model_step()
                response = self._complete_model_request()
                if not isinstance(response, NormalizedAssistantResponse):
                    raise TypeError("model_client.complete must return NormalizedAssistantResponse")
                self._record_usage(response)
                plans = self._plan_calls(response.tool_calls)
                assistant_message = self._assistant_message(response, plans)
                assistant_event = self._emit(
                    EventType.ASSISTANT_MESSAGE,
                    self._assistant_payload(response, plans),
                )
                self._append_message(
                    assistant_message,
                    sequence=assistant_event.sequence,
                )
                pending_plans = list(plans)
                if response.text:
                    self._last_assistant_text = response.text

                if self.runtime.cancellation.is_cancelled:
                    self._skip_pending_plans(
                        pending_plans,
                        reason="interrupted",
                        status=ToolResultStatus.CANCELLED,
                    )
                    return self._interrupted_result(self.runtime.cancellation.reason or "cancelled")
                if self.runtime.budget.deadline is not None and (
                    self.runtime.budget.clock() >= self.runtime.budget.deadline
                ):
                    self._skip_pending_plans(
                        pending_plans,
                        reason=LimitReason.WALL_TIME.value,
                        status=ToolResultStatus.DENIED,
                    )
                    return self._limit_result(LimitReason.WALL_TIME)

                if not plans:
                    if response.text:
                        self.runtime.budget.protocol_errors = 0
                        return self._end_turn(
                            RuntimeState.COMPLETED_TURN,
                            "assistant_completed",
                        )
                    self._recover_empty_response(response)
                    if (
                        self.runtime.budget.max_protocol_errors is not None
                        and self.runtime.budget.protocol_errors
                        >= self.runtime.budget.max_protocol_errors
                    ):
                        return self._limit_result(LimitReason.PROTOCOL_ERRORS)
                    continue

                while pending_plans:
                    plan = pending_plans[0]
                    self._check_interrupt()
                    self._check_active_view()
                    if self.runtime.budget.deadline is not None and (
                        self.runtime.budget.clock() >= self.runtime.budget.deadline
                    ):
                        self._skip_pending_plans(
                            pending_plans,
                            reason=LimitReason.WALL_TIME.value,
                            status=ToolResultStatus.DENIED,
                        )
                        return self._limit_result(LimitReason.WALL_TIME)
                    if (
                        self.runtime.budget.max_tool_calls is not None
                        and self.runtime.budget.tool_calls >= self.runtime.budget.max_tool_calls
                    ):
                        self._skip_pending_plans(
                            pending_plans,
                            reason=LimitReason.TOOL_CALLS.value,
                            status=ToolResultStatus.DENIED,
                        )
                        return self._limit_result(LimitReason.TOOL_CALLS)

                    self.runtime.budget.record_tool_call()
                    result = self._execute_plan(plan)
                    self._append_message(
                        self._tool_message(result, plan.context_tool_call_id),
                        sequence=self._tool_event_publisher.last_sequence,
                    )
                    pending_plans.pop(0)
                    if result.status is ToolResultStatus.INVALID:
                        self.runtime.budget.record_protocol_error()
                    else:
                        self.runtime.budget.protocol_errors = 0

                    if self.runtime.cancellation.is_cancelled:
                        self._skip_pending_plans(
                            pending_plans,
                            reason="interrupted",
                            status=ToolResultStatus.CANCELLED,
                        )
                        return self._interrupted_result(
                            self.runtime.cancellation.reason or "cancelled"
                        )
                    if (
                        self.runtime.budget.max_protocol_errors is not None
                        and self.runtime.budget.protocol_errors
                        >= self.runtime.budget.max_protocol_errors
                    ):
                        self._skip_pending_plans(
                            pending_plans,
                            reason=LimitReason.PROTOCOL_ERRORS.value,
                            status=ToolResultStatus.DENIED,
                        )
                        return self._limit_result(LimitReason.PROTOCOL_ERRORS)

        except _LoopLimitReached as error:
            return self._limit_result(error.reason)
        except ContextWindowExceeded:
            return self._limit_result(LimitReason.CONTEXT_WINDOW)
        except (KeyboardInterrupt, asyncio.CancelledError, CancellationRequested) as exc:
            try:
                self._consume_published_result(pending_plans)
                self._skip_pending_plans(
                    pending_plans,
                    reason="interrupted",
                    status=ToolResultStatus.CANCELLED,
                )
            except BaseException as publication_error:
                return self._failed_result(publication_error)
            if not self.runtime.cancellation.is_cancelled:
                self.runtime.cancellation.cancel("keyboard_interrupt")
            reason = self.runtime.cancellation.reason or type(exc).__name__
            return self._interrupted_result(reason)
        except BaseException as exc:
            failure = exc
            try:
                self._consume_published_result(pending_plans)
                self._skip_pending_plans(
                    pending_plans,
                    reason="agent_failed",
                    status=ToolResultStatus.ERROR,
                )
            except BaseException as publication_error:
                failure = publication_error
            return self._failed_result(failure)

    run = run_turn


__all__ = [
    "ActiveToolsMismatchError",
    "AgentLoop",
    "AgentLoopResult",
    "DEFAULT_MAX_PROTOCOL_ERRORS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_MAX_WALL_SECONDS",
    "LoopClosedError",
    "SimpleContextBuilder",
    "TurnResult",
]
