"""Backend-neutral execution environment protocol.

Only the interface lives here.  Local filesystem and subprocess behaviour is
intentionally deferred to ``LocalExecutionEnvironment`` in the next task;
this module is safe to import in tests and in future non-local backends.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from coding_agent_neo.models import (
    CommandRequest,
    CommandResult,
    EditFileRequest,
    EditFileResult,
    EnvironmentRequest,
    EnvironmentResponse,
    FileResult,
    ListFilesRequest,
    ListResult,
    ReadFileRequest,
    ReadFileResult,
    RunCommandRequest,
    RunCommandResult,
    SearchRequest,
    SearchResult,
    WriteFileRequest,
    WriteFileResult,
)
from coding_agent_neo.runtime import CancellationSignal


@runtime_checkable
class ExecutionEnvironment(Protocol):
    """Lifecycle and six logical operations available to tools.

    Requests and results contain logical paths, limits, status and structured
    output only.  They do not expose local absolute paths, Docker IDs, exec
    handles, subprocess objects or other backend-specific implementation
    details.
    """

    def start(self) -> None:
        """Initialize the environment before the first operation."""

    def close(self) -> None:
        """Release environment resources; should be safe to call once."""

    def read_file(self, request: ReadFileRequest, cancellation: CancellationSignal) -> FileResult:
        """Read a bounded text file identified by a logical path."""

    def list_files(self, request: ListFilesRequest, cancellation: CancellationSignal) -> ListResult:
        """List bounded logical entries below a logical path."""

    def search(self, request: SearchRequest, cancellation: CancellationSignal) -> SearchResult:
        """Search text or a regular expression within the logical workspace."""

    def write_file(self, request: WriteFileRequest, cancellation: CancellationSignal) -> FileResult:
        """Create or replace text at a logical path."""

    def edit_file(self, request: EditFileRequest, cancellation: CancellationSignal) -> FileResult:
        """Apply a validated exact-text edit at a logical path."""

    def run_command(
        self, request: RunCommandRequest, cancellation: CancellationSignal
    ) -> CommandResult:
        """Run a command under the environment's own execution boundaries."""


__all__ = [
    "CommandRequest",
    "CommandResult",
    "EditFileRequest",
    "EditFileResult",
    "EnvironmentRequest",
    "EnvironmentResponse",
    "ExecutionEnvironment",
    "FileResult",
    "ListFilesRequest",
    "ListResult",
    "ReadFileRequest",
    "ReadFileResult",
    "RunCommandRequest",
    "RunCommandResult",
    "SearchRequest",
    "SearchResult",
    "WriteFileRequest",
    "WriteFileResult",
]
