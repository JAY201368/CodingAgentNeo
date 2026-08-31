"""Reusable backend/adaptor bindings for transport conformance scenarios."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel

from coding_agent_neo.assembly import build_agent_backend, build_in_process_adapter
from coding_agent_neo.backend import (
    AgentBackend,
    BackendClosedError,
    TurnInProgressError,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventEnvelope, NormalizedAssistantResponse, RuntimeState


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        session_dir=tmp_path / "sessions",
        api_key="placeholder",
        context_window=8000,
        reserved_output_tokens=1000,
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
