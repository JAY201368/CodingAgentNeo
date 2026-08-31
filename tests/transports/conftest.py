"""Reusable backend/adaptor bindings for transport conformance scenarios."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel

from coding_agent_neo.assembly import build_agent_backend, build_in_process_adapter
from coding_agent_neo.backend import AgentBackend
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import NormalizedAssistantResponse


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        session_dir=tmp_path / "sessions",
        api_key="placeholder",
        context_window=8000,
        reserved_output_tokens=1000,
    )


@pytest.fixture(params=("service", "in_process"))
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
