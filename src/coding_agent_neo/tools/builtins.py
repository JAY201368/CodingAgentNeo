"""The six built-in tools exposed by CodingAgentNeo.

Each implementation only translates JSON arguments into a backend-neutral
environment request and translates the resulting response into ``ToolResult``.
It never obtains execution capabilities from anywhere other than the supplied
``ToolExecutionContext``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from coding_agent_neo.environment.base import (
    CommandResult,
    EditFileRequest,
    EnvironmentResponse,
    FileResult,
    ListFilesRequest,
    ListResult,
    ReadFileRequest,
    RunCommandRequest,
    SearchRequest,
    SearchResult,
    WriteFileRequest,
)
from coding_agent_neo.models import EnvironmentStatus, ToolResult, ToolResultStatus
from coding_agent_neo.runtime import CancellationRequested, ToolExecutionContext

from .protocol import Tool
from .schema import ToolProtocolError, ensure_json_schema, validate_arguments

READ_FILE = "read_file"
LIST_FILES = "list_files"
SEARCH = "search"
WRITE_FILE = "write_file"
EDIT_FILE = "edit_file"
BASH = "bash"

BUILTIN_TOOL_NAMES = (READ_FILE, LIST_FILES, SEARCH, WRITE_FILE, EDIT_FILE, BASH)


def _string_or_message(value: str, message: str) -> str:
    return value if value else message


def _result_text(response: EnvironmentResponse, value: str) -> str:
    """Prefer a backend diagnostic for failures and output for successes."""

    try:
        status = EnvironmentStatus(response.status)
    except (TypeError, ValueError):
        status = EnvironmentStatus.ERROR
    if status is EnvironmentStatus.SUCCESS:
        return _string_or_message(value, response.message)
    return _string_or_message(response.message, value)


def _status_for_environment(response: EnvironmentResponse) -> ToolResultStatus:
    try:
        status = EnvironmentStatus(response.status)
    except (TypeError, ValueError):
        return ToolResultStatus.ERROR
    if status is EnvironmentStatus.SUCCESS:
        return ToolResultStatus.SUCCESS
    if status is EnvironmentStatus.CANCELLED:
        return ToolResultStatus.CANCELLED
    if status is EnvironmentStatus.TIMEOUT:
        return ToolResultStatus.TIMEOUT
    return ToolResultStatus.ERROR


def _base_result(
    context: ToolExecutionContext,
    response: EnvironmentResponse,
    *,
    text: str,
    path: str | None = None,
    original_length: int | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> ToolResult:
    status = _status_for_environment(response)
    if status is ToolResultStatus.SUCCESS and timed_out:
        status = ToolResultStatus.TIMEOUT
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=status,
        text=text,
        metadata=dict(response.metadata),
        truncated=response.truncated if hasattr(response, "truncated") else False,
        original_length=original_length,
        duration_seconds=response.duration_seconds,
        exit_code=exit_code,
        timed_out=timed_out,
        path=path,
    )


def _file_result(
    context: ToolExecutionContext,
    response: FileResult,
    *,
    requested_path: str,
) -> ToolResult:
    return _base_result(
        context,
        response,
        text=_result_text(response, response.content),
        path=response.path or requested_path,
        original_length=response.original_length,
    )


def _list_result(context: ToolExecutionContext, response: ListResult) -> ToolResult:
    text = "\n".join(response.entries)
    return _base_result(
        context,
        response,
        text=_result_text(response, text),
        original_length=response.original_length,
    )


def _search_result(context: ToolExecutionContext, response: SearchResult) -> ToolResult:
    text = "\n".join(f"{match.path}:{match.line_number}:{match.text}" for match in response.matches)
    return _base_result(
        context,
        response,
        text=_result_text(response, text),
        original_length=response.original_length,
    )


def _command_result(context: ToolExecutionContext, response: CommandResult) -> ToolResult:
    text = response.stdout
    if response.stderr:
        text = f"{text}\n{response.stderr}" if text else response.stderr
    text = _string_or_message(text, response.message)
    status = _status_for_environment(response)
    if response.timed_out:
        status = ToolResultStatus.TIMEOUT
    elif status is ToolResultStatus.SUCCESS and response.exit_code not in (None, 0):
        status = ToolResultStatus.ERROR
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=status,
        text=text,
        metadata=dict(response.metadata),
        truncated=response.truncated,
        original_length=response.original_output_length,
        duration_seconds=response.duration_seconds,
        exit_code=response.exit_code,
        timed_out=response.timed_out,
    )


def _failure_result(context: ToolExecutionContext, exc: Exception) -> ToolResult:
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=ToolResultStatus.ERROR,
        text=f"tool execution failed: {type(exc).__name__}: {exc}",
        metadata={"error_type": type(exc).__name__},
    )


def _cancelled_result(context: ToolExecutionContext, exc: Exception) -> ToolResult:
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=ToolResultStatus.CANCELLED,
        text=str(exc) or context.cancellation.reason or "operation cancelled",
        metadata={"reason": "cancelled"},
    )


def _timeout_result(context: ToolExecutionContext, exc: Exception) -> ToolResult:
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=ToolResultStatus.TIMEOUT,
        text=str(exc) or "operation timed out",
        metadata={"reason": "timeout"},
        timed_out=True,
    )


def _invalid_argument_result(context: ToolExecutionContext, error: ToolProtocolError) -> ToolResult:
    return ToolResult(
        correlation_id=context.correlation_id,
        provider_tool_call_id=context.provider_tool_call_id,
        status=ToolResultStatus.INVALID,
        text=error.message,
        metadata={
            "error": error.as_dict(),
            "error_code": str(error.code),
        },
    )


class BuiltinTool:
    """Shared schema and argument handling for a built-in tool."""

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]

    @property
    def schema(self) -> dict[str, Any]:
        """Return the descriptive schema listed by the tool registry."""

        return {
            "name": self.name,
            "description": self.description,
            "parameters": ensure_json_schema(self.parameters),
        }

    @property
    def json_schema(self) -> dict[str, Any]:
        """Return only the JSON Schema object for arguments."""

        return ensure_json_schema(self.parameters)

    @property
    def parameter_schema(self) -> dict[str, Any]:
        return self.json_schema

    arguments_schema = parameter_schema

    @property
    def openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI-compatible function-tool wrapper."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            },
        }

    function_schema = openai_schema
    tool_schema = openai_schema

    def validate(self, arguments: str | Mapping[str, Any]) -> dict[str, Any]:
        return validate_arguments(arguments, self.parameters)

    def execute(
        self, arguments: str | Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        try:
            parsed = self.validate(arguments)
        except ToolProtocolError as exc:
            return _invalid_argument_result(context, exc)
        except Exception as exc:
            error = ToolProtocolError("invalid_value", str(exc) or "invalid tool arguments")
            return _invalid_argument_result(context, error)
        try:
            return self._execute_validated(parsed, context)
        except (TypeError, ValueError) as exc:
            error = ToolProtocolError("invalid_value", str(exc) or "invalid tool arguments")
            return _invalid_argument_result(context, error)

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        raise NotImplementedError

    def _call(
        self,
        context: ToolExecutionContext,
        request: Any,
        operation: Callable[[Any, Any], EnvironmentResponse],
        normalize: Callable[[EnvironmentResponse], ToolResult],
    ) -> ToolResult:
        try:
            response = operation(request, context.cancellation)
            return normalize(response)
        except CancellationRequested as exc:
            return _cancelled_result(context, exc)
        except TimeoutError as exc:
            return _timeout_result(context, exc)
        except Exception as exc:
            return _failure_result(context, exc)


class ReadFileTool(BuiltinTool):
    name = READ_FILE
    description = "Read a bounded UTF-8 text file from the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "start_line": {"type": "integer", "minimum": 0},
            "end_line": {"type": "integer", "minimum": 0},
            "max_bytes": {"type": "integer", "minimum": 0},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = ReadFileRequest(
            path=arguments["path"],
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
            max_bytes=arguments.get("max_bytes"),
        )
        return self._call(
            context,
            request,
            context.environment.read_file,
            lambda response: _file_result(context, response, requested_path=request.path),
        )


class ListFilesTool(BuiltinTool):
    name = LIST_FILES
    description = "List bounded entries in a workspace directory."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 0},
            "recursive": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = ListFilesRequest(
            path=arguments.get("path", ""),
            recursive=arguments.get("recursive", False),
            max_entries=arguments.get("max_entries", 100),
        )
        return self._call(
            context,
            request,
            context.environment.list_files,
            lambda response: _list_result(context, response),
        )


class SearchTool(BuiltinTool):
    name = SEARCH
    description = "Search workspace text using literal or regular-expression matching."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 0},
            "use_regex": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 0},
            "max_bytes": {"type": "integer", "minimum": 0},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = SearchRequest(
            query=arguments["query"],
            path=arguments.get("path", ""),
            use_regex=arguments.get("use_regex", False),
            max_results=arguments.get("max_results", 100),
            max_bytes=arguments.get("max_bytes"),
        )
        return self._call(
            context,
            request,
            context.environment.search,
            lambda response: _search_result(context, response),
        )


class WriteFileTool(BuiltinTool):
    name = WRITE_FILE
    description = "Create or replace a UTF-8 text file in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
            "max_bytes": {"type": "integer", "minimum": 0},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = WriteFileRequest(
            path=arguments["path"],
            content=arguments["content"],
            max_bytes=arguments.get("max_bytes"),
        )
        return self._call(
            context,
            request,
            context.environment.write_file,
            lambda response: _file_result(context, response, requested_path=request.path),
        )


class EditFileTool(BuiltinTool):
    name = EDIT_FILE
    description = "Replace an exact text occurrence count in a workspace file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
            "expected_replacements": {"type": "integer", "minimum": 1},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = EditFileRequest(
            path=arguments["path"],
            old_text=arguments["old_text"],
            new_text=arguments["new_text"],
            expected_replacements=arguments.get("expected_replacements", 1),
        )
        return self._call(
            context,
            request,
            context.environment.edit_file,
            lambda response: _file_result(context, response, requested_path=request.path),
        )


class BashTool(BuiltinTool):
    name = BASH
    description = "Run a shell command through the current execution environment."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "number", "minimum": 0},
            "max_output_bytes": {"type": "integer", "minimum": 0},
            "working_directory": {"type": "string", "minLength": 0},
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        request = RunCommandRequest(
            command=arguments["command"],
            timeout_seconds=arguments.get("timeout_seconds"),
            max_output_bytes=arguments.get("max_output_bytes"),
            working_directory=arguments.get("working_directory"),
        )
        return self._call(
            context,
            request,
            context.environment.run_command,
            lambda response: _command_result(context, response),
        )


BUILTIN_TOOL_TYPES = (
    ReadFileTool,
    ListFilesTool,
    SearchTool,
    WriteFileTool,
    EditFileTool,
    BashTool,
)
BUILTIN_TOOLS: tuple[Tool, ...] = tuple(tool_type() for tool_type in BUILTIN_TOOL_TYPES)


def builtin_tools() -> tuple[Tool, ...]:
    """Create fresh built-in tool instances in stable schema order."""

    return tuple(tool_type() for tool_type in BUILTIN_TOOL_TYPES)


def get_builtin_tool(name: str) -> Tool:
    """Return a fresh built-in tool by its stable public name."""

    for tool in builtin_tools():
        if tool.name == name:
            return tool
    raise KeyError(name)


ReadFile = ReadFileTool
ListFiles = ListFilesTool
Search = SearchTool
WriteFile = WriteFileTool
EditFile = EditFileTool
Bash = BashTool
BuiltinTools = builtin_tools


__all__ = [
    "BASH",
    "Bash",
    "BUILTIN_TOOL_NAMES",
    "BUILTIN_TOOL_TYPES",
    "BUILTIN_TOOLS",
    "BashTool",
    "BuiltinTool",
    "BuiltinTools",
    "EDIT_FILE",
    "EditFile",
    "EditFileTool",
    "LIST_FILES",
    "ListFiles",
    "ListFilesTool",
    "READ_FILE",
    "ReadFile",
    "ReadFileTool",
    "SEARCH",
    "Search",
    "SearchTool",
    "WRITE_FILE",
    "WriteFile",
    "WriteFileTool",
    "builtin_tools",
    "get_builtin_tool",
]
