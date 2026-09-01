"""Provider-owned session creation and resume revalidation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import coding_agent_neo as package
import coding_agent_neo.session as session_module
from coding_agent_neo import assembly
from coding_agent_neo.assembly import build_agent_backend_provider
from coding_agent_neo.backend import (
    AgentBackendProvider,
    InvalidSessionHistoryIdError,
    SessionHistoryNotFoundError,
    SessionResumeUnavailableError,
)
from coding_agent_neo.backend_provider import LocalAgentBackendProvider
from coding_agent_neo.config import AppConfig


class FakeBackend:
    last_state = "RUNNING"

    def send(self, command):
        del command

    def events(self, *, since=0):
        del since
        return iter(())

    def close(self):
        return None


def test_public_surface_exposes_only_the_provider_contract_for_history() -> None:
    for name in (
        "FileSessionHistoryRepository",
        "SessionHistoryRepository",
        "LocalAgentBackendProvider",
        "WorkspaceAgentBackendProvider",
        "build_backend_provider",
        "build_workspace_backend_provider",
    ):
        assert not hasattr(package, name)
        assert not hasattr(session_module, name)


def event(
    sequence: int, event_type: str, payload: dict, session_id: str = "session_resume1"
) -> dict:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "event_id": f"event_resume{sequence}",
        "agent_id": "agent_resume1",
        "parent_agent_id": None,
        "sequence": sequence,
        "type": event_type,
        "correlation_id": None,
        "provider_tool_call_id": None,
        "timestamp": f"2026-09-01T00:00:{sequence:02d}Z",
        "payload": payload,
    }


def write_resumable(path: Path, session_id: str = "session_resume1") -> None:
    records = [
        event(1, "session_start", {"state": "RUNNING"}, session_id=session_id),
        event(
            2,
            "agent_start",
            {"state": "RUNNING", "active_tools": ["read_file"]},
            session_id=session_id,
        ),
        event(3, "user_message", {"text": "remember this"}, session_id=session_id),
        event(
            4,
            "assistant_message",
            {"text": "done", "tool_calls": []},
            session_id=session_id,
        ),
        event(
            5,
            "turn_end",
            {
                "state": "COMPLETED_TURN",
                "budget": {
                    "model_steps": 1,
                    "tool_calls": 0,
                    "protocol_errors": 0,
                    "input_tokens": 10,
                    "output_tokens": 2,
                },
            },
            session_id=session_id,
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def test_provider_is_runtime_checkable_and_controls_creation(tmp_path: Path) -> None:
    calls: list[str | None] = []

    def factory(resume_session_id: str | None):
        calls.append(resume_session_id)
        return FakeBackend()

    provider = LocalAgentBackendProvider(tmp_path, backend_factory=factory)
    assert isinstance(provider, AgentBackendProvider)
    created = provider.create_session()
    assert isinstance(created, FakeBackend)
    assert calls == [None]


def test_new_session_factory_failure_keeps_its_startup_classification(tmp_path: Path) -> None:
    failure = RuntimeError("backend startup failed")

    def factory(_resume: str | None):
        raise failure

    provider = LocalAgentBackendProvider(tmp_path, backend_factory=factory)

    with pytest.raises(RuntimeError) as raised:
        provider.create_session()
    assert raised.value is failure
    assert not isinstance(raised.value, SessionResumeUnavailableError)


def test_resume_revalidates_id_and_calls_factory_only_after_recovery(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_resume1.jsonl"
    write_resumable(path)
    calls: list[str | None] = []
    validations: list[tuple[Path, str]] = []

    def factory(resume_session_id: str | None):
        calls.append(resume_session_id)
        return FakeBackend()

    def validator(candidate: Path, session_id: str):
        validations.append((candidate, session_id))
        assert candidate == path
        assert session_id == "session_resume1"
        return object()

    provider = LocalAgentBackendProvider(
        tmp_path,
        backend_factory=factory,
        resume_validator=validator,
    )
    backend = provider.create_session(resume_session_id="session_resume1")
    assert isinstance(backend, FakeBackend)
    assert calls == ["session_resume1"]
    assert validations == [(path, "session_resume1")]


def test_resume_does_not_open_unknown_or_unsafe_targets(tmp_path: Path) -> None:
    calls: list[str | None] = []
    provider = LocalAgentBackendProvider(
        tmp_path,
        backend_factory=lambda resume: calls.append(resume) or FakeBackend(),
    )

    with pytest.raises(SessionHistoryNotFoundError):
        provider.create_session(resume_session_id="session_missing1")
    with pytest.raises(InvalidSessionHistoryIdError):
        provider.create_session(resume_session_id="../session_missing1")
    assert calls == []


def test_resume_rejects_malformed_or_nonresumable_records(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_bad1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(event(1, "session_start", {"state": "RUNNING"})) + "\n")
    provider = LocalAgentBackendProvider(
        tmp_path,
        backend_factory=lambda _resume: FakeBackend(),
        resume_validator=lambda _path, _id: (_ for _ in ()).throw(ValueError("bad")),
    )

    with pytest.raises(SessionResumeUnavailableError) as failure:
        provider.create_session(resume_session_id="session_bad1")
    assert str(failure.value) == "session cannot be resumed"
    assert str(tmp_path) not in str(failure.value)


def test_assembled_resume_factory_rechecks_filename_identity_after_preflight(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = "session_resume_race"
    path = tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl"
    write_resumable(path, session_id)
    config = AppConfig(
        workspace=tmp_path,
        api_key="placeholder",
        approval_mode="auto",
        context_window=8_000,
        reserved_output_tokens=1_000,
    )
    original_factory = assembly.build_agent_backend

    def replacing_factory(*args, **kwargs):
        if kwargs.get("resume") == session_id:
            replacement = [
                record | {"session_id": "session_replaced"}
                for record in (json.loads(line) for line in path.read_text().splitlines())
            ]
            path.write_text(
                "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in replacement),
                encoding="utf-8",
            )
        return original_factory(*args, **kwargs)

    monkeypatch.setattr(assembly, "build_agent_backend", replacing_factory)
    provider = build_agent_backend_provider(config, interactive=False)

    with pytest.raises(SessionResumeUnavailableError) as failure:
        provider.create_session(resume_session_id=session_id)
    assert str(failure.value) == "session cannot be resumed"
