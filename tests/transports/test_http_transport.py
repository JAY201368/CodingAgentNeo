"""HTTP/SSE contract tests using an injected fake AgentBackend port."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

from coding_agent_neo.assembly import build_agent_backend
from coding_agent_neo.backend import (
    AgentCommand,
    ApprovalResponse,
    BackendClosedError,
    CloseSession,
    Interrupt,
    SubmitTask,
    TurnInProgressError,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventEnvelope, NormalizedAssistantResponse, RuntimeState
from coding_agent_neo.transports.http import create_app
from coding_agent_neo.transports.http.app import _event_stream


def _event(sequence: int, event_type: str = "assistant_message") -> EventEnvelope:
    return EventEnvelope.create(
        session_id="session_fake",
        agent_id="agent_fake",
        sequence=sequence,
        type=event_type,
        payload={"text": f"message-{sequence}", "new_field": {"preserve": True}},
    )


class FakeBackend:
    """A port-only fake; it intentionally has no Agent Core implementation."""

    def __init__(self, events: tuple[EventEnvelope, ...] = ()) -> None:
        self.events_to_send = events
        self.commands: list[AgentCommand] = []
        self.cursors: list[int] = []
        self.close_calls = 0
        self.closed = False
        self.last_state = RuntimeState.RUNNING
        self.raise_on_send: BaseException | None = None
        self.release_stream = threading.Event()
        self.block_stream = False

    def send(self, command: AgentCommand) -> None:
        if self.closed:
            raise BackendClosedError("closed")
        if self.raise_on_send is not None:
            error = self.raise_on_send
            self.raise_on_send = None
            raise error
        self.commands.append(command)
        if isinstance(command, SubmitTask):
            self.last_state = RuntimeState.COMPLETED_TURN

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        self.cursors.append(since)
        if self.block_stream:
            self.release_stream.wait()
        yield from (event for event in self.events_to_send if event.sequence > since)

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


@pytest.fixture
def backend_and_client():
    backend = FakeBackend((_event(1), _event(2, "unknown_future_event")))
    app = create_app(lambda *, interactive: backend, keepalive_seconds=0.02)
    with TestClient(app) as client:
        yield backend, client


def _session_id(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={})
    assert response.status_code == 201
    return response.json()["transport_session_id"]


def _frames(text: str) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for line in text.splitlines():
        if not line:
            if current:
                frames.append(current)
                current = {}
            continue
        if line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
    if current:
        frames.append(current)
    return frames


def test_health_session_state_and_single_active_registry(backend_and_client) -> None:
    backend, client = backend_and_client
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "protocol_version": 1}

    transport_id = _session_id(client)
    created = client.get(f"/api/v1/sessions/{transport_id}")
    assert created.status_code == 200
    assert created.json() == {"state": "RUNNING", "cursor": 0, "closed": False}

    conflict = client.post("/api/v1/sessions", json={})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "session_exists"
    assert backend.commands == []


def test_command_decoder_accepts_four_commands_and_is_non_blocking(backend_and_client) -> None:
    backend, client = backend_and_client
    transport_id = _session_id(client)
    path = f"/api/v1/sessions/{transport_id}/commands"
    commands = (
        {"type": "SubmitTask", "text": "inspect"},
        {"type": "ApprovalResponse", "request_id": "correlation_1", "approved": False},
        {"type": "Interrupt"},
    )
    for payload in commands:
        response = client.post(path, json=payload)
        assert response.status_code == 202
        assert response.json() == {"accepted": True}
    assert [type(command).__name__ for command in backend.commands] == [
        "SubmitTask",
        "ApprovalResponse",
        "Interrupt",
    ]
    assert backend.commands[0].to_dict() == {"type": "SubmitTask", "text": "inspect"}


def test_sse_since_last_event_id_keepalive_and_canonical_data(backend_and_client) -> None:
    backend, client = backend_and_client
    transport_id = _session_id(client)
    expected_event = backend.events_to_send[1]
    with client.stream(
        "GET",
        f"/api/v1/sessions/{transport_id}/events?since=0",
        headers={"Last-Event-ID": "1"},
    ) as response:
        assert response.status_code == 200
        frames = _frames("\n".join(response.iter_lines()))
    assert backend.cursors == [0]
    assert [frame["id"] for frame in frames] == [2]
    assert frames[0]["event"] == "agent-event"
    assert frames[0]["data"] == expected_event.to_dict()


def test_sse_lower_cursor_reconnect_replays_history_after_high_cursor_attach() -> None:
    backend = FakeBackend(tuple(_event(sequence) for sequence in range(6, 13)))

    class Session:
        closed = False

        def __init__(self, port) -> None:
            self.backend = port

        @staticmethod
        def record_event(_sequence: int) -> None:
            pass

    session = Session(backend)
    high_cursor = _event_stream(session, 10, keepalive_seconds=0.01)
    assert _frames(next(high_cursor))[0]["id"] == 11
    high_cursor.close()

    lower_cursor = _event_stream(session, 5, keepalive_seconds=0.01)
    replayed = _frames("".join(lower_cursor))
    assert [frame["id"] for frame in replayed] == list(range(6, 13))
    assert backend.cursors == [0]


def test_invalid_command_and_cursor_are_stable_safe_errors(backend_and_client) -> None:
    _backend, client = backend_and_client
    transport_id = _session_id(client)
    path = f"/api/v1/sessions/{transport_id}/commands"
    invalid = client.post(path, json={"type": "SubmitTask", "text": "   "})
    assert invalid.status_code == 400
    assert invalid.json() == {"error": {"code": "invalid_command", "message": "command is invalid"}}
    invalid_cursor = client.get(f"/api/v1/sessions/{transport_id}/events?since=-1")
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"
    malformed = client.post(path, content=b'{"type":"SubmitTask"}')
    assert malformed.status_code == 400
    assert "traceback" not in malformed.text.casefold()


def test_error_mapping_does_not_leak_backend_exception_or_task(backend_and_client) -> None:
    backend, client = backend_and_client
    transport_id = _session_id(client)
    path = f"/api/v1/sessions/{transport_id}/commands"

    backend.raise_on_send = TurnInProgressError("secret task text")
    conflict = client.post(path, json={"type": "SubmitTask", "text": "secret task text"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "turn_in_progress"
    assert "secret" not in conflict.text

    backend.raise_on_send = RuntimeError("provider body API_KEY=secret")
    failure = client.post(path, json={"type": "Interrupt"})
    assert failure.status_code == 500
    assert failure.json() == {
        "error": {
            "code": "internal_error",
            "message": "the Agent service could not complete the request",
        }
    }
    assert "provider" not in failure.text and "secret" not in failure.text


def test_host_origin_and_session_lifecycle(backend_and_client) -> None:
    backend, client = backend_and_client
    assert client.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 400
    assert (
        client.get("/api/v1/health", headers={"origin": "https://evil.example"}).status_code == 400
    )
    assert client.get("/api/v1/sessions/not-known").status_code == 404

    transport_id = _session_id(client)
    assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204
    assert backend.close_calls == 1
    assert client.get(f"/api/v1/sessions/{transport_id}").status_code == 410
    assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204
    assert backend.close_calls == 1


def test_sse_disconnect_only_stops_consumer() -> None:
    backend = FakeBackend()
    backend.events_to_send = ()
    backend.block_stream = True

    class Session:
        closed = False

        def __init__(self, port) -> None:
            self.backend = port

        @staticmethod
        def record_event(_sequence: int) -> None:
            pass

    session = Session(backend)
    stream = _event_stream(session, 0, keepalive_seconds=0.01)
    assert next(stream).startswith(": keepalive")
    stream.close()
    assert backend.close_calls == 0
    assert backend.commands == []
    assert getattr(session, "_http_event_pump")._subscribers == set()
    backend.release_stream.set()
    deadline = time.monotonic() + 1
    while (
        any(
            thread.is_alive()
            for thread in threading.enumerate()
            if thread.name == "coding-agent-neo-http-session-pump"
        )
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert not any(
        thread.is_alive()
        for thread in threading.enumerate()
        if thread.name == "coding-agent-neo-http-session-pump"
    )


def test_sse_reconnects_reuse_one_session_pump_and_leave_agent_untouched() -> None:
    backend = FakeBackend()
    backend.block_stream = True

    class Session:
        closed = False

        def __init__(self, port) -> None:
            self.backend = port

        @staticmethod
        def record_event(_sequence: int) -> None:
            pass

    session = Session(backend)
    for _ in range(5):
        stream = _event_stream(session, 0, keepalive_seconds=0.01)
        assert next(stream).startswith(": keepalive")
        stream.close()
        pump = getattr(session, "_http_event_pump")
        assert pump._subscribers == set()

    pump_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == "coding-agent-neo-http-session-pump"
    ]
    assert len(pump_threads) == 1
    assert backend.cursors == [0]
    assert backend.close_calls == 0
    assert not any(
        isinstance(command, (ApprovalResponse, Interrupt, CloseSession))
        for command in backend.commands
    )

    # A disconnected SSE consumer does not own the Agent backend.  Commands
    # remain independently usable while the event pump waits for the stream.
    backend.send(SubmitTask("after disconnect"))
    assert isinstance(backend.commands[-1], SubmitTask)

    backend.release_stream.set()
    deadline = time.monotonic() + 1
    while (
        any(
            thread.is_alive()
            for thread in threading.enumerate()
            if thread.name == "coding-agent-neo-http-session-pump"
        )
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert not any(
        thread.is_alive()
        for thread in threading.enumerate()
        if thread.name == "coding-agent-neo-http-session-pump"
    )


def test_close_command_is_delivered_once_and_closes_asynchronously() -> None:
    backend = FakeBackend()
    app = create_app(lambda *, interactive: backend, keepalive_seconds=0.02)
    with TestClient(app) as client:
        transport_id = _session_id(client)
        response = client.post(
            f"/api/v1/sessions/{transport_id}/commands",
            json={"type": "CloseSession", "reason": "frontend_exit"},
        )
        assert response.status_code == 202
        assert isinstance(backend.commands[0], CloseSession)
        deadline = time.monotonic() + 1
        while backend.close_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backend.close_calls == 1
        assert client.get(f"/api/v1/sessions/{transport_id}").status_code == 410


def test_http_path_injects_shared_backend_service_without_in_process_adapter(tmp_path) -> None:
    config = AppConfig(
        workspace=tmp_path,
        session_dir=tmp_path / "sessions",
        api_key="placeholder",
        context_window=8000,
        reserved_output_tokens=1000,
    )
    model = ScriptedModel([NormalizedAssistantResponse(text="service response")])
    factory_calls: list[bool] = []

    def factory(config_value, *, interactive):
        factory_calls.append(interactive)
        return build_agent_backend(
            config_value,
            interactive=interactive,
            model_client=model,
            environment=FakeExecutionEnvironment(),
            worker_shutdown_timeout_seconds=2.0,
            event_poll_timeout_seconds=0.05,
            fsync=False,
        )

    app = create_app(factory, config=config, close_timeout_seconds=2.0)
    with TestClient(app) as client:
        transport_id = _session_id(client)
        response = client.post(
            f"/api/v1/sessions/{transport_id}/commands",
            json={"type": "SubmitTask", "text": "service task"},
        )
        assert response.status_code == 202
        events = wait_session(tmp_path, lambda event: event.type == "turn_end")
        assert events[-1].payload["assistant_text"] == "service response"
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204
    assert factory_calls == [True]
