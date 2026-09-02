"""Fail-closed tool execution policy and approval ports.

The policy is deliberately small.  It decides whether a *validated* tool
call may proceed; it never executes a tool and it never talks to a terminal by
itself.  Approval is represented by a narrow port so the executor can be used
by both an interactive CLI and a non-interactive caller without importing any
UI code.

Only syntactic path safety belongs here.  The execution environment remains
the authority for resolving existing paths, symlinks, and the logical
workspace boundary.
"""

from __future__ import annotations

import inspect
import ntpath
import posixpath
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from coding_agent_neo.models import ToolCall
from coding_agent_neo.runtime import ToolExecutionContext
from coding_agent_neo.tools.schema import parse_json_arguments


class PolicyDecision(StrEnum):
    """The three decisions a policy can make before execution."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @property
    def action(self) -> PolicyDecision:
        return self

    @property
    def decision(self) -> PolicyDecision:
        return self


# These aliases make the vocabulary discoverable without introducing several
# subtly different decision types in downstream code.
PolicyAction = PolicyDecision
Decision = PolicyDecision
ApprovalDecision = PolicyDecision


class ApprovalMode(StrEnum):
    """Default policy modes understood by the built-in policy."""

    ASK = "ask"
    INTERACTIVE = "ask"
    AUTO = "auto"
    YOLO = "yolo"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyDecisionRecord:
    """Optional structured result useful to custom policy implementations.

    The executor accepts either this record, :class:`PolicyDecision`, or the
    equivalent string.  ``requested`` records the policy's initial answer and
    ``decision`` records the final answer after an approval port, if any.
    """

    decision: PolicyDecision | str
    reason: str = ""
    requested: PolicyDecision | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", PolicyDecision(self.decision))
        if self.requested is not None:
            object.__setattr__(self, "requested", PolicyDecision(self.requested))
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")

    @property
    def action(self) -> PolicyDecision:
        """Compatibility spelling for callers that call the field ``action``."""

        return self.decision


@runtime_checkable
class ApprovalPort(Protocol):
    """Narrow approval boundary consumed by :class:`ToolExecutor`.

    Implementations may expose ``interactive = False`` to make the executor
    reject ``ask`` without invoking the port.  A port is never required to
    read stdin; terminal input belongs in the caller/CLI adapter.
    """

    interactive: bool

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> bool:
        """Return ``True`` to approve one request and ``False`` to reject."""


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Information supplied to an approval adapter for one tool call."""

    tool_name: str
    arguments: Mapping[str, Any]
    context: ToolExecutionContext

    @property
    def correlation_id(self):
        return self.context.correlation_id

    @property
    def provider_tool_call_id(self):
        return self.context.provider_tool_call_id


@dataclass(slots=True)
class CallbackApprovalPort:
    """Adapt a Python callback to the approval protocol.

    The callback is intentionally injected.  It can have one of the common
    signatures ``(request)``, ``(tool_name, arguments)`` or
    ``(tool_name, arguments, context)``.  No callback is called for a
    non-interactive port.
    """

    callback: Callable[..., bool]
    interactive: bool = True

    def __post_init__(self) -> None:
        if not callable(self.callback):
            raise TypeError("callback must be callable")
        if not isinstance(self.interactive, bool):
            raise TypeError("interactive must be a boolean")

    def request_approval(self, request: ApprovalRequest) -> bool:
        if not self.interactive:
            return False
        return _invoke_approval_callable(self.callback, request)

    # Common names used by small callers and tests.
    approve = request_approval
    confirm = request_approval


@dataclass(slots=True)
class InteractiveApprovalPort(CallbackApprovalPort):
    """Explicitly interactive spelling for dependency assembly."""

    interactive: bool = True


@dataclass(slots=True)
class NonInteractiveApprovalPort:
    """Approval adapter that immediately rejects without reading stdin."""

    interactive: bool = False

    def request_approval(self, request: ApprovalRequest) -> bool:
        del request
        return False

    approve = request_approval
    confirm = request_approval


# Friendly aliases for dependency assembly code.
ApprovalProvider = ApprovalPort
InteractiveApproval = InteractiveApprovalPort
NonInteractiveApproval = NonInteractiveApprovalPort


@runtime_checkable
class ExecutionPolicyProtocol(Protocol):
    """Minimal policy interface accepted by the tool executor/runtime."""

    def decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | str,
        context: ToolExecutionContext | None = None,
    ) -> PolicyDecision | PolicyDecisionRecord | str:
        """Return ``allow``, ``ask`` or ``deny``; exceptions fail closed."""


