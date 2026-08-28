"""JSON argument schema validation for the tool protocol.

This validator deliberately supports the small JSON Schema subset needed by
the built-in tools.  It is deterministic, returns plain JSON-compatible
values, and reports failures as structured :class:`ToolProtocolError`
instances.  It is not a general-purpose schema implementation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProtocolErrorCode(StrEnum):
    """Stable codes for errors at the model/tool protocol boundary."""

    INVALID_JSON = "invalid_json"
    ARGUMENTS_NOT_OBJECT = "arguments_not_object"
    MISSING_ARGUMENT = "missing_argument"
    UNKNOWN_ARGUMENT = "unknown_argument"
    INVALID_TYPE = "invalid_type"
    INVALID_VALUE = "invalid_value"
    UNKNOWN_TOOL = "unknown_tool"
    INACTIVE_TOOL = "inactive_tool"
    CONTEXT_REQUIRED = "context_required"
    CORRELATION_MISMATCH = "correlation_mismatch"
    INTERNAL_TOOL_ERROR = "internal_tool_error"


@dataclass(frozen=True, slots=True)
class ToolProtocolError(ValueError):
    """A machine-readable protocol failure.

    ``details`` only contains JSON-compatible diagnostic values.  The
    exception is converted to a ``ToolResult`` by the registry, so malformed
    arguments never reach an environment implementation.
    """

    code: ProtocolErrorCode | str
    message: str
    path: str = "$"
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", ProtocolErrorCode(self.code))
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("protocol error message must not be empty")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("protocol error path must not be empty")
        if not isinstance(self.details, Mapping):
            raise TypeError("protocol error details must be a mapping")
        object.__setattr__(self, "details", dict(self.details))
        ValueError.__init__(self, self.message)

    @property
    def error_code(self) -> str:
        """Compatibility spelling used by protocol/result callers."""

        return str(self.code)

    @property
    def kind(self) -> str:
        return str(self.code)

    @property
    def reason(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible structured diagnostic."""

        return {
            "code": str(self.code),
            "message": self.message,
            "path": self.path,
            "details": dict(self.details),
        }


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _raise(
    code: ProtocolErrorCode,
    message: str,
    path: str,
    **details: Any,
) -> None:
    raise ToolProtocolError(code=code, message=message, path=path, details=details)


