"""Unit coverage for the thin in-process transport binding."""

from __future__ import annotations

from collections.abc import Iterator

import coding_agent_neo.assembly as assembly
from coding_agent_neo.backend import (
    AgentBackend,
    AgentBackendProvider,
    AgentCommand,
    SessionEventPage,
    SessionHistoryPage,
)
from coding_agent_neo.models import EventEnvelope, RuntimeState
from coding_agent_neo.transports.in_process import (
    InProcessAdapter,
    InProcessWorkspaceBinding,
)


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


class RecordingProvider:
    def __init__(self, backend: RecordingBackend) -> None:
        self.backend = backend
        self.list_calls: list[tuple[str | None, int]] = []
        self.read_calls: list[tuple[str, int, int]] = []
        self.create_calls: list[str | None] = []
        self.history = SessionHistoryPage(sessions=())
        self.events_page = SessionEventPage(session_id="session_history", events=())

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50) -> SessionHistoryPage:
        self.list_calls.append((cursor, limit))
        return self.history

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage:
        self.read_calls.append((session_id, since, limit))
        return self.events_page

    def create_session(self, *, resume_session_id: str | None = None) -> RecordingBackend:
        self.create_calls.append(resume_session_id)
        return self.backend


def test_workspace_binding_delegates_history_and_wraps_created_backend() -> None:
    backend = RecordingBackend()
    provider = RecordingProvider(backend)
    assert isinstance(provider, AgentBackendProvider)
    binding = InProcessWorkspaceBinding(provider)

    assert binding.list_sessions(cursor="cursor", limit=2) is provider.history
    assert binding.read_session_events("session_history", since=3, limit=4) is provider.events_page
    adapter = binding.create_session(resume_session_id="session_history")

    assert isinstance(adapter, InProcessAdapter)
    assert adapter.resume_last_sequence == backend.resume_last_sequence
    assert adapter.resume_diagnostics == backend.resume_diagnostics
    assert provider.list_calls == [("cursor", 2)]
    assert provider.read_calls == [("session_history", 3, 4)]
    assert provider.create_calls == ["session_history"]


def test_canonical_and_compatibility_builders_share_one_provider_path(monkeypatch) -> None:
    backend = RecordingBackend()
    provider = RecordingProvider(backend)
    provider_calls: list[tuple[object, bool]] = []

    def provider_builder(config, *, interactive):
        provider_calls.append((config, interactive))
        return provider

    def forbidden_direct_factory(*_args, **_kwargs):
        raise AssertionError("compatibility builders must not use direct backend factory")

    monkeypatch.setattr(assembly, "build_agent_backend_provider", provider_builder)
    monkeypatch.setattr(assembly, "build_agent_backend", forbidden_direct_factory)
    config = object()

    workspace = assembly.build_in_process_workspace_binding(config, interactive=False)
    assert isinstance(workspace, InProcessWorkspaceBinding)
    assert provider.create_calls == []

    adapter = assembly.build_in_process_adapter(config, interactive=False)
    assert provider.create_calls == [None]
    backend_from_legacy = assembly.build_local_backend(config, interactive=False)
    try:
        assert isinstance(adapter, InProcessAdapter)
        assert backend_from_legacy is backend
        assert provider.create_calls == [None, None]
        assert provider_calls == [(config, False), (config, False), (config, False)]
    finally:
        adapter.close()
        backend_from_legacy.close()


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