@runtime_checkable
class Policy(ExecutionPolicyProtocol, Protocol):
    """Compatibility alias for the policy protocol."""


_READ_TOOLS = frozenset({"read_file", "list_files", "search"})
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})
_FILE_TOOLS = _READ_TOOLS | _WRITE_TOOLS
_BASH_TOOL = "bash"
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _safe_relative_path(value: Any, *, allow_empty: bool) -> bool:
    """Return whether a logical path is syntactically safe to hand off.

    This uses both POSIX and Windows path parsers regardless of the host OS so
    a configuration or model request cannot rely on the host platform to make
    a path appear safe.  It does not resolve filesystem links; that remains an
    Environment responsibility.
    """

    if not isinstance(value, str):
        return False
    if not value and not allow_empty:
        return False
    if "\x00" in value:
        return False
    # ``ntpath`` catches drive-relative ``C:foo`` as well as absolute drives;
    # the explicit regex keeps the intent clear for alternate path parsers.
    if _DRIVE_PREFIX.match(value) or ntpath.splitdrive(value)[0]:
        return False
    if posixpath.isabs(value) or ntpath.isabs(value):
        return False
    if value.startswith(("/", "\\")):
        return False
    # Treat either slash as a separator for safety, independent of host OS.
    if any(part == ".." for part in re.split(r"[/\\]", value)):
        return False
    return True


def _contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, Mapping):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_nul(item) for item in value)
    return False


def _coerce_arguments(arguments: Mapping[str, Any] | str) -> Mapping[str, Any] | None:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            return parse_json_arguments(arguments)
        except Exception:
            return None
    return None


class DefaultExecutionPolicy:
    """Default allow/ask/deny policy for the six built-in tools.

    Read-only workspace tools (``read_file``, ``list_files``, ``search``) are
    allowed after syntactic validation.  Side-effecting tools
    (``write_file``, ``edit_file``, ``bash``) are ``ask`` by default,
    ``auto``/``yolo`` allows them, and ``deny`` rejects them.  Unknown tools
    and malformed argument objects are denied.  The policy does not attempt
    to inspect shell command text: a command blacklist is not a reliable
    security boundary.
    """

    def __init__(
        self,
        approval_mode: ApprovalMode | str = ApprovalMode.ASK,
        *,
        mode: ApprovalMode | str | None = None,
        interactive: bool | None = None,
        auto: bool = False,
        yolo: bool = False,
        workspace: str | None = None,
    ) -> None:
        if mode is not None:
            approval_mode = mode
        if auto or yolo:
            approval_mode = ApprovalMode.AUTO
        if not isinstance(approval_mode, (str, ApprovalMode)):
            raise TypeError("approval_mode must be ask, auto, yolo, or deny")
        normalized = str(approval_mode).lower()
        if normalized == "interactive":
            normalized = ApprovalMode.ASK.value
        if normalized not in {item.value for item in ApprovalMode}:
            raise ValueError("approval_mode must be ask, auto, yolo, or deny")
        if interactive is not None and not isinstance(interactive, bool):
            raise TypeError("interactive must be a boolean or None")
        if workspace is not None and not isinstance(workspace, str):
            raise TypeError("workspace must be a string or None")
        self._mode_lock = threading.Lock()
        self._approval_mode = ApprovalMode(normalized)
        self.interactive = interactive
        self.workspace = workspace

    def decide(
        self,
        tool_name: str | ToolCall,
        arguments: Mapping[str, Any] | str | None = None,
        context: ToolExecutionContext | None = None,
    ) -> PolicyDecision:
        """Return a decision without causing an environment side effect."""

        del context
        if isinstance(tool_name, ToolCall):
            call = tool_name
            tool_name = call.name
            arguments = call.raw_arguments
        if not isinstance(tool_name, str) or not tool_name:
            return PolicyDecision.DENY
        parsed = _coerce_arguments(arguments if arguments is not None else "")
        if parsed is None or _contains_nul(parsed):
            return PolicyDecision.DENY

        if tool_name in _FILE_TOOLS:
            path = parsed.get("path", "")
            if not _safe_relative_path(path, allow_empty=tool_name in {"list_files", "search"}):
                return PolicyDecision.DENY
            if tool_name == "search" and (
                not isinstance(parsed.get("query"), str) or not parsed["query"]
            ):
                return PolicyDecision.DENY
            if tool_name == "write_file" and not isinstance(parsed.get("content"), str):
                return PolicyDecision.DENY
            if tool_name == "edit_file" and (
                not isinstance(parsed.get("old_text"), str)
                or not parsed["old_text"]
                or not isinstance(parsed.get("new_text"), str)
            ):
                return PolicyDecision.DENY
            if tool_name in _WRITE_TOOLS:
                return self._decide_side_effect()
            return PolicyDecision.ALLOW

        if tool_name == _BASH_TOOL:
            if not isinstance(parsed.get("command"), str) or not parsed["command"].strip():
                return PolicyDecision.DENY
            working_directory = parsed.get("working_directory")
            if working_directory is not None and not _safe_relative_path(
                working_directory, allow_empty=True
            ):
                return PolicyDecision.DENY
            return self._decide_side_effect()

        return PolicyDecision.DENY

    def _decide_side_effect(self) -> PolicyDecision:
        approval_mode = self.approval_mode
        if approval_mode in {ApprovalMode.AUTO, ApprovalMode.YOLO}:
            return PolicyDecision.ALLOW
        if approval_mode is ApprovalMode.DENY:
            return PolicyDecision.DENY
        if self.interactive is False:
            return PolicyDecision.DENY
        return PolicyDecision.ASK

    @property
    def approval_mode(self) -> ApprovalMode:
        with self._mode_lock:
            return self._approval_mode

    @approval_mode.setter
    def approval_mode(self, value: ApprovalMode | str) -> None:
        self.set_approval_mode(value)

    @property
    def mode(self) -> ApprovalMode:
        return self.approval_mode

    @mode.setter
    def mode(self, value: ApprovalMode | str) -> None:
        self.set_approval_mode(value)

    def set_approval_mode(self, value: ApprovalMode | str) -> ApprovalMode:
        """Atomically replace the mode used by subsequent policy decisions."""

        if not isinstance(value, (str, ApprovalMode)):
            raise TypeError("approval_mode must be ask, auto, or deny")
        normalized = str(value).lower()
        if normalized not in {"ask", "auto", "deny"}:
            raise ValueError("approval_mode must be ask, auto, or deny")
        selected = ApprovalMode(normalized)
        with self._mode_lock:
            self._approval_mode = selected
        return selected

    evaluate = decide
    check = decide


