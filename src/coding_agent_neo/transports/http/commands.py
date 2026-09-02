"""Decode the public Agent commands at the HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coding_agent_neo.backend import (
    AgentCommand,
    ApprovalResponse,
    CloseSession,
    Interrupt,
    SetApprovalMode,
    SubmitTask,
)


class CommandDecodeError(ValueError):
    """The JSON value does not represent one valid public Agent command."""


_COMMAND_FIELDS = {
    "SubmitTask": (frozenset({"type", "text"}), frozenset()),
    "ApprovalResponse": (frozenset({"type", "request_id", "approved"}), frozenset()),
    "SetApprovalMode": (frozenset({"type", "mode"}), frozenset()),
    "Interrupt": (frozenset({"type"}), frozenset({"reason"})),
    "CloseSession": (frozenset({"type"}), frozenset({"reason"})),
}


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CommandDecodeError("command must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise CommandDecodeError("command fields must be strings")
    return value


def _command_type(value: Mapping[str, Any]) -> str:
    command_type = value.get("type")
    if not isinstance(command_type, str) or command_type not in _COMMAND_FIELDS:
        raise CommandDecodeError("command type is not supported")
    fields = frozenset(value)
    required, optional = _COMMAND_FIELDS[command_type]
    if not required <= fields or not fields <= required | optional:
        raise CommandDecodeError("command fields are invalid")
    return command_type


def decode_command(value: Any) -> AgentCommand:
    """Decode an already parsed JSON value into an immutable Agent command.

    Unknown fields are rejected so a caller cannot silently believe the
    transport accepted semantics that the Agent port does not define.  The
    public constructors retain their own validation and therefore remain the
    single source of truth for field types and non-empty values.
    """

    command = _mapping(value)
    command_type = _command_type(command)
    try:
        if command_type == "SubmitTask":
            return SubmitTask(command["text"])
        if command_type == "ApprovalResponse":
            return ApprovalResponse(command["request_id"], command["approved"])
        if command_type == "SetApprovalMode":
            return SetApprovalMode(command["mode"])
        if command_type == "Interrupt":
            return Interrupt(command.get("reason", "interrupted"))
        return CloseSession(command.get("reason", "session_closed"))
    except (TypeError, ValueError) as error:
        raise CommandDecodeError("command fields are invalid") from error


class CommandDecoder:
    """Small object-oriented facade useful for dependency injection/tests."""

    @staticmethod
    def decode(value: Any) -> AgentCommand:
        return decode_command(value)


__all__ = ["CommandDecodeError", "CommandDecoder", "decode_command"]
