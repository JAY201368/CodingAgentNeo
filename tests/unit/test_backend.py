"""Unit tests for the in-process Agent backend, event stream, and approval channel."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.assembly import build_local_backend
from coding_agent_neo.backend import (
    ApprovalResponse,
    BackendClosedError,
    CloseSession,
    Interrupt,
    SubmitTask,
    TurnInProgressError,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import (
    EventType,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
)
from coding_agent_neo.session import read_session

SHORT_APPROVAL = 0.3
SHORT_SHUTDOWN = 2.0
SHORT_POLL = 0.05


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools, parameters):
        del messages, tools, parameters
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def config(tmp_path: Path, **changes) -> AppConfig:
    values = {
        "workspace": tmp_path,
        "session_dir": tmp_path / "sessions",
        "api_key": "placeholder",
        "context_window": 8000,
        "reserved_output_tokens": 1000,
    }
    values.update(changes)
    return AppConfig(**values)


def make_backend(
    tmp_path: Path,
    model,
    *,
    interactive: bool = True,
    environment=None,
    approval_timeout_seconds: float = SHORT_APPROVAL,
    **changes,
):
    return build_local_backend(
        config(tmp_path, **changes),
        interactive=interactive,
        model_client=model,
        environment=environment,
        approval_timeout_seconds=approval_timeout_seconds,
        worker_shutdown_timeout_seconds=SHORT_SHUTDOWN,
        event_poll_timeout_seconds=SHORT_POLL,
        fsync=False,
    )


def bash_call(command: str) -> NormalizedAssistantResponse:
    return NormalizedAssistantResponse(
        tool_calls=(
            NormalizedToolCall(
                provider_tool_call_id="provider_bash",
                name="bash",
                raw_arguments=json.dumps({"command": command}),
                arguments_valid=True,
            ),
        )
    )


def session_events(tmp_path: Path):
    path = next((tmp_path / "sessions").glob("*.jsonl"))
    return read_session(path).events


def wait_session(tmp_path: Path, predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = session_events(tmp_path)
        except StopIteration:
            time.sleep(SHORT_POLL)
            continue
        if any(predicate(event) for event in events):
            return events
        time.sleep(SHORT_POLL)
    raise TimeoutError("timed out waiting for persisted event")


def test_commands_are_json_serializable_without_callables() -> None:
    commands = (
        SubmitTask("inspect the workspace"),
        ApprovalResponse("correlation_1", True),
        Interrupt("user"),
        CloseSession("done"),
    )
    for command in commands:
        payload = command.to_dict()
        encoded = json.dumps(payload)
        assert json.loads(encoded) == payload
        assert not any(callable(value) for value in payload.values())


def test_submit_task_during_turn_is_rejected(tmp_path: Path) -> None:
    class SlowModel(ScriptedModel):
        def complete(self, messages, tools, parameters):
            time.sleep(0.4)
            return super().complete(messages, tools, parameters)

    model = SlowModel([NormalizedAssistantResponse(text="done")])
    backend = make_backend(tmp_path, model, environment=FakeExecutionEnvironment())
    try:
        backend.send(SubmitTask("first"))
        wait_session(tmp_path, lambda event: event.type == EventType.SESSION_START)
        with pytest.raises(TurnInProgressError):
            backend.send(SubmitTask("second"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        assert backend.last_state is RuntimeState.COMPLETED_TURN
    finally:
        backend.close()


def test_submit_after_close_is_rejected(tmp_path: Path) -> None:
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="done")]),
        environment=FakeExecutionEnvironment(),
    )
    backend.close()
    with pytest.raises(BackendClosedError):
        backend.send(SubmitTask("too late"))


def _assert_denied_without_command(tmp_path: Path, on_request) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel(
        [bash_call("printf should-not-run"), NormalizedAssistantResponse(text="denied")]
    )
    backend = make_backend(tmp_path, model, environment=environment)
    try:
        backend.send(SubmitTask("ask"))
        wait_session(tmp_path, lambda event: event.type == EventType.APPROVAL_REQUEST)
        on_request(backend)
        events = wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        decisions = [event for event in events if event.type == EventType.POLICY_DECISION]
        assert decisions
        assert decisions[-1].payload["decision"] == "deny"
        assert decisions[-1].payload["reason"]
        assert all(call.operation != "run_command" for call in environment.calls)
    finally:
        backend.close()


def test_approval_timeout_is_fail_closed(tmp_path: Path) -> None:
    _assert_denied_without_command(tmp_path, lambda _backend: None)


def test_approval_interrupt_is_fail_closed(tmp_path: Path) -> None:
    _assert_denied_without_command(
        tmp_path, lambda backend: backend.send(Interrupt("stop-approval"))
    )


def test_approval_close_is_fail_closed(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel(
        [bash_call("printf should-not-run"), NormalizedAssistantResponse(text="denied")]
    )
    backend = make_backend(tmp_path, model, environment=environment)
    try:
        backend.send(SubmitTask("ask"))
        wait_session(tmp_path, lambda event: event.type == EventType.APPROVAL_REQUEST)
        backend.close()
        events = wait_session(tmp_path, lambda event: event.type == EventType.POLICY_DECISION)
        decisions = [event for event in events if event.type == EventType.POLICY_DECISION]
        assert decisions
        assert decisions[-1].payload["decision"] == "deny"
        assert all(call.operation != "run_command" for call in environment.calls)
    finally:
        backend.close()


def test_approval_request_id_mismatch_is_fail_closed(tmp_path: Path) -> None:
    _assert_denied_without_command(
        tmp_path,
        lambda backend: backend.send(ApprovalResponse("not-the-pending-id", True)),
    )


def test_noninteractive_ask_does_not_emit_approval_request(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel([bash_call("printf blocked"), NormalizedAssistantResponse(text="denied")])
    backend = make_backend(
        tmp_path,
        model,
        interactive=False,
        environment=environment,
        approval_mode="ask",
    )
    try:
        backend.send(SubmitTask("ask"))
        events = wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        assert all(event.type != EventType.APPROVAL_REQUEST for event in events)
        decisions = [event for event in events if event.type == EventType.POLICY_DECISION]
        assert decisions
        assert decisions[-1].payload["decision"] == "deny"
        assert all(call.operation != "run_command" for call in environment.calls)
    finally:
        backend.close()


def test_approved_bash_shares_correlation_and_executes(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel([bash_call("printf approved"), NormalizedAssistantResponse(text="done")])
    backend = make_backend(tmp_path, model, environment=environment)
    try:
        backend.send(SubmitTask("confirm"))
        events = wait_session(tmp_path, lambda event: event.type == EventType.APPROVAL_REQUEST)
        request = [event for event in events if event.type == EventType.APPROVAL_REQUEST][-1]
        request_id = str(request.payload["request_id"])
        assert request_id == str(request.correlation_id)
        backend.send(ApprovalResponse(request_id, True))
        events = wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        decisions = [event for event in events if event.type == EventType.POLICY_DECISION]
        assert decisions[-1].payload["decision"] == "allow"
        assert str(decisions[-1].correlation_id) == request_id
        assert any(call.operation == "run_command" for call in environment.calls)
    finally:
        backend.close()


def test_event_cursor_is_contiguous_and_reenterable(tmp_path: Path) -> None:
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="done")]),
        environment=FakeExecutionEnvironment(),
    )
    try:
        backend.send(SubmitTask("inspect"))
        first: list = []
        for event in backend.events(since=0):
            first.append(event)
            if event.type == EventType.USER_MESSAGE:
                break
        rest: list = []
        for event in backend.events(since=first[-1].sequence):
            rest.append(event)
            if event.type == EventType.TURN_END:
                break
        sequences = [event.sequence for event in [*first, *rest]]
        assert sequences == list(range(sequences[0], sequences[-1] + 1))
        assert len(sequences) == len(set(sequences))
        persisted = [event.sequence for event in session_events(tmp_path)]
        assert sequences == persisted[: len(sequences)]
    finally:
        backend.close()


def test_slow_consumer_does_not_block_persistence(tmp_path: Path) -> None:
    backend = make_backend(
        tmp_path,
        ScriptedModel([NormalizedAssistantResponse(text="done")]),
        environment=FakeExecutionEnvironment(),
    )
    received: list = []
    started = threading.Event()

    def consume() -> None:
        for event in backend.events(since=0):
            received.append(event)
            if event.type == EventType.USER_MESSAGE:
                started.set()
                time.sleep(0.4)
            if event.type == EventType.TURN_END:
                break

    worker = threading.Thread(target=consume)
    try:
        backend.send(SubmitTask("inspect"))
        worker.start()
        assert started.wait(timeout=2)
        deadline = time.monotonic() + 1.0
        persisted = []
        while time.monotonic() < deadline:
            try:
                persisted = session_events(tmp_path)
            except StopIteration:
                time.sleep(SHORT_POLL)
                continue
            types = {event.type for event in persisted}
            if EventType.ASSISTANT_MESSAGE in types or EventType.TURN_END in types:
                break
            time.sleep(SHORT_POLL)
        types = {event.type for event in persisted}
        assert EventType.ASSISTANT_MESSAGE in types or EventType.TURN_END in types
        worker.join(timeout=2)
        assert any(event.type == EventType.TURN_END for event in received)
    finally:
        backend.close()
        worker.join(timeout=2)


def test_interrupt_stops_running_bash(tmp_path: Path) -> None:
    model = ScriptedModel(
        [bash_call("sleep 30"), NormalizedAssistantResponse(text="should-not-finish")]
    )
    backend = make_backend(tmp_path, model, interactive=False, approval_mode="auto")
    started = time.monotonic()
    try:
        backend.send(SubmitTask("sleep"))
        wait_session(tmp_path, lambda event: event.type == EventType.TOOL_CALL)
        backend.send(Interrupt("stop-bash"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0
        assert backend.last_state is RuntimeState.INTERRUPTED
        persisted = session_events(tmp_path)
        results = [event for event in persisted if event.type == EventType.TOOL_RESULT]
        assert results
        result = results[0].payload.get("result", results[0].payload)
        assert result.get("exit_code") in {130, None} or str(result.get("status")) in {
            "cancelled",
            "error",
        }
    finally:
        backend.close()
    assert time.monotonic() - started < 8.0
