"""Thin Python binding for an in-process ``AgentBackend`` port."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from coding_agent_neo.backend import AgentBackend, AgentCommand
from coding_agent_neo.models import EventEnvelope, RuntimeState


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

    def send(self, command: AgentCommand) -> None:
        self._backend.send(command)

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        return self._backend.events(since=since)

    def close(self) -> None:
        self._backend.close()


__all__ = ["InProcessAdapter"]
