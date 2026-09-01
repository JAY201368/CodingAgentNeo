"""Reusable backend/adaptor bindings for transport conformance scenarios."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

import coding_agent_neo.assembly as assembly
from coding_agent_neo.assembly import (
    build_agent_backend,
    build_in_process_adapter,
    build_in_process_workspace_binding,
)
from coding_agent_neo.backend import (
    AgentBackend,
    BackendClosedError,
    CloseSession,
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


@pytest.fixture(params=("in_process",))
def history_binding(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> WorkspaceHistoryScenario:
    """Build a fixed-directory history fixture through the canonical binding.

    T05 can extend the parameter set with an HTTP binding that presents the
    same finite history/create surface; the conformance scenarios remain
    provider- and transport-agnostic.
    """

    if request.param != "in_process":  # pragma: no cover - extension guard for T05
        raise AssertionError(f"unsupported history binding: {request.param}")

    config = _config(tmp_path)
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(text="history seed"),
            NormalizedAssistantResponse(text="history follow-up"),
        ]
    )
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

        def factory(config_value, *, interactive):
            return build_agent_backend(
                config_value,
                interactive=interactive,
                model_client=model,
                environment=FakeExecutionEnvironment(),
                worker_shutdown_timeout_seconds=2.0,
                event_poll_timeout_seconds=0.05,
                fsync=False,
            )

        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(factory, config=config, keepalive_seconds=0.02),
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
