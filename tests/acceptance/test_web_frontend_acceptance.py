"""Scripted end-to-end evidence for the Web adapter delivery.

These tests exercise the local composition boundary with a real
``AgentBackendService`` and the HTTP/SSE ASGI adapter.  The model and
environment are deliberately scripted and side-effect free: this proves the
transport and frontend-facing facts without pretending to prove a live model
gateway or host-shell isolation.
"""

from __future__ import annotations

import ast
import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.unit.fake_environment import EnvironmentCall, FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, bash_call

from coding_agent_neo.assembly import build_agent_backend_provider
from coding_agent_neo.config import AppConfig
from coding_agent_neo.environment.base import RunCommandRequest
from coding_agent_neo.models import (
    CommandResult,
    EnvironmentStatus,
    EventEnvelope,
    NormalizedAssistantResponse,
    NormalizedToolCall,
    RuntimeState,
)
from coding_agent_neo.transports.http import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "coding_agent_neo"
pytestmark = pytest.mark.acceptance


def _config(tmp_path: Path, **changes: Any) -> AppConfig:
    values: dict[str, Any] = {
        "workspace": tmp_path,
        "api_key": "placeholder",
        "context_window": 8_000,
        "reserved_output_tokens": 1_000,
    }
    values.update(changes)
    return AppConfig(**values)


