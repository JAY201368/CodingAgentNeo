"""Unit tests for backend-neutral domain models."""

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from coding_agent_neo.models import (
    AgentId,
    CorrelationId,
    EditFileRequest,
    EnvironmentStatus,
    EventEnvelope,
    EventType,
    FileResult,
    ListResult,
    ProviderToolCallId,
    ReadFileRequest,
    SearchMatch,
    SearchRequest,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    WriteFileRequest,
)


def test_identifier_types_validate_values_and_remain_strings() -> None:
    agent_id = AgentId("agent-1")
    assert isinstance(agent_id, str)
    assert agent_id == "agent-1"

    for invalid in ("", "agent with spaces", "agent/escape", "\x00"):
        with pytest.raises((TypeError, ValueError)):
            AgentId(invalid)

    with pytest.raises(TypeError):
        AgentId(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentId(CorrelationId("correlation-1"))


def test_provider_tool_call_id_preserves_opaque_provider_format() -> None:
    opaque = "provider/call=opaque value"
    provider_id = ProviderToolCallId(opaque)
    assert provider_id == opaque
    assert str(provider_id) == opaque

    for invalid in ("", "provider\x00call", 42, CorrelationId("correlation-1")):
        with pytest.raises((TypeError, ValueError)):
            ProviderToolCallId(invalid)  # type: ignore[arg-type]


def test_event_envelope_requires_and_separates_identity_fields() -> None:
    envelope = EventEnvelope(
        schema_version=1,
        session_id="session-1",
        event_id="event-1",
        agent_id="agent-1",
        sequence=3,
        type=EventType.TOOL_CALL,
        timestamp=datetime(2026, 8, 28, 12, 30, tzinfo=UTC),
        parent_agent_id="parent-1",
        correlation_id="correlation-1",
        provider_tool_call_id="call_1",
        payload={"name": "read_file"},
    )

    assert envelope.schema_version == 1
    assert envelope.type == "tool_call"
    assert envelope.event_type == "tool_call"
    assert envelope.timestamp.endswith("Z")
    assert isinstance(envelope.session_id, str)
    assert isinstance(envelope.event_id, str)
    assert isinstance(envelope.agent_id, str)
    assert isinstance(envelope.correlation_id, CorrelationId)
    assert isinstance(envelope.provider_tool_call_id, ProviderToolCallId)
    assert envelope.correlation_id != envelope.provider_tool_call_id
    assert envelope.to_dict()["payload"] == {"name": "read_file"}


def test_event_envelope_rejects_missing_or_invalid_required_values() -> None:
    required = dict(
        schema_version=1,
        session_id="session-1",
        event_id="event-1",
        agent_id="agent-1",
        sequence=0,
        type="user_message",
        timestamp="2026-08-28T12:30:00Z",
    )
    with pytest.raises(ValueError):
        EventEnvelope(**{**required, "timestamp": None})
    with pytest.raises(ValueError):
        EventEnvelope(**{**required, "sequence": -1})
    with pytest.raises(ValueError):
        EventEnvelope(**{**required, "event_id": "bad id"})
    with pytest.raises(ValueError):
        EventEnvelope(**{**required, "type": None})
    with pytest.raises(ValueError):
        EventEnvelope(**{**required, "timestamp": "2026-08-28T12:30:00"})


def test_environment_requests_validate_limits_but_keep_logical_paths() -> None:
    assert ReadFileRequest("../candidate.py").path == "../candidate.py"
    assert WriteFileRequest("src/candidate.py", "pass").content == "pass"
    assert EditFileRequest("src/candidate.py", "old", "new").expected_replacements == 1
    assert SearchRequest("needle", path="src", max_results=4).max_results == 4

    with pytest.raises(ValueError):
        ReadFileRequest("file.txt", start_line=-1)
    with pytest.raises(ValueError):
        ReadFileRequest("file.txt", start_line=4, end_line=2)
    with pytest.raises(ValueError):
        SearchRequest("", max_results=1)
    with pytest.raises(ValueError):
        EditFileRequest("file.txt", "old", "new", expected_replacements=0)


def test_result_models_are_controlled_and_backend_neutral() -> None:
    metadata = {"source": "fake"}
    result = FileResult(path="src/a.py", content="pass", metadata=metadata)
    metadata["secret"] = "not copied"
    assert result.metadata == {"source": "fake"}
    with pytest.raises(TypeError):
        result.metadata["new"] = "value"  # type: ignore[index]

    list_result = ListResult(entries=["src", "src/a.py"])
    match = SearchMatch("src/a.py", 1, "pass")
    tool_call = ToolCall(
        correlation_id="correlation-1",
        provider_tool_call_id="provider-1",
        name="read_file",
        raw_arguments='{"path":"src/a.py"}',
        parsed_arguments={"path": "src/a.py"},
    )
    tool_result = ToolResult(
        correlation_id=tool_call.correlation_id,
        provider_tool_call_id=tool_call.provider_tool_call_id,
        status=ToolResultStatus.SUCCESS,
        text="pass",
        path="src/a.py",
    )
    assert list_result.paths == ("src", "src/a.py")
    assert match.path == "src/a.py"
    assert tool_call.arguments == {"path": "src/a.py"}
    assert tool_result.ok
    assert EnvironmentStatus.SUCCESS.value == "success"
    assert json.loads(json.dumps(asdict(result))) == {
        "status": "success",
        "message": "",
        "metadata": {"source": "fake"},
        "duration_seconds": None,
        "path": "src/a.py",
        "content": "pass",
        "truncated": False,
        "original_length": None,
    }
