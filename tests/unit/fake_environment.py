"""In-memory ExecutionEnvironment fake for unit and integration tests.

The fake deliberately records requests and returns configured data.  It never
opens files, invokes ``rg``, starts subprocesses, or otherwise touches the
host environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coding_agent_neo.environment.base import (
    CommandResult,
    EditFileRequest,
    ExecutionEnvironment,
    FileResult,
    ListFilesRequest,
    ListResult,
    ReadFileRequest,
    RunCommandRequest,
    SearchRequest,
    SearchResult,
    WriteFileRequest,
)
from coding_agent_neo.models import EnvironmentStatus
from coding_agent_neo.runtime import CancellationSignal


@dataclass(frozen=True, slots=True)
class EnvironmentCall:
    """One recorded logical environment operation."""

    operation: str
    request: object
    cancellation: CancellationSignal


@dataclass(slots=True)
class FakeExecutionEnvironment:
    """A configurable, side-effect-free implementation of the protocol."""

    responses: dict[str, object] = field(default_factory=dict)
    calls: list[EnvironmentCall] = field(default_factory=list)
    started: bool = False
    closed: bool = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def _response(self, operation: str, request: object, cancellation: CancellationSignal) -> Any:
        self.calls.append(EnvironmentCall(operation, request, cancellation))
        configured = self.responses.get(operation)
        if configured is not None:
            return configured
        if cancellation.is_cancelled:
            if operation in {"read_file", "write_file", "edit_file"}:
                return FileResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
            if operation == "list_files":
                return ListResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
            if operation == "search":
                return SearchResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
            return CommandResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
        if operation in {"read_file", "write_file", "edit_file"}:
            path = getattr(request, "path", None)
            content = getattr(request, "content", "")
            return FileResult(path=path, content=content)
        if operation == "list_files":
            return ListResult()
        if operation == "search":
            return SearchResult()
        return CommandResult()

    def read_file(self, request: ReadFileRequest, cancellation: CancellationSignal) -> FileResult:
        return self._response("read_file", request, cancellation)

    def list_files(self, request: ListFilesRequest, cancellation: CancellationSignal) -> ListResult:
        return self._response("list_files", request, cancellation)

    def search(self, request: SearchRequest, cancellation: CancellationSignal) -> SearchResult:
        return self._response("search", request, cancellation)

    def write_file(self, request: WriteFileRequest, cancellation: CancellationSignal) -> FileResult:
        return self._response("write_file", request, cancellation)

    def edit_file(self, request: EditFileRequest, cancellation: CancellationSignal) -> FileResult:
        return self._response("edit_file", request, cancellation)

    def run_command(
        self, request: RunCommandRequest, cancellation: CancellationSignal
    ) -> CommandResult:
        return self._response("run_command", request, cancellation)


FakeEnvironment = FakeExecutionEnvironment


assert isinstance(FakeExecutionEnvironment(), ExecutionEnvironment)


__all__ = ["EnvironmentCall", "FakeEnvironment", "FakeExecutionEnvironment"]