def _validate_value(value: Any, schema: Mapping[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        expected_values = (expected,) if isinstance(expected, str) else tuple(expected)
        if not expected_values or not all(isinstance(item, str) for item in expected_values):
            _raise(ProtocolErrorCode.INVALID_VALUE, "schema type must be a string", path)
        if not any(_type_matches(value, item) for item in expected_values):
            _raise(
                ProtocolErrorCode.INVALID_TYPE,
                f"expected {', '.join(expected_values)}, got {_json_type(value)}",
                path,
                expected=expected_values[0] if len(expected_values) == 1 else list(expected_values),
                actual=_json_type(value),
            )

    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes, bytearray)):
            _raise(ProtocolErrorCode.INVALID_VALUE, "schema enum must be an array", path)
        if value not in choices:
            _raise(
                ProtocolErrorCode.INVALID_VALUE,
                "value is not one of the allowed choices",
                path,
                allowed=list(choices),
            )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            _raise(
                ProtocolErrorCode.INVALID_VALUE,
                f"string must contain at least {minimum} characters",
                path,
                minimum=minimum,
            )
        if maximum is not None and len(value) > maximum:
            _raise(
                ProtocolErrorCode.INVALID_VALUE,
                f"string must contain at most {maximum} characters",
                path,
                maximum=maximum,
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            _raise(
                ProtocolErrorCode.INVALID_VALUE,
                f"value must be at least {minimum}",
                path,
                minimum=minimum,
            )
        if maximum is not None and value > maximum:
            _raise(
                ProtocolErrorCode.INVALID_VALUE,
                f"value must be at most {maximum}",
                path,
                maximum=maximum,
            )

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            _raise(ProtocolErrorCode.INVALID_VALUE, "schema properties must be an object", path)
        required = schema.get("required", ())
        if not isinstance(required, Sequence) or isinstance(required, (str, bytes, bytearray)):
            _raise(ProtocolErrorCode.INVALID_VALUE, "schema required must be an array", path)
        for key in required:
            if not isinstance(key, str):
                _raise(ProtocolErrorCode.INVALID_VALUE, "required names must be strings", path)
            if key not in value:
                _raise(
                    ProtocolErrorCode.MISSING_ARGUMENT,
                    f"missing required argument: {key}",
                    f"{path}.{key}",
                    argument=key,
                )
        additional = schema.get("additionalProperties", True)
        if additional is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                key = str(unknown[0])
                _raise(
                    ProtocolErrorCode.UNKNOWN_ARGUMENT,
                    f"unknown argument: {key}",
                    f"{path}.{key}",
                    argument=key,
                )
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                if not isinstance(child_schema, Mapping):
                    _raise(
                        ProtocolErrorCode.INVALID_VALUE,
                        "property schema must be an object",
                        f"{path}.{key}",
                    )
                _validate_value(item, child_schema, f"{path}.{key}")

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, Mapping):
                _raise(
                    ProtocolErrorCode.INVALID_VALUE, "array items schema must be an object", path
                )
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def parse_json_arguments(arguments: str | Mapping[str, Any]) -> dict[str, Any]:
    """Decode a JSON object or copy an already decoded mapping.

    Mapping input is useful for direct Python callers and tests; model-facing
    callers should pass the original JSON string so malformed syntax is
    detected at this boundary.
    """

    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments, parse_constant=_reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolProtocolError(
                ProtocolErrorCode.INVALID_JSON,
                "tool arguments are not valid JSON",
                details={"reason": str(exc)},
            ) from exc
    elif isinstance(arguments, Mapping):
        decoded = dict(arguments)
    else:
        raise ToolProtocolError(
            ProtocolErrorCode.INVALID_JSON,
            "tool arguments must be a JSON object or JSON string",
            details={"actual": _json_type(arguments)},
        )
    if not isinstance(decoded, Mapping):
        raise ToolProtocolError(
            ProtocolErrorCode.ARGUMENTS_NOT_OBJECT,
            "tool arguments must decode to a JSON object",
            details={"actual": _json_type(decoded)},
        )
    try:
        json.dumps(decoded, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ToolProtocolError(
            ProtocolErrorCode.INVALID_JSON,
            "tool arguments must contain only JSON-compatible values",
            details={"reason": str(exc)},
        ) from exc
    return dict(decoded)


def validate_arguments(
    arguments: str | Mapping[str, Any], schema: Mapping[str, Any]
) -> dict[str, Any]:
    """Parse and validate arguments against a JSON schema subset."""

    if not isinstance(schema, Mapping):
        raise TypeError("tool schema must be a mapping")
    parsed = parse_json_arguments(arguments)
    _validate_value(parsed, schema, "$")
    return parsed


parse_arguments = parse_json_arguments
validate_json_arguments = validate_arguments


def ensure_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Detach and verify a schema is JSON serializable."""

    if not isinstance(schema, Mapping):
        raise TypeError("tool schema must be a mapping")
    detached = json.loads(json.dumps(schema, ensure_ascii=False, allow_nan=False))
    if not isinstance(detached, dict):
        raise TypeError("tool schema must serialize to an object")
    return detached


__all__ = [
    "ProtocolError",
    "ProtocolErrorCode",
    "ToolArgumentError",
    "ToolProtocolError",
    "ensure_json_schema",
    "parse_arguments",
    "parse_json_arguments",
    "validate_json_arguments",
    "validate_arguments",
]


ProtocolError = ToolProtocolError
ToolArgumentError = ToolProtocolError