DefaultPolicy = DefaultExecutionPolicy


class ExecutionPolicy(DefaultExecutionPolicy):
    """Concrete spelling of the default policy for simple callers.

    ``DefaultExecutionPolicy`` remains the descriptive name used by the
    runtime assembly code.  Keeping this thin subclass also makes
    ``ExecutionPolicy()`` useful without weakening the protocol boundary.
    """

    pass


def _invoke_approval_callable(callback: Callable[..., bool], request: ApprovalRequest) -> Any:
    """Call common approval callback shapes without retrying callback bodies.

    Inspecting the signature before invocation avoids the unsafe pattern of
    retrying after a ``TypeError`` raised *inside* a user callback.
    """

    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        # An opaque callable is safest to treat as a one-argument request.
        return callback(request)

    parameters = list(signature.parameters.values())
    positional = [
        item
        for item in parameters
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only = {
        item.name: item for item in parameters if item.kind is inspect.Parameter.KEYWORD_ONLY
    }
    has_varargs = any(item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters)
    names = {item.name for item in parameters}
    if has_varargs or len(positional) >= 3:
        return callback(request.tool_name, request.arguments, request.context)
    if len(positional) == 2:
        if "context" in keyword_only:
            return callback(
                request.tool_name,
                request.arguments,
                context=request.context,
            )
        return callback(request.tool_name, request.arguments)
    if len(positional) == 1:
        parameter_name = positional[0].name
        if parameter_name in {"tool_name", "name", "tool"}:
            return callback(request.tool_name)
        return callback(request)
    if "tool_name" in names or "arguments" in names or "context" in names:
        kwargs: dict[str, Any] = {}
        if "tool_name" in names:
            kwargs["tool_name"] = request.tool_name
        if "arguments" in names:
            kwargs["arguments"] = request.arguments
        if "context" in names:
            kwargs["context"] = request.context
        return callback(**kwargs)
    return callback()


__all__ = [
    "ApprovalMode",
    "ApprovalDecision",
    "ApprovalPort",
    "ApprovalProvider",
    "ApprovalRequest",
    "CallbackApprovalPort",
    "Decision",
    "DefaultExecutionPolicy",
    "DefaultPolicy",
    "ExecutionPolicy",
    "ExecutionPolicyProtocol",
    "InteractiveApproval",
    "InteractiveApprovalPort",
    "NonInteractiveApproval",
    "NonInteractiveApprovalPort",
    "Policy",
    "PolicyAction",
    "PolicyDecision",
    "PolicyDecisionRecord",
]