class _BackendProvider:
    """Minimal provider fake for tests that exercise only live HTTP routes."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50):
        del cursor, limit
        raise AssertionError("live-route acceptance test must not read history")

    def read_session_events(self, session_id: str, *, since: int = 0, limit: int = 200):
        del session_id, since, limit
        raise AssertionError("live-route acceptance test must not read history")

    def create_session(self, *, resume_session_id: str | None = None):
        del resume_session_id
        return self.backend


class SequencedEnvironment(FakeExecutionEnvironment):
    """Return deterministic command outcomes without invoking a shell."""

    def __init__(self, results: list[CommandResult]) -> None:
        super().__init__()
        self._results = list(results)

    def run_command(
        self,
        request: RunCommandRequest,
        cancellation,
    ) -> CommandResult:
        self.calls.append(EnvironmentCall("run_command", request, cancellation))
        if cancellation.is_cancelled:
            return CommandResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
        if self._results:
            return self._results.pop(0)
        return CommandResult(status=EnvironmentStatus.SUCCESS, exit_code=0)


def _app(tmp_path: Path, model: Any, environment: Any, **config_changes: Any):
    config = _config(tmp_path, **config_changes)
    provider = build_agent_backend_provider(
        config,
        interactive=True,
        model_client=model,
        environment=environment,
        approval_timeout_seconds=2.0,
        worker_shutdown_timeout_seconds=2.0,
        event_poll_timeout_seconds=0.02,
        fsync=False,
    )

    return create_app(
        provider,
        keepalive_seconds=0.02,
        close_timeout_seconds=2.0,
    )


class _LiveServer:
    """Run an ASGI app on an ephemeral loopback port for real SSE streaming."""

    def __init__(self, app: Any) -> None:
        pytest.importorskip("httpx")
        pytest.importorskip("uvicorn")
        import httpx
        import uvicorn

        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                log_level="critical",
                access_log=False,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self._server.started:
            self._server.should_exit = True
            self._thread.join(timeout=2.0)
            raise RuntimeError("scripted Web server did not start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}")

    def __enter__(self) -> _LiveServer:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.client.close()
        self._server.should_exit = True
        self._thread.join(timeout=3.0)


def _session_id(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["transport_session_id"]


def _sse_frames(response) -> Iterator[dict[str, Any]]:
    frame: dict[str, Any] = {}
    for raw_line in response.iter_lines():
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line:
            if frame:
                yield frame
                frame = {}
            continue
        if line.startswith("id: "):
            frame["id"] = int(line[4:])
        elif line.startswith("event: "):
            frame["event"] = line[7:]
        elif line.startswith("data: "):
            frame["data"] = json.loads(line[6:])
    if frame:
        yield frame


def _read_until(
    client: TestClient,
    transport_id: str,
    cursor: int,
    predicate,
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    with client.stream(
        "GET",
        f"/api/v1/sessions/{transport_id}/events?since={cursor}",
    ) as response:
        assert response.status_code == 200, response.text
        for frame in _sse_frames(response):
            frames.append(frame)
            if predicate(frame["data"]):
                return frames
    raise AssertionError("scripted event stream ended before the expected event")


def _wait_for_state(client: TestClient, transport_id: str, expected: str) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{transport_id}")
        assert response.status_code == 200, response.text
        if response.json()["state"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"session did not reach state {expected}")


def _send(client: TestClient, transport_id: str, command: dict[str, Any]) -> None:
    response = client.post(
        f"/api/v1/sessions/{transport_id}/commands",
        json=command,
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": True}


def test_scripted_web_approval_tools_followup_and_disconnect_replay(tmp_path: Path) -> None:
    """Cover the primary Web journey over the real shared backend service."""

    environment = SequencedEnvironment(
        [
            CommandResult(stdout="tool ok", exit_code=0),
            CommandResult(
                status=EnvironmentStatus.ERROR,
                stderr="tool failed",
                exit_code=7,
            ),
        ]
    )
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(
                tool_calls=(
                    NormalizedToolCall(
                        provider_tool_call_id="provider_success",
                        name="bash",
                        raw_arguments=json.dumps({"command": "first"}),
                        arguments_valid=True,
                    ),
                    NormalizedToolCall(
                        provider_tool_call_id="provider_failure",
                        name="bash",
                        raw_arguments=json.dumps({"command": "second"}),
                        arguments_valid=True,
                    ),
                )
            ),
            NormalizedAssistantResponse(text="first turn completed"),
            NormalizedAssistantResponse(text="follow-up completed"),
        ]
    )

    with _LiveServer(_app(tmp_path, model, environment, approval_mode="ask")) as server:
        client = server.client
        transport_id = _session_id(client)
        _send(
            client,
            transport_id,
            {"type": "SubmitTask", "text": "run the scripted tool journey"},
        )

        # Consume one fact and disconnect before the approval event.  The
        # subsequent subscription must replay the canonical gap from that
        # last successful cursor.
        first_frame = _read_until(
            client,
            transport_id,
            0,
            lambda data: data["type"] == "session_start",
        )[-1]
        first_cursor = first_frame["id"]
        _wait_for_state(client, transport_id, "WAITING_FOR_APPROVAL")

        replayed = _read_until(
            client,
            transport_id,
            first_cursor,
            lambda data: data["type"] == "approval_request",
        )
        assert [frame["id"] for frame in replayed] == list(
            range(first_cursor + 1, replayed[-1]["id"] + 1)
        )
        approval = replayed[-1]["data"]
        request_id = approval["payload"]["request_id"]
        assert request_id and request_id == approval["correlation_id"]
        _send(
            client,
            transport_id,
            {"type": "ApprovalResponse", "request_id": request_id, "approved": True},
        )

        second_approval = _read_until(
            client,
            transport_id,
            replayed[-1]["id"],
            lambda data: (
                data["type"] == "approval_request"
                and data["payload"].get("request_id") != request_id
            ),
        )
        second_request_id = second_approval[-1]["data"]["payload"]["request_id"]
        assert second_request_id and second_request_id != request_id
        _send(
            client,
            transport_id,
            {"type": "ApprovalResponse", "request_id": second_request_id, "approved": True},
        )
        after_approval = _read_until(
            client,
            transport_id,
            second_approval[-1]["id"],
            lambda data: data["type"] == "turn_end",
        )
        complete_turn = [*second_approval, *after_approval]
        after_types = [frame["data"]["type"] for frame in complete_turn]
        assert after_types[:2] == ["policy_decision", "tool_result"]
        assert "tool_call" in after_types and "approval_request" in after_types
        assert after_types.count("tool_result") == 2
        results = [
            frame["data"]["payload"]
            for frame in complete_turn
            if frame["data"]["type"] == "tool_result"
        ]
        assert [result["status"] for result in results] == ["success", "error"]
        assert after_approval[-1]["data"]["payload"]["assistant_text"] == ("first turn completed")
        first_turn_cursor = after_approval[-1]["id"]
        assert client.get(f"/api/v1/sessions/{transport_id}").json()["state"] == "COMPLETED_TURN"
        assert [call.operation for call in environment.calls] == ["run_command", "run_command"]

        _send(
            client,
            transport_id,
            {"type": "SubmitTask", "text": "continue after the completed turn"},
        )
        follow_up = _read_until(
            client,
            transport_id,
            first_turn_cursor,
            lambda data: data["type"] == "turn_end",
        )
        assert follow_up[-1]["data"]["payload"]["assistant_text"] == "follow-up completed"
        assert any(frame["data"]["type"] == "user_message" for frame in follow_up)
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204


def test_scripted_web_rejected_approval_is_fail_closed(tmp_path: Path) -> None:
    environment = SequencedEnvironment([])
    model = ScriptedModel(
        [bash_call("must not run"), NormalizedAssistantResponse(text="rejected safely")]
    )
    with _LiveServer(_app(tmp_path, model, environment, approval_mode="ask")) as server:
        client = server.client
        transport_id = _session_id(client)
        _send(client, transport_id, {"type": "SubmitTask", "text": "reject the tool"})
        approval_frames = _read_until(
            client,
            transport_id,
            0,
            lambda data: data["type"] == "approval_request",
        )
        approval = approval_frames[-1]["data"]
        request_id = approval["payload"]["request_id"]
        _send(
            client,
            transport_id,
            {"type": "ApprovalResponse", "request_id": request_id, "approved": False},
        )
        completed = _read_until(
            client,
            transport_id,
            approval_frames[-1]["id"],
            lambda data: data["type"] == "turn_end",
        )
        decisions = [
            frame["data"]["payload"]
            for frame in completed
            if frame["data"]["type"] == "policy_decision"
        ]
        results = [
            frame["data"]["payload"]
            for frame in completed
            if frame["data"]["type"] == "tool_result"
        ]
        assert decisions[-1]["decision"] == "deny"
        assert results[-1]["status"] == "denied"
        assert environment.calls == []
        assert completed[-1]["data"]["payload"]["state"] == "COMPLETED_TURN"


class BlockingModel:
    """Hold one model request until the HTTP Interrupt command is delivered."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools, parameters=None) -> NormalizedAssistantResponse:
        del messages, tools, parameters
        self.started.set()
        self.release.wait(timeout=2.0)
        return NormalizedAssistantResponse(text="must be interrupted")


