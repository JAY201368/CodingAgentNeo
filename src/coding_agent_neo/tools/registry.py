"""Registration, activation, and dispatch for tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, ClassVar
from uuid import uuid4

from coding_agent_neo.models import (
    CorrelationId,
    ProviderToolCallId,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from coding_agent_neo.runtime import ToolExecutionContext

from .builtins import BUILTIN_TOOL_NAMES, builtin_tools
from .protocol import Tool
from .schema import ProtocolErrorCode, ToolProtocolError, ensure_json_schema


def _fresh_correlation_id() -> CorrelationId:
    return CorrelationId(f"correlation_{uuid4().hex}")


def _error_metadata(
    code: ProtocolErrorCode | str,
    message: str,
    *,
    path: str = "$",
    details: Mapping[str, Any] | None = None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "code": str(code),
        "message": message,
        "path": path,
        "details": dict(details or {}),
    }
    result: dict[str, Any] = {
        "error": diagnostic,
        "error_code": str(code),
    }
    if tool_name is not None:
        result["tool_name"] = tool_name
    return result


def _invalid_result(
    correlation_id: CorrelationId | str,
    provider_tool_call_id: ProviderToolCallId | str | None,
    error: ToolProtocolError,
    *,
    tool_name: str | None = None,
) -> ToolResult:
    return ToolResult(
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        status=ToolResultStatus.INVALID,
        text=error.message,
        metadata=_error_metadata(
            error.code,
            error.message,
            path=error.path,
            details=error.details,
            tool_name=tool_name,
        ),
    )


def _internal_error(
    correlation_id: CorrelationId | str,
    provider_tool_call_id: ProviderToolCallId | str | None,
    exc: Exception,
    *,
    tool_name: str | None = None,
) -> ToolResult:
    message = f"tool execution failed: {type(exc).__name__}: {exc}"
    return ToolResult(
        correlation_id=correlation_id,
        provider_tool_call_id=provider_tool_call_id,
        status=ToolResultStatus.ERROR,
        text=message,
        metadata=_error_metadata(
            ProtocolErrorCode.INTERNAL_TOOL_ERROR,
            message,
            details={"exception_type": type(exc).__name__},
            tool_name=tool_name,
        ),
    )


class ToolRegistry:
    """A small explicit registry with separate registered and active sets.

    Constructing the registry does not activate tools.  This makes accidental
    model exposure impossible: callers explicitly select an active set before
    requesting schemas or dispatching calls.  ``default_tool_registry`` is a
    convenience for the normal six-tool setup and activates all built-ins.
    """

    _BUILTIN_NAMES: ClassVar[tuple[str, ...]] = BUILTIN_TOOL_NAMES

    def __init__(
        self,
        tools: Iterable[Tool] | None = None,
        *,
        active_tools: Iterable[str] | None = None,
        include_builtins: bool = False,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._active: set[str] = set()
        if include_builtins:
            tools = (*builtin_tools(), *(tuple(tools) if tools is not None else ()))
        if tools is not None:
            for tool in tools:
                self.register(tool)
        if active_tools is not None:
            self.set_active(active_tools)

    @classmethod
    def with_builtins(cls, *, active: bool = True) -> ToolRegistry:
        """Create a registry containing the six built-ins."""

        registry = cls(builtin_tools())
        if active:
            registry.set_active(registry.registered_names)
        return registry

    @property
    def registered(self) -> Mapping[str, Tool]:
        """A read-only view of registered tools."""

        return MappingProxyType(self._tools)

    @property
    def registered_tools(self) -> Mapping[str, Tool]:
        return self.registered

    @property
    def tools(self) -> Mapping[str, Tool]:
        return self.registered

    @property
    def registered_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(name for name in self._tools if name in self._active)

    @property
    def active_tool_names(self) -> tuple[str, ...]:
        return self.active_names

    @property
    def active_tools(self) -> frozenset[str]:
        return frozenset(self._active)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools)

    def register(self, tool: Tool, *, active: bool = False, replace_existing: bool = False) -> Tool:
        """Register a tool and optionally activate it immediately."""

        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        parameters = getattr(tool, "parameters", None)
        if parameters is None:
            declared_schema = getattr(tool, "schema", None)
            if callable(declared_schema):
                declared_schema = declared_schema()
            if isinstance(declared_schema, Mapping):
                parameters = declared_schema.get("parameters", declared_schema)
        if not isinstance(name, str) or not name:
            raise TypeError("tool name must be a non-empty string")
        if not isinstance(description, str) or not description:
            raise TypeError("tool description must be a non-empty string")
        if not callable(getattr(tool, "execute", None)):
            raise TypeError("tool must provide an execute method")
        try:
            schema = ensure_json_schema(parameters)
        except Exception as exc:
            raise TypeError("tool parameters must be a JSON-serializable object") from exc
        if schema.get("type") != "object":
            raise ValueError("tool parameters schema must describe an object")
        if name in self._tools and not replace_existing:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool
        if active:
            self._active.add(name)
        elif replace_existing:
            self._active.discard(name)
        return tool

    def register_tool(self, tool: Tool, *, active: bool = False) -> Tool:
        return self.register(tool, active=active)

    def unregister(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(name)
        self._active.discard(name)
        return self._tools.pop(name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_tool(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool: {name}") from None

    def activate(self, *names: str) -> None:
        if len(names) == 1 and not isinstance(names[0], str):
            names = tuple(names[0])  # type: ignore[assignment]
        for name in names:
            if name not in self._tools:
                raise KeyError(f"unknown tool: {name}")
        self._active.update(names)

    def activate_tool(self, name: str) -> None:
        self.activate(name)

    def deactivate(self, *names: str) -> None:
        if len(names) == 1 and not isinstance(names[0], str):
            names = tuple(names[0])  # type: ignore[assignment]
        self._active.difference_update(names)

    def deactivate_tool(self, name: str) -> None:
        self.deactivate(name)

    def set_active(self, names: Iterable[str]) -> None:
        selected = (names,) if isinstance(names, str) else tuple(names)
        unknown = [name for name in selected if name not in self._tools]
        if unknown:
            raise KeyError(f"unknown tool: {unknown[0]}")
        self._active = set(selected)

    def clear_active(self) -> None:
        self._active.clear()

    def is_active(self, name: str) -> bool:
        return name in self._active

    def active_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._tool_schema(self._tools[name]) for name in self._tools if name in self._active
        )

    def schemas(self) -> tuple[dict[str, Any], ...]:
        """Return only schemas for active tools, in registration order."""

        return self.active_schemas()

    def active_tool_schemas(self) -> tuple[dict[str, Any], ...]:
        return self.active_schemas()

    get_active_schemas = active_schemas
    get_schemas = active_schemas
    get_tool_schemas = active_schemas
    set_active_tools = set_active

    def register_builtins(self, *, active: bool = True) -> ToolRegistry:
        for tool in builtin_tools():
            self.register(tool, active=active)
        return self

    def schema_for(self, name: str) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        if name not in self._active:
            raise KeyError(f"tool is not active: {name}")
        return self._tool_schema(self._tools[name])

    get_schema = schema_for

    def validate(self, name: str, arguments: str | Mapping[str, Any]) -> Mapping[str, Any]:
        if name not in self._tools:
            raise ToolProtocolError(ProtocolErrorCode.UNKNOWN_TOOL, f"unknown tool: {name}")
        if name not in self._active:
            raise ToolProtocolError(ProtocolErrorCode.INACTIVE_TOOL, f"tool is not active: {name}")
        tool = self._tools[name]
        validator = getattr(tool, "validate", None)
        if not callable(validator):
            raise TypeError("tool must provide a validate method")
        return validator(arguments)

    @staticmethod
    def _tool_schema(tool: Tool) -> dict[str, Any]:
        openai_schema = getattr(tool, "openai_schema", None)
        if callable(openai_schema):
            openai_schema = openai_schema()
        if isinstance(openai_schema, Mapping):
            return ensure_json_schema(openai_schema)
        schema = getattr(tool, "schema", None)
        if callable(schema):
            schema = schema()
        if not isinstance(schema, Mapping):
            schema = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
        schema_copy = ensure_json_schema(schema)
        if schema_copy.get("type") == "function" and isinstance(
            schema_copy.get("function"), Mapping
        ):
            return schema_copy
        parameter_schema = getattr(tool, "parameters", None)
        if parameter_schema is None:
            parameter_schema = schema_copy.get("parameters", schema_copy)
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": ensure_json_schema(parameter_schema),
            },
        }

    def execute(
        self,
        tool_or_call: str | ToolCall,
        arguments: str | Mapping[str, Any] | ToolExecutionContext | None = None,
        context: ToolExecutionContext | None = None,
        *,
        correlation_id: CorrelationId | str | None = None,
        provider_tool_call_id: ProviderToolCallId | str | None = None,
    ) -> ToolResult:
        """Validate and dispatch a call, returning exactly one ToolResult.

        ``ToolCall`` input preserves its normalized IDs and raw argument JSON.
        For convenience, a name plus arguments can be supplied directly.  A
        malformed, unknown, or inactive call is returned as a structured
        ``INVALID`` result before any environment method is considered.
        """

        if isinstance(arguments, ToolExecutionContext) and context is None:
            context = arguments
            arguments = None

        call: ToolCall | None = tool_or_call if isinstance(tool_or_call, ToolCall) else None
        if call is not None:
            name = call.name
            raw_arguments: str | Mapping[str, Any] | None = call.raw_arguments
            call_correlation: CorrelationId | str = call.correlation_id
            call_provider = call.provider_tool_call_id
            if arguments is not None:
                raw_arguments = arguments  # explicit caller input is authoritative
        else:
            name = tool_or_call
            raw_arguments = arguments if arguments is not None else ""
            call_correlation = correlation_id or (
                context.correlation_id if context is not None else _fresh_correlation_id()
            )
            call_provider = provider_tool_call_id or (
                context.provider_tool_call_id if context is not None else None
            )

        if not isinstance(name, str) or not name:
            error = ToolProtocolError(ProtocolErrorCode.UNKNOWN_TOOL, "tool name is required")
            return _invalid_result(call_correlation, call_provider, error)

        if correlation_id is not None and str(call_correlation) != str(correlation_id):
            error = ToolProtocolError(
                ProtocolErrorCode.CORRELATION_MISMATCH,
                "provided correlation ID does not match the tool call",
                details={"call": str(call_correlation), "provided": str(correlation_id)},
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)

        if name not in self._tools:
            error = ToolProtocolError(
                ProtocolErrorCode.UNKNOWN_TOOL,
                f"unknown tool: {name}",
                details={"registered_tools": list(self.registered_names)},
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)
        if name not in self._active:
            error = ToolProtocolError(
                ProtocolErrorCode.INACTIVE_TOOL,
                f"tool is not active: {name}",
                details={"active_tools": list(self.active_names)},
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)

        if context is None:
            error = ToolProtocolError(
                ProtocolErrorCode.CONTEXT_REQUIRED,
                "ToolExecutionContext is required for tool execution",
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)
        if not isinstance(context, ToolExecutionContext):
            error = ToolProtocolError(
                ProtocolErrorCode.CONTEXT_REQUIRED,
                "context must be a ToolExecutionContext",
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)
        if str(context.correlation_id) != str(call_correlation):
            error = ToolProtocolError(
                ProtocolErrorCode.CORRELATION_MISMATCH,
                "context correlation ID does not match the tool call",
                details={"call": str(call_correlation), "context": str(context.correlation_id)},
            )
            return _invalid_result(call_correlation, call_provider, error, tool_name=name)

        effective_provider = call_provider or context.provider_tool_call_id
        effective_context = context
        if effective_provider != context.provider_tool_call_id:
            effective_context = replace(context, provider_tool_call_id=effective_provider)

        tool = self._tools[name]
        try:
            effective_arguments = raw_arguments if raw_arguments is not None else ""
            result = tool.execute(effective_arguments, effective_context)
        except ToolProtocolError as exc:
            return _invalid_result(
                effective_context.correlation_id,
                effective_context.provider_tool_call_id,
                exc,
                tool_name=name,
            )
        except Exception as exc:
            return _internal_error(
                effective_context.correlation_id,
                effective_context.provider_tool_call_id,
                exc,
                tool_name=name,
            )
        if not isinstance(result, ToolResult):
            return _internal_error(
                effective_context.correlation_id,
                effective_context.provider_tool_call_id,
                TypeError("tool execute must return ToolResult"),
                tool_name=name,
            )
        if (
            result.correlation_id != effective_context.correlation_id
            or result.provider_tool_call_id != effective_context.provider_tool_call_id
        ):
            result = replace(
                result,
                correlation_id=effective_context.correlation_id,
                provider_tool_call_id=effective_context.provider_tool_call_id,
            )
        return result

    dispatch = execute
    invoke = execute
    call = execute


def register_builtin_tools(
    registry: ToolRegistry | None = None,
    *,
    active: bool = True,
) -> ToolRegistry:
    """Register fresh built-ins into ``registry`` and optionally activate them."""

    target = registry if registry is not None else ToolRegistry()
    for tool in builtin_tools():
        target.register(tool, active=active)
    return target


def default_tool_registry(*, active_tools: Iterable[str] | None = None) -> ToolRegistry:
    """Return the standard six-tool registry, active by default."""

    registry = ToolRegistry.with_builtins(active=False)
    selected = registry.registered_names if active_tools is None else tuple(active_tools)
    registry.set_active(selected)
    return registry


BuiltinToolRegistry = ToolRegistry


__all__ = [
    "BuiltinToolRegistry",
    "ToolRegistry",
    "default_tool_registry",
    "register_builtin_tools",
]
