"""Frontend contract tests: CLI talks only through AgentBackend commands/events."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import (
    ScriptedModel,
    bash_call,
    make_backend,
    wait_session,
)

from coding_agent_neo.assembly import build_local_backend
from coding_agent_neo.backend import ApprovalResponse, SubmitTask
from coding_agent_neo.cli import (
    EXIT_INTERRUPTED,
    EXIT_SUCCESS,
    run_cli,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventType, NormalizedAssistantResponse, RuntimeState
from coding_agent_neo.session import read_session


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


def backend_factory_for(model, environment=None):
    def factory(cfg, *, interactive, **_kwargs):
        return build_local_backend(
            cfg,
            interactive=interactive,
            model_client=model,
            environment=environment,
            approval_timeout_seconds=2.0,
            worker_shutdown_timeout_seconds=5.0,
            event_poll_timeout_seconds=0.05,
            fsync=False,
        )

    return factory


def test_cli_frontend_does_not_need_agent_objects(tmp_path: Path) -> None:
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        config(tmp_path),
        task="inspect",
        interactive=False,
        input_stream=StringIO(),
        output_stream=stdout,
        error_stream=stderr,
        backend_factory=backend_factory_for(
            ScriptedModel([NormalizedAssistantResponse(text="done")]),
            FakeExecutionEnvironment(),
        ),
    )
    assert code == EXIT_SUCCESS
    assert stdout.getvalue() == "done\n"
    assert "assistant> done" in stderr.getvalue()


def test_interactive_approval_roundtrip_persists_request_before_execution(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel([bash_call("printf approved"), NormalizedAssistantResponse(text="done")])
    output = StringIO()
    code = run_cli(
        config(tmp_path, approval_mode="ask"),
        task="confirm",
        interactive=True,
        input_stream=StringIO("yes\n"),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=backend_factory_for(model, environment),
    )
    assert code == EXIT_SUCCESS
    events = read_session(next((tmp_path / "sessions").glob("*.jsonl"))).events
    types = [event.type for event in events]
    request_index = types.index(EventType.APPROVAL_REQUEST)
    decision_index = types.index(EventType.POLICY_DECISION)
    result_index = types.index(EventType.TOOL_RESULT)
    assert request_index < decision_index < result_index
    request = events[request_index]
    decision = events[decision_index]
    assert request.payload["request_id"] == str(request.correlation_id)
    assert str(decision.correlation_id) == str(request.correlation_id)
    assert decision.payload["decision"] == "allow"
    assert any(call.operation == "run_command" for call in environment.calls)
    assert "Approve bash command" in output.getvalue()


def test_rejected_approval_shares_correlation_and_skips_environment(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    model = ScriptedModel([bash_call("printf blocked"), NormalizedAssistantResponse(text="denied")])
    output = StringIO()
    code = run_cli(
        config(tmp_path, approval_mode="ask"),
        task="confirm",
        interactive=True,
        input_stream=StringIO("n\n"),
        output_stream=output,
        error_stream=StringIO(),
        backend_factory=backend_factory_for(model, environment),
    )
    assert code == EXIT_SUCCESS
    events = read_session(next((tmp_path / "sessions").glob("*.jsonl"))).events
    request = next(event for event in events if event.type == EventType.APPROVAL_REQUEST)
    decision = next(event for event in events if event.type == EventType.POLICY_DECISION)
    assert str(decision.correlation_id) == str(request.correlation_id)
    assert decision.payload["decision"] == "deny"
    assert all(call.operation != "run_command" for call in environment.calls)


def test_last_state_drives_interrupted_exit_contract(tmp_path: Path) -> None:
    code = run_cli(
        config(tmp_path),
        task="interrupt",
        interactive=False,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=StringIO(),
        backend_factory=backend_factory_for(
            ScriptedModel([KeyboardInterrupt()]),
            FakeExecutionEnvironment(),
        ),
    )
    assert code == EXIT_INTERRUPTED
    events = read_session(next((tmp_path / "sessions").glob("*.jsonl"))).events
    assert events[-1].payload["state"] == RuntimeState.INTERRUPTED


def test_backend_approval_response_is_the_only_execution_gate(tmp_path: Path) -> None:
    environment = FakeExecutionEnvironment()
    backend = make_backend(
        tmp_path,
        ScriptedModel([bash_call("printf gated"), NormalizedAssistantResponse(text="done")]),
        environment=environment,
    )
    try:
        backend.send(SubmitTask("gate"))
        events = wait_session(tmp_path, lambda event: event.type == EventType.APPROVAL_REQUEST)
        request = next(event for event in events if event.type == EventType.APPROVAL_REQUEST)
        assert all(call.operation != "run_command" for call in environment.calls)
        backend.send(ApprovalResponse(str(request.payload["request_id"]), True))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        assert any(call.operation == "run_command" for call in environment.calls)
        assert json.loads(request.payload["arguments_summary"])
    finally:
        backend.close()
