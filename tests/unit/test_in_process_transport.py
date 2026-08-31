"""Unit coverage for the thin in-process transport binding."""

from __future__ import annotations

from collections.abc import Iterator

from coding_agent_neo.backend import AgentBackend, AgentCommand
from coding_agent_neo.models import EventEnvelope, RuntimeState
from coding_agent_neo.transports.in_process import InProcessAdapter


class RecordingBackend:
    last_state = RuntimeState.RUNNING
    resume_last_sequence = 7
    resume_diagnostics = ("diagnostic",)

    def __init__(self) -> None:
        self.commands: list[AgentCommand] = []
        self.cursors: list[int] = []
        self.close_calls = 0
        self.event = object()

    def send(self, command: AgentCommand) -> None:
        self.commands.append(command)

    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]:
        self.cursors.append(since)
        yield self.event  # type: ignore[misc]

    def close(self) -> None:
        self.close_calls += 1


def test_adapter_only_delegates_port_and_copies_resume_metadata() -> None:
    backend = RecordingBackend()
    adapter = InProcessAdapter(backend)  # type: ignore[arg-type]
    assert isinstance(adapter, AgentBackend)
    assert adapter.last_state is RuntimeState.RUNNING
    assert adapter.resume_last_sequence == 7
    assert adapter.resume_diagnostics == ("diagnostic",)

    from coding_agent_neo.backend import SubmitTask

    command = SubmitTask("hello")
    adapter.send(command)
    assert backend.commands == [command]
    assert list(adapter.events(since=8)) == [backend.event]
    assert backend.cursors == [8]
    adapter.close()
    assert backend.close_calls == 1


def test_adapter_accepts_explicit_resume_metadata() -> None:
    backend = RecordingBackend()
    adapter = InProcessAdapter(
        backend,  # type: ignore[arg-type]
        resume_last_sequence=3,
        resume_diagnostics=("override",),
    )
    assert adapter.resume_last_sequence == 3
    assert adapter.resume_diagnostics == ("override",)
