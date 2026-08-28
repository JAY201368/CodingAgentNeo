"""Registry, activation, and protocol-boundary tests."""

from __future__ import annotations

import json

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.models import ToolCall, ToolResultStatus
from coding_agent_neo.runtime import CancellationSignal, ToolExecutionContext
from coding_agent_neo.tools import (
    BUILTIN_TOOL_NAMES,
    ToolRegistry,
    default_tool_registry,
    register_builtin_tools,
)


def _context(environment: FakeExecutionEnvironment, correlation: str = "correlation-1"):
    return ToolExecutionContext(
        agent_id="agent-1",
        correlation_id=correlation,
        provider_tool_call_id="provider/call-1",
        environment=environment,
        cancellation=CancellationSignal(),
    )


def test_registered_and_active_tools_are_separate() -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry, active=False)

    assert registry.registered_names == BUILTIN_TOOL_NAMES
    assert registry.active_names == ()
    assert registry.active_schemas() == ()
    registry.activate("read_file", "search")
    assert registry.active_names == ("read_file", "search")
    assert [item["function"]["name"] for item in registry.schemas()] == [
        "read_file",
        "search",
    ]


def test_unknown_inactive_and_invalid_arguments_are_structured_and_side_effect_free() -> None:
    environment = FakeExecutionEnvironment()
    registry = default_tool_registry()
    context = _context(environment)

    unknown = registry.execute("not_registered", "{}", context)
    assert unknown.status is ToolResultStatus.INVALID
    assert unknown.metadata["error_code"] == "unknown_tool"
    assert environment.calls == []

    registry.deactivate("bash")
    inactive = registry.execute("bash", '{"command":"echo hi"}', context)
    assert inactive.status is ToolResultStatus.INVALID
    assert inactive.metadata["error_code"] == "inactive_tool"
    assert environment.calls == []

    for arguments, code in (
        ('{"path":', "invalid_json"),
        ('{"path": 7}', "invalid_type"),
        ("{}", "missing_argument"),
    ):
        invalid = registry.execute("read_file", arguments, context)
        assert invalid.status is ToolResultStatus.INVALID
        assert invalid.metadata["error_code"] == code
        assert invalid.correlation_id == context.correlation_id
        assert invalid.provider_tool_call_id == context.provider_tool_call_id
        assert environment.calls == []


def test_tool_call_ids_are_preserved_and_correlation_is_not_provider_id() -> None:
    environment = FakeExecutionEnvironment()
    registry = default_tool_registry()
    call = ToolCall(
        correlation_id="correlation-call",
        provider_tool_call_id="provider/call=opaque value",
        name="read_file",
        raw_arguments='{"path":"src/a.py"}',
    )
    context = ToolExecutionContext(
        agent_id="agent-1",
        correlation_id="correlation-call",
        environment=environment,
        cancellation=CancellationSignal(),
    )
    result = registry.execute(call, context)
    assert result.status is ToolResultStatus.SUCCESS
    assert result.correlation_id == "correlation-call"
    assert result.provider_tool_call_id == "provider/call=opaque value"
    assert result.correlation_id != result.provider_tool_call_id
    assert environment.calls[0].request.path == "src/a.py"


def test_active_schemas_are_json_serializable_and_stable() -> None:
    registry = default_tool_registry()
    encoded = json.dumps(registry.active_schemas(), ensure_ascii=False)
    decoded = json.loads(encoded)
    assert [item["function"]["name"] for item in decoded] == list(BUILTIN_TOOL_NAMES)
    for item in decoded:
        assert item["type"] == "function"
        assert item["function"]["parameters"]["type"] == "object"


def test_every_public_schema_surface_is_active_only() -> None:
    registry = default_tool_registry(active_tools=("read_file", "search"))
    expected = {"read_file", "search"}
    surfaces = (
        registry.active_schemas,
        registry.schemas,
        registry.active_tool_schemas,
        registry.get_active_schemas,
        registry.get_schemas,
        registry.get_tool_schemas,
    )

    for surface in surfaces:
        schemas = surface()
        assert {schema["function"]["name"] for schema in schemas} == expected

    for name in ("bash", "write_file"):
        for query in (registry.schema_for, registry.get_schema):
            try:
                query(name)
            except KeyError as exc:
                assert "not active" in str(exc)
            else:
                raise AssertionError(f"inactive schema leaked for {name}")

    for removed_surface in ("all_schemas", "registered_schemas", "get_registered_schemas"):
        assert not hasattr(registry, removed_surface)
