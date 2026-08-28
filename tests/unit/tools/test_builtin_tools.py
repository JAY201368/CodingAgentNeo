"""Built-in tool request translation and result normalization tests."""

from __future__ import annotations

from tests.unit.fake_environment import EnvironmentCall, FakeExecutionEnvironment

from coding_agent_neo.models import (
    CommandResult,
    EnvironmentStatus,
    FileResult,
    ListResult,
    SearchMatch,
    SearchResult,
    ToolResultStatus,
)
from coding_agent_neo.runtime import CancellationRequested, CancellationSignal, ToolExecutionContext
from coding_agent_neo.tools import default_tool_registry


def _context(environment: FakeExecutionEnvironment, name: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        agent_id="agent-1",
        correlation_id=f"correlation-{name}",
        provider_tool_call_id=f"provider-{name}",
        environment=environment,
        cancellation=CancellationSignal(),
    )


def test_six_builtins_pass_backend_requests_and_same_cancellation_signal() -> None:
    environment = FakeExecutionEnvironment(
        responses={
            "read_file": FileResult(path="src/a.py", content="pass"),
            "list_files": ListResult(entries=["src", "src/a.py"]),
            "search": SearchResult(matches=[SearchMatch("src/a.py", 1, "pass")]),
            "write_file": FileResult(path="src/new.py", content="pass"),
            "edit_file": FileResult(path="src/a.py", content="return None"),
            "run_command": CommandResult(stdout="ok", exit_code=0),
        }
    )
    registry = default_tool_registry()
    arguments = {
        "read_file": {"path": "src/a.py", "start_line": 1, "end_line": 2, "max_bytes": 10},
        "list_files": {"path": "src", "recursive": True, "max_entries": 3},
        "search": {"query": "pass", "path": "src", "use_regex": False, "max_results": 4},
        "write_file": {"path": "src/new.py", "content": "pass", "max_bytes": 10},
        "edit_file": {
            "path": "src/a.py",
            "old_text": "pass",
            "new_text": "return None",
            "expected_replacements": 1,
        },
        "bash": {"command": "pytest", "timeout_seconds": 3.0, "max_output_bytes": 20},
    }
    for name in registry.registered_names:
        context = _context(environment, name)
        result = registry.execute(name, arguments[name], context)
        assert result.status is ToolResultStatus.SUCCESS
        assert result.correlation_id == context.correlation_id
        assert result.provider_tool_call_id == context.provider_tool_call_id
        assert environment.calls[-1].operation == {
            "bash": "run_command",
            "list_files": "list_files",
        }.get(name, name)
        assert environment.calls[-1].cancellation is context.cancellation

    assert environment.calls[0].request.path == "src/a.py"
    assert environment.calls[1].request.recursive is True
    assert environment.calls[2].request.query == "pass"
    assert environment.calls[3].request.content == "pass"
    assert environment.calls[4].request.expected_replacements == 1
    assert environment.calls[5].request.timeout_seconds == 3.0


def test_environment_failures_are_normalized_without_leaking_backend_types() -> None:
    environment = FakeExecutionEnvironment(
        responses={
            "read_file": FileResult(
                status=EnvironmentStatus.ERROR,
                message="file does not exist",
                path="src/missing.py",
                metadata={"reason": "file_error"},
                duration_seconds=0.25,
            ),
            "run_command": CommandResult(
                status=EnvironmentStatus.TIMEOUT,
                message="command timed out",
                timed_out=True,
                exit_code=None,
                duration_seconds=1.5,
            ),
        }
    )
    registry = default_tool_registry()
    file_result = registry.execute(
        "read_file", {"path": "src/missing.py"}, _context(environment, "read")
    )
    command_result = registry.execute("bash", {"command": "sleep 2"}, _context(environment, "bash"))
    assert file_result.status is ToolResultStatus.ERROR
    assert file_result.text == "file does not exist"
    assert file_result.path == "src/missing.py"
    assert file_result.duration_seconds == 0.25
    assert command_result.status is ToolResultStatus.TIMEOUT
    assert command_result.timed_out is True
    assert command_result.timeout is True


def test_invalid_cross_field_or_nul_arguments_do_not_call_environment() -> None:
    environment = FakeExecutionEnvironment()
    registry = default_tool_registry()
    context = _context(environment, "invalid")
    result = registry.execute(
        "read_file", {"path": "a.py", "start_line": 4, "end_line": 2}, context
    )
    assert result.status is ToolResultStatus.INVALID
    assert environment.calls == []

    result = registry.execute("write_file", {"path": "a.py", "content": "bad\x00text"}, context)
    assert result.status is ToolResultStatus.INVALID
    assert environment.calls == []


def test_raised_cancellation_and_timeout_are_normalized() -> None:
    class RaisingEnvironment(FakeExecutionEnvironment):
        def read_file(self, request, cancellation):
            self.calls.append(EnvironmentCall("read_file", request, cancellation))
            if cancellation.is_cancelled:
                raise CancellationRequested("cancelled")
            raise RuntimeError("unexpected")

        def run_command(self, request, cancellation):
            self.calls.append(EnvironmentCall("run_command", request, cancellation))
            raise TimeoutError("timed out")

    environment = RaisingEnvironment()
    registry = default_tool_registry()
    cancellation = CancellationSignal()
    cancellation.cancel("user interrupt")
    context = ToolExecutionContext(
        agent_id="agent-1",
        correlation_id="correlation-cancel",
        environment=environment,
        cancellation=cancellation,
    )
    cancelled = registry.execute("read_file", {"path": "a.py"}, context)
    assert cancelled.status is ToolResultStatus.CANCELLED
    timeout = registry.execute(
        "bash",
        {"command": "pytest"},
        ToolExecutionContext(
            agent_id="agent-1",
            correlation_id="correlation-timeout",
            environment=environment,
            cancellation=CancellationSignal(),
        ),
    )
    assert timeout.status is ToolResultStatus.TIMEOUT
    assert timeout.timed_out is True
