"""Thin Python binding for an in-process ``AgentBackend`` port."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from coding_agent_neo.backend import (
    AgentBackend,
    AgentBackendProvider,
    AgentCommand,
    SessionEventPage,
    SessionHistoryPage,
)
from coding_agent_neo.models import EventEnvelope, RuntimeState


class InProcessWorkspaceBinding:
    """Expose one workspace provider through the controlled Python binding.

    The workspace binding exists before a session is selected.  It delegates
    history and session creation to the composition-owned provider and wraps
    only the returned per-session port.  In particular, it never receives a
    repository, session path, factory, or any other persistence/runtime
    implementation object.
    """

    def __init__(self, provider: AgentBackendProvider) -> None:
        self._provider = provider

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50) -> SessionHistoryPage:
        """Return one bounded provider-owned history page."""

        return self._provider.list_sessions(cursor=cursor, limit=limit)

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage:
        """Return one bounded provider-owned event page."""

        return self._provider.read_session_events(session_id, since=since, limit=limit)

    def create_session(self, *, resume_session_id: str | None = None) -> InProcessAdapter:
        """Create one new or resumed per-session binding through the provider."""

        backend = self._provider.create_session(resume_session_id=resume_session_id)
        return InProcessAdapter(backend)


class InProcessAdapter:
    """Delegate the Python binding to an injected ``AgentBackend`` port.

    The adapter owns no worker, event buffer, approval channel, or Agent Core
    object.  The optional resume metadata is copied from the composition-owned
    backend so CLI callers can start their cursor without widening the port.
    """

    def __init__(
        self,
        backend: AgentBackend,
        *,
        resume_last_sequence: int | None = None,
        resume_diagnostics: tuple[Any, ...] | None = None,
    ) -> None:
        self._backend = backend
        selected_sequence = (
            getattr(backend, "resume_last_sequence", 0)
            if resume_last_sequence is None
            else resume_last_sequence
        )
        if selected_sequence is None:
            selected_sequence = 0
        if (
            isinstance(selected_sequence, bool)
            or not isinstance(selected_sequence, int)
            or selected_sequence < 0
        ):
            raise ValueError("resume_last_sequence must be a non-negative integer")
        self.resume_last_sequence = selected_sequence
        selected_diagnostics = (
            getattr(backend, "resume_diagnostics", ())
            if resume_diagnostics is None
            else resume_diagnostics
        )
        if selected_diagnostics is None:
            selected_diagnostics = ()
        self.resume_diagnostics = tuple(selected_diagnostics)

    @property
    def last_state(self) -> RuntimeState:
        return self._backend.last_state

    @property
    def approval_mode(self) -> str:
        return self._backend.approval_mode

    def send(self, command: AgentCommand) -> None:
        self._backend.send(command)

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        return self._backend.events(since=since)

    def close(self) -> None:
        self._backend.close()


__all__ = ["InProcessAdapter", "InProcessWorkspaceBinding"]