def test_scripted_web_interrupt_emits_terminal_chain(tmp_path: Path) -> None:
    model = BlockingModel()
    environment = FakeExecutionEnvironment()
    app = _app(tmp_path, model, environment, approval_mode="auto")
    with _LiveServer(app) as server:
        client = server.client
        transport_id = _session_id(client)
        _send(client, transport_id, {"type": "SubmitTask", "text": "stop this turn"})
        assert model.started.wait(timeout=2.0)
        _send(client, transport_id, {"type": "Interrupt", "reason": "user_stop"})
        model.release.set()
        terminal = _read_until(
            client,
            transport_id,
            0,
            lambda data: data["type"] == "session_end",
        )
        terminal_types = [frame["data"]["type"] for frame in terminal]
        assert terminal_types[-3:] == ["turn_end", "agent_end", "session_end"]
        assert terminal[-3]["data"]["payload"]["state"] == "INTERRUPTED"
        assert terminal[-1]["data"]["payload"]["state"] == "INTERRUPTED"
    model.release.set()


class CanonicalPayloadBackend:
    """Port-only fake for preserving unknown and truncated canonical payloads."""

    last_state = RuntimeState.RUNNING

    def __init__(self, events: tuple[EventEnvelope, ...]) -> None:
        self.events_to_send = events
        self.closed = False

    def send(self, command) -> None:
        del command

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        yield from (event for event in self.events_to_send if event.sequence > since)

    def close(self) -> None:
        self.closed = True


def test_web_wire_preserves_unknown_and_truncated_payloads() -> None:
    events = (
        EventEnvelope(
            schema_version=1,
            session_id="session_compat",
            event_id="event_compat_1",
            agent_id="agent_compat",
            sequence=1,
            type="unknown_future_event",
            timestamp="2026-09-01T00:00:00Z",
            payload={"new_field": {"preserve": True}},
        ),
        EventEnvelope(
            schema_version=1,
            session_id="session_compat",
            event_id="event_compat_2",
            agent_id="agent_compat",
            sequence=2,
            type="assistant_message",
            timestamp="2026-09-01T00:00:01Z",
            payload={
                "truncated": True,
                "original_length": 1234,
                "limit": 100,
                "head": "safe",
                "tail": "preview",
            },
        ),
    )
    backend = CanonicalPayloadBackend(events)
    app = create_app(_BackendProvider(backend), keepalive_seconds=0.02)
    with TestClient(app) as client:
        transport_id = _session_id(client)
        with client.stream(
            "GET",
            f"/api/v1/sessions/{transport_id}/events?since=0",
        ) as response:
            assert response.status_code == 200
            frames = list(_sse_frames(response))
        assert [frame["id"] for frame in frames] == [1, 2]
        assert frames[0]["data"]["type"] == "unknown_future_event"
        assert frames[0]["data"]["payload"] == {"new_field": {"preserve": True}}
        assert frames[1]["data"]["payload"]["truncated"] is True
        assert frames[1]["data"]["payload"]["original_length"] == 1234


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_t10_static_dependency_secret_and_resource_boundaries() -> None:
    backend_source = (PACKAGE_ROOT / "backend.py").read_text(encoding="utf-8")
    assert "import threading" not in backend_source
    assert "import queue" not in backend_source
    assert "AgentLoop" not in backend_source
    assert "SessionStore" not in backend_source
    assert not any(
        module.startswith("coding_agent_neo.transports")
        for module in _imported_modules(PACKAGE_ROOT / "backend_service.py")
    )

    http_root = PACKAGE_ROOT / "transports" / "http"
    for path in http_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "transports.in_process" not in source
        assert "web/dist" not in source
        assert "StaticFiles" not in source

    web_root = REPO_ROOT / "web" / "src"
    web_files = tuple(web_root.rglob("*.ts")) + tuple(web_root.rglob("*.vue"))
    web_source = "\n".join(path.read_text(encoding="utf-8") for path in web_files)
    assert "v-html" not in web_source
    assert "OPENAI_API_KEY" not in web_source
    assert "api_key" not in web_source.casefold()
    assert re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", web_source) is None

    for path in (PACKAGE_ROOT / "http_cli.py", PACKAGE_ROOT / "web_launcher.py"):
        assert 'DEFAULT_HOST = "127.0.0.1"' in (
            (PACKAGE_ROOT / "http_cli.py").read_text(encoding="utf-8")
        )
        assert "host=DEFAULT_HOST" in path.read_text(encoding="utf-8")
