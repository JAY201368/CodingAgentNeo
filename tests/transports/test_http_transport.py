"""HTTP/SSE contract tests using an injected fake AgentBackend port."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

from coding_agent_neo.assembly import build_agent_backend_provider
from coding_agent_neo.backend import (
    AgentCommand,
    ApprovalResponse,
    BackendClosedError,
    BoundedText,
    CloseSession,
    HistoryDiagnostic,
    Interrupt,
    InvalidSessionHistoryCursorError,
    InvalidSessionHistoryIdError,
    InvalidSessionHistoryLimitError,
    SessionEventPage,
    SessionHistoryItem,
    SessionHistoryNotFoundError,
    SessionHistoryPage,
    SessionHistoryUnavailableError,
    SubmitTask,
    TurnInProgressError,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventEnvelope, NormalizedAssistantResponse, RuntimeState
from coding_agent_neo.transports.http import create_app
from coding_agent_neo.transports.http.app import _event_stream

_WIRE_FIXTURE = json.loads(
    (
        Path(__file__).parents[2] / "web" / "src" / "domain" / "fixtures" / "transport-v1.json"
    ).read_text(encoding="utf-8")
)


def test_shared_wire_fixture_includes_history_and_resume_samples() -> None:
    """Browser and Python HTTP tests must share one history/resume wire sample."""

    history = _WIRE_FIXTURE["history"]
    listing = history["list"]
    assert set(listing) == {"sessions", "next_cursor"}
    item = listing["sessions"][0]
    assert set(item) >= {
        "session_id",
        "first_user_message",
        "created_at",
        "updated_at",
        "last_sequence",
        "last_state",
        "resumable",
        "diagnostics",
    }
    bounded = item["first_user_message"]
    assert set(bounded) >= {"text", "truncated", "original_length", "limit", "encoding"}
    assert item["session_id"].startswith("session_")
    assert listing["next_cursor"] is None or isinstance(listing["next_cursor"], str)

    events_page = history["events"]
    assert set(events_page) >= {
        "session_id",
        "events",
        "next_cursor",
        "has_more",
        "diagnostics",
    }
    assert events_page["session_id"] == item["session_id"]
    assert events_page["events"][0]["schema_version"] == 1
    assert events_page["events"][0]["sequence"] == 1
    empty_page = history["events_empty"]
    assert empty_page["events"] == []
    assert empty_page["has_more"] is False
    assert empty_page["next_cursor"] is None
    preview = history["truncated_payload"]
    assert preview["truncated"] is True
    assert set(preview) >= {"truncated", "original_length", "limit", "encoding", "head", "tail"}

    resume = history["resume"]
    assert resume["request"] == {"resume_session_id": item["session_id"]}
    assert set(resume["response"]) >= {"transport_session_id", "state", "cursor"}
    assert resume["response"]["transport_session_id"].startswith("transport_")
    assert resume["response"]["transport_session_id"] != item["session_id"]

    codes = {error["code"]: error["status"] for error in _WIRE_FIXTURE["history_errors"]}
    assert codes["invalid_history_id"] == 400
    assert codes["invalid_history_cursor"] == 400
    assert codes["invalid_history_limit"] == 400
    assert codes["history_not_found"] == 404
    assert codes["history_unavailable"] == 422
    assert codes["invalid_resume"] == 422
    assert codes["session_exists"] == 409


def _event(sequence: int, event_type: str = "assistant_message") -> EventEnvelope:
    """Build fake canonical events from the browser contract fixture."""

    template = next(item for item in _WIRE_FIXTURE["events"] if item["type"] == event_type)
    payload = dict(template["payload"])
    payload["text"] = f"message-{sequence}"
    return EventEnvelope(
        schema_version=template["schema_version"],
        session_id="session_fake",
        event_id=f"event_fake_{sequence}",
        agent_id="agent_fake",
        parent_agent_id=template["parent_agent_id"],
        sequence=sequence,
        type=template["type"],
        correlation_id=template["correlation_id"],
        provider_tool_call_id=template["provider_tool_call_id"],
        timestamp=template["timestamp"],
        payload=payload,
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


class FakeHistoryProvider:
    """Provider-port fake for HTTP DTO/error mapping tests."""

    def __init__(self, events: tuple[EventEnvelope, ...] = ()) -> None:
        self.backend = FakeBackend(events)
        self.backend.resume_last_sequence = 7
        self.create_calls: list[str | None] = []
        self.history = SessionHistoryPage(
            sessions=(
                SessionHistoryItem(
                    session_id="session_history",
                    first_user_message=BoundedText(
                        text="remember",
                        truncated=False,
                        original_length=8,
                        limit=4096,
                    ),
                    created_at="2026-09-01T00:00:00Z",
                    updated_at="2026-09-01T00:00:01Z",
                    last_sequence=7,
                    last_state="COMPLETED_TURN",
                    resumable=True,
                    diagnostics=(
                        HistoryDiagnostic(
                            "incomplete_tail", "history has an incomplete final record"
                        ),
                    ),
                ),
            ),
        )
        self.events_page = SessionEventPage(
            session_id="session_history",
            events=(_event(1),),
            diagnostics=(
                HistoryDiagnostic("incomplete_tail", "history has an incomplete final record"),
            ),
        )
        self.raise_error: BaseException | None = None

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50):
        del cursor, limit
        if self.raise_error is not None:
            raise self.raise_error
        return self.history

    def read_session_events(self, session_id: str, *, since: int = 0, limit: int = 200):
        del session_id, since, limit
        if self.raise_error is not None:
            raise self.raise_error
        return self.events_page

    def create_session(self, *, resume_session_id: str | None = None):
        self.create_calls.append(resume_session_id)
        if self.raise_error is not None:
            raise self.raise_error
        return self.backend


@pytest.fixture
def backend_and_client():
    provider = FakeHistoryProvider((_event(1), _event(2, "unknown_future_event")))
    backend = provider.backend
    app = create_app(provider, keepalive_seconds=0.02)
    with TestClient(app) as client:
        yield backend, client


def _session_id(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={})
    assert response.status_code == 201
    return response.json()["transport_session_id"]


def test_http_composition_rejects_callable_backend_dependency() -> None:
    with pytest.raises(TypeError, match="provider must implement AgentBackendProvider"):
        create_app(lambda **_kwargs: FakeBackend())


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


def test_finite_history_routes_map_provider_dtos_and_resume_cursor() -> None:
    provider = FakeHistoryProvider()
    app = create_app(provider)
    with TestClient(app) as client:
        listing = client.get("/api/v1/session-history?limit=1")
        assert listing.status_code == 200
        assert listing.json() == {
            "sessions": [provider.history.sessions[0].to_dict()],
            "next_cursor": None,
        }

        events = client.get("/api/v1/session-history/session_history/events?since=0&limit=1")
        assert events.status_code == 200
        assert events.json() == provider.events_page.to_dict()

        created = client.post("/api/v1/sessions", content=b"")
        assert created.status_code == 201
        assert created.json()["cursor"] == 0
        transport_id = created.json()["transport_session_id"]
        conflict = client.post(
            "/api/v1/sessions",
            json={"resume_session_id": "session_history"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "session_exists"
        assert provider.create_calls == [None]
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204

        resumed = client.post(
            "/api/v1/sessions",
            json={"resume_session_id": "session_history"},
        )
        assert resumed.status_code == 201
        assert resumed.json()["cursor"] == 7
        assert provider.create_calls == [None, "session_history"]
        assert (
            client.delete(f"/api/v1/sessions/{resumed.json()['transport_session_id']}").status_code
            == 204
        )


@pytest.mark.parametrize(
    ("error", "status", "code"),
    (
        (InvalidSessionHistoryIdError, 400, "invalid_history_id"),
        (InvalidSessionHistoryCursorError, 400, "invalid_history_cursor"),
        (InvalidSessionHistoryLimitError, 400, "invalid_history_limit"),
        (SessionHistoryNotFoundError, 404, "history_not_found"),
        (SessionHistoryUnavailableError, 422, "history_unavailable"),
    ),
)
def test_history_provider_errors_have_stable_codes(error, status: int, code: str) -> None:
    provider = FakeHistoryProvider()
    provider.raise_error = error()
    with TestClient(create_app(provider)) as client:
        response = client.get("/api/v1/session-history")
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert "traceback" not in response.text.casefold()
        assert "history" in response.json()["error"]["message"]


def test_session_body_rejects_extra_and_path_fields_without_provider_call() -> None:
    provider = FakeHistoryProvider()
    with TestClient(create_app(provider)) as client:
        for body in (
            {"resume_session_id": "session_history", "path": "/private/secret"},
            {"path": "/private/secret"},
            {"resume_session_id": "../outside"},
            ["session_history"],
        ):
            response = client.post("/api/v1/sessions", json=body)
            assert response.status_code == 400
            assert response.json()["error"]["code"] in {
                "invalid_session_request",
                "invalid_history_id",
            }
            assert "/private/secret" not in response.text
        assert provider.create_calls == []


@pytest.mark.parametrize("error", (SessionHistoryNotFoundError, SessionHistoryUnavailableError))
def test_resume_provider_failures_use_invalid_resume(error) -> None:
    provider = FakeHistoryProvider()
    provider.raise_error = error()
    with TestClient(create_app(provider)) as client:
        response = client.post(
            "/api/v1/sessions",
            json={"resume_session_id": "session_history"},
        )
        assert response.status_code == 422
        assert response.json() == {
            "error": {"code": "invalid_resume", "message": "session cannot be resumed"}
        }


def test_history_unknown_internal_errors_are_safe() -> None:
    provider = FakeHistoryProvider()
    provider.raise_error = RuntimeError("private provider payload and traceback")
    with TestClient(create_app(provider)) as client:
        response = client.get("/api/v1/session-history")
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "the Agent service could not complete the request",
            }
        }
        assert "private" not in response.text
        assert "traceback" not in response.text.casefold()


def test_finite_history_routes_reject_request_bodies() -> None:
    provider = FakeHistoryProvider()
    with TestClient(create_app(provider)) as client:
        listing = client.request(
            "GET",
            "/api/v1/session-history",
            content=b'{"path":"/private/secret"}',
        )
        events = client.request(
            "GET",
            "/api/v1/session-history/session_history/events",
            content=b'{"filename":"/private/secret.jsonl"}',
        )
        for response in (listing, events):
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "invalid_session_request"
            assert "/private/secret" not in response.text
        assert provider.create_calls == []


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
    provider = FakeHistoryProvider()
    backend = provider.backend
    app = create_app(provider, keepalive_seconds=0.02)
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
        api_key="placeholder",
        context_window=8000,
        reserved_output_tokens=1000,
    )
    model = ScriptedModel([NormalizedAssistantResponse(text="service response")])
    provider = build_agent_backend_provider(
        config,
        interactive=True,
        model_client=model,
        environment=FakeExecutionEnvironment(),
        worker_shutdown_timeout_seconds=2.0,
        event_poll_timeout_seconds=0.05,
        fsync=False,
    )

    app = create_app(provider, close_timeout_seconds=2.0)
    with TestClient(app) as client:
        transport_id = _session_id(client)
        response = client.post(
            f"/api/v1/sessions/{transport_id}/commands",
            json={"type": "SubmitTask", "text": "service task"},
        )
        assert response.status_code == 202
        events = wait_session(tmp_path, lambda event: event.type == "turn_end")
        assert events[-1].payload["assistant_text"] == "service response"
        assert list((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204
