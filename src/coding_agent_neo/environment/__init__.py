"""Execution-environment contracts.

Concrete local and container implementations are intentionally supplied by
later tasks; importing this package only exposes backend-neutral contracts.
"""

from coding_agent_neo.environment.base import (
    CommandRequest,
    CommandResult,
    EditFileRequest,
    EditFileResult,
    EnvironmentRequest,
    EnvironmentResponse,
    ExecutionEnvironment,
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
