"""Shared, implementation-neutral seam for composing an ``AgentBackend``.

The protocol lives beside the backend port so a transport can accept the
composition-owned factory without importing the concrete service graph.
``assembly.py`` re-exports the same protocol for existing callers.
"""

from __future__ import annotations

from typing import Any, Protocol

from coding_agent_neo.backend import (
    DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
    DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
    AgentBackend,
)


class AgentBackendFactory(Protocol):
    """Build one concrete backend and return only its public port."""

    def __call__(
        self,
        config: Any,
        *,
        interactive: bool,
        resume: str | None = None,
        model_client: Any | None = None,
        environment: Any | None = None,
        approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        worker_shutdown_timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        event_poll_timeout_seconds: float = DEFAULT_EVENT_POLL_TIMEOUT_SECONDS,
        fsync: bool = True,
    ) -> AgentBackend: ...


__all__ = ["AgentBackendFactory"]
