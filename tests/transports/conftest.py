"""Reusable backend/adaptor bindings for transport conformance scenarios."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

import coding_agent_neo.assembly as assembly
from coding_agent_neo.assembly import (
    build_agent_backend,
    build_agent_backend_provider,
    build_in_process_adapter,
    build_in_process_workspace_binding,
)
from coding_agent_neo.backend import (
    AgentBackend,
    BackendClosedError,
    BoundedText,
    CloseSession,
    HistoryDiagnostic,
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
from coding_agent_neo.models import (
    EventEnvelope,
    EventType,
    NormalizedAssistantResponse,
    RuntimeState,
)
from coding_agent_neo.session import read_session


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        api_key="placeholder",
        context_window=8000,
        reserved_output_tokens=1000,
    )


@dataclass(frozen=True)
class WorkspaceHistoryScenario:
    """Reusable provider-backed history scenario for adapter conformance."""

    binding: object
    session_id: str
    session_path: Path
    original_last_sequence: int
    model: ScriptedModel


def _decode_history_item(value: dict[str, object]) -> SessionHistoryItem:
    first_message = value["first_user_message"]
    bounded = None
    if isinstance(first_message, dict):
        bounded = BoundedText(
            text=first_message["text"],
            truncated=first_message["truncated"],
            original_length=first_message["original_length"],
            limit=first_message["limit"],
            encoding=first_message["encoding"],
        )
    diagnostics = tuple(
        HistoryDiagnostic(item["code"], item["message"]) for item in value["diagnostics"]
    )
    return SessionHistoryItem(
        session_id=value["session_id"],
        first_user_message=bounded,
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        last_sequence=value["last_sequence"],
        last_state=value["last_state"],
        resumable=value["resumable"],
        diagnostics=diagnostics,
    )


def _decode_event(value: dict[str, object]) -> EventEnvelope:
    return EventEnvelope(
        schema_version=value["schema_version"],
        session_id=value["session_id"],
        event_id=value["event_id"],
        agent_id=value["agent_id"],
        sequence=value["sequence"],
        type=value["type"],
        timestamp=value["timestamp"],
        parent_agent_id=value.get("parent_agent_id"),
        correlation_id=value.get("correlation_id"),
        provider_tool_call_id=value.get("provider_tool_call_id"),
        payload=value.get("payload", {}),
    )


class _LiveHttpHistorySession:
    """Provider-like per-session facade over the finite HTTP test client."""

    def __init__(self, binding: _LiveHttpHistoryBinding, value: dict[str, object]) -> None:
        self._binding = binding
        self._transport_id = value["transport_session_id"]
        self.resume_last_sequence = value["cursor"]
        self.resume_diagnostics: tuple[object, ...] = ()

    @property
    def last_state(self) -> RuntimeState:
        response = self._binding._client.get(f"/api/v1/sessions/{self._transport_id}")
        response.raise_for_status()
        return RuntimeState(response.json()["state"])

    def send(self, command) -> None:
        response = self._binding._client.post(
            f"/api/v1/sessions/{self._transport_id}/commands",
            json=command.to_dict(),
        )
        if response.status_code == 409:
            raise TurnInProgressError("turn in progress")
        if response.status_code == 410:
            raise BackendClosedError("session closed")
        response.raise_for_status()

    def events(self, *, since: int = 0):
        with self._binding._client.stream(
            "GET",
            f"/api/v1/sessions/{self._transport_id}/events?since={since}",
        ) as response:
            response.raise_for_status()
            data_line: str | None = None
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_line = line[6:]
                elif not line and data_line is not None:
                    yield _decode_event(json.loads(data_line))
                    data_line = None

    def close(self) -> None:
        self._binding._client.delete(f"/api/v1/sessions/{self._transport_id}")


class _LiveHttpHistoryBinding:
    """HTTP binding exposing the same finite history/create surface."""

    def __init__(self, config: AppConfig, model: ScriptedModel) -> None:
        pytest.importorskip("fastapi")
        pytest.importorskip("uvicorn")
        import httpx
        import uvicorn

        from coding_agent_neo.transports.http import create_app

        provider = build_agent_backend_provider(
            config,
            interactive=False,
            model_client=model,
            environment=FakeExecutionEnvironment(),
            worker_shutdown_timeout_seconds=2.0,
            event_poll_timeout_seconds=0.05,
            fsync=False,
        )
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(provider, keepalive_seconds=0.02, close_timeout_seconds=2.0),
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
            raise RuntimeError("HTTP history conformance server did not start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{port}")

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50) -> SessionHistoryPage:
        params = {"limit": str(limit)}
        if cursor is not None:
            params["cursor"] = cursor
        response = self._client.get("/api/v1/session-history", params=params)
        self._raise_history_error(response)
        response.raise_for_status()
        value = response.json()
        return SessionHistoryPage(
            sessions=tuple(_decode_history_item(item) for item in value["sessions"]),
            next_cursor=value["next_cursor"],
        )

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage:
        response = self._client.get(
            f"/api/v1/session-history/{quote(session_id, safe='')}/events",
            params={"since": str(since), "limit": str(limit)},
        )
        self._raise_history_error(response)
        response.raise_for_status()
        value = response.json()
        return SessionEventPage(
            session_id=value["session_id"],
            events=tuple(_decode_event(item) for item in value["events"]),
            next_cursor=value["next_cursor"],
            has_more=value["has_more"],
            diagnostics=tuple(
                HistoryDiagnostic(item["code"], item["message"]) for item in value["diagnostics"]
            ),
        )

    def create_session(self, *, resume_session_id: str | None = None) -> _LiveHttpHistorySession:
        body = {} if resume_session_id is None else {"resume_session_id": resume_session_id}
        response = self._client.post("/api/v1/sessions", json=body)
        response.raise_for_status()
        return _LiveHttpHistorySession(self, response.json())

    @staticmethod
    def _raise_history_error(response) -> None:
        if response.is_success:
            return
        code = response.json().get("error", {}).get("code")
        errors = {
            "invalid_history_id": InvalidSessionHistoryIdError,
            "invalid_history_cursor": InvalidSessionHistoryCursorError,
            "invalid_history_limit": InvalidSessionHistoryLimitError,
            "history_not_found": SessionHistoryNotFoundError,
            "history_unavailable": SessionHistoryUnavailableError,
        }
        error = errors.get(code)
        if error is not None:
            raise error

    def close(self) -> None:
        self._client.close()
        self._server.should_exit = True
        self._thread.join(timeout=3.0)


@pytest.fixture(params=("in_process", "http"))
def history_binding(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> WorkspaceHistoryScenario:
    """Build a fixed-directory history fixture through the canonical binding.

    Both bindings present the same finite history/create surface; the
    conformance scenarios remain provider- and transport-agnostic.
    """

    if request.param not in {"in_process", "http"}:  # pragma: no cover - extension guard
        raise AssertionError(f"unsupported history binding: {request.param}")

    config = _config(tmp_path)
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(text="history seed"),
            NormalizedAssistantResponse(text="history follow-up"),
        ]
    )
    if request.param == "http":
        binding = _LiveHttpHistoryBinding(config, model)
        request.addfinalizer(binding.close)
    else:
        real_provider_builder = assembly.build_agent_backend_provider

        def provider_builder(config_value, *, interactive):
            return real_provider_builder(
                config_value,
                interactive=interactive,
                model_client=model,
                environment=FakeExecutionEnvironment(),
                worker_shutdown_timeout_seconds=2.0,
                event_poll_timeout_seconds=0.05,
                fsync=False,
            )

        monkeypatch.setattr(assembly, "build_agent_backend_provider", provider_builder)
        binding = build_in_process_workspace_binding(config, interactive=False)
    seeded = binding.create_session()
    try:
        seeded.send(SubmitTask("remember history"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
    finally:
        try:
            seeded.send(CloseSession("history_seed_done"))
        except BackendClosedError:
            pass
        seeded.close()

    session_path = next((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
    original_last_sequence = read_session(session_path).last_valid_sequence
    assert original_last_sequence is not None
    session_id = session_path.stem

    # The public canonical entry intentionally exposes no model/environment
    # injection seams; the patched composition seam above keeps this scenario
    # on the real provider while using deterministic test dependencies.
    return WorkspaceHistoryScenario(
        binding=binding,
        session_id=session_id,
        session_path=session_path,
        original_last_sequence=original_last_sequence,
        model=model,
    )


class _LiveHttpBinding:
    """Small wire client used to run the existing conformance scenario over HTTP."""

    def __init__(self, config: AppConfig, model: ScriptedModel) -> None:
        pytest.importorskip("fastapi")
        pytest.importorskip("uvicorn")
        import httpx
        import uvicorn

        from coding_agent_neo.transports.http import create_app

        provider = build_agent_backend_provider(
            config,
            interactive=True,
            model_client=model,
            environment=FakeExecutionEnvironment(),
            worker_shutdown_timeout_seconds=2.0,
            event_poll_timeout_seconds=0.05,
            fsync=False,
        )

        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(provider, keepalive_seconds=0.02),
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
            raise RuntimeError("HTTP conformance server did not start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{port}")
        response = self._client.post("/api/v1/sessions", json={})
        response.raise_for_status()
        self._transport_id = response.json()["transport_session_id"]

    @property
    def last_state(self) -> RuntimeState:
        response = self._client.get(f"/api/v1/sessions/{self._transport_id}")
        response.raise_for_status()
        return RuntimeState(response.json()["state"])

    def send(self, command) -> None:
        response = self._client.post(
            f"/api/v1/sessions/{self._transport_id}/commands",
            json=command.to_dict(),
        )
        if response.status_code == 409:
            raise TurnInProgressError("turn in progress")
        if response.status_code == 410:
            raise BackendClosedError("session closed")
        response.raise_for_status()

    def events(self, *, since: int = 0):
        with self._client.stream(
            "GET",
            f"/api/v1/sessions/{self._transport_id}/events?since={since}",
        ) as response:
            response.raise_for_status()
            data_line: str | None = None
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_line = line[6:]
                elif not line and data_line is not None:
                    value = json.loads(data_line)
                    yield EventEnvelope(
                        schema_version=value["schema_version"],
                        session_id=value["session_id"],
                        event_id=value["event_id"],
                        agent_id=value["agent_id"],
                        sequence=value["sequence"],
                        type=value["type"],
                        timestamp=value["timestamp"],
                        parent_agent_id=value.get("parent_agent_id"),
                        correlation_id=value.get("correlation_id"),
                        provider_tool_call_id=value.get("provider_tool_call_id"),
                        payload=value.get("payload", {}),
                    )
                    data_line = None

    def close(self) -> None:
        try:
            self._client.delete(f"/api/v1/sessions/{self._transport_id}")
        finally:
            self._client.close()
            self._server.should_exit = True
            self._thread.join(timeout=3.0)


@pytest.fixture(params=("service", "in_process", "http"))
def backend_binding(request: pytest.FixtureRequest, tmp_path: Path) -> AgentBackend:
    """Yield the same scripted backend scenarios through both T01 surfaces."""

    model = ScriptedModel([NormalizedAssistantResponse(text="done")])
    kwargs = {
        "interactive": False,
        "model_client": model,
        "environment": FakeExecutionEnvironment(),
        "worker_shutdown_timeout_seconds": 2.0,
        "event_poll_timeout_seconds": 0.05,
        "fsync": False,
    }
    if request.param == "http":
        backend = _LiveHttpBinding(_config(tmp_path), model)
    else:
        builder: Callable[..., AgentBackend]
        if request.param == "service":
            builder = build_agent_backend
        else:
            builder = build_in_process_adapter
        backend = builder(_config(tmp_path), **kwargs)
    try:
        yield backend
    finally:
        backend.close()
