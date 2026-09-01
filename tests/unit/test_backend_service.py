"""Unit coverage for the shared ``AgentBackendService`` implementation."""

from __future__ import annotations

from pathlib import Path

from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

from coding_agent_neo.assembly import build_agent_backend
from coding_agent_neo.backend import AgentBackend, SubmitTask
from coding_agent_neo.backend_service import (
    AgentBackendService,
    ApprovalChannel,
    ChannelApprovalPort,
    EventStreamBuffer,
)
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventType, NormalizedAssistantResponse, RuntimeState


def config(tmp_path: Path, **changes) -> AppConfig:
    values = {
        "workspace": tmp_path,
        "api_key": "placeholder",
        "context_window": 8000,
        "reserved_output_tokens": 1000,
    }
    values.update(changes)
    return AppConfig(**values)


def test_service_owns_runtime_and_implements_port(tmp_path: Path) -> None:
    service = build_agent_backend(
        config(tmp_path),
        interactive=False,
        model_client=ScriptedModel([NormalizedAssistantResponse(text="done")]),
        environment=FakeExecutionEnvironment(),
        worker_shutdown_timeout_seconds=2.0,
        event_poll_timeout_seconds=0.05,
        fsync=False,
    )
    try:
        assert isinstance(service, AgentBackendService)
        assert isinstance(service, AgentBackend)
        assert isinstance(service._stream, EventStreamBuffer)
        assert isinstance(service._approval, ApprovalChannel)
        service.send(SubmitTask("run"))
        events = wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
        assert events[-1].payload["state"] == RuntimeState.COMPLETED_TURN
    finally:
        service.close()


def test_service_approval_port_is_shared_runtime_component() -> None:
    assert ChannelApprovalPort.__module__ == "coding_agent_neo.backend_service"
    assert ApprovalChannel.__module__ == "coding_agent_neo.backend_service"
    assert EventStreamBuffer.__module__ == "coding_agent_neo.backend_service"
