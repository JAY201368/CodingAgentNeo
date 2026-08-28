"""Backend-neutral protocol for tools.

The tool package is intentionally a thin boundary around an
``ExecutionEnvironment``.  A tool receives all execution capabilities through
``ToolExecutionContext`` and therefore has no reason to know which environment
implementation is in use.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from coding_agent_neo.models import ToolResult
from coding_agent_neo.runtime import ToolExecutionContext

JSONSchema = Mapping[str, Any]
ToolSchema = JSONSchema


@runtime_checkable
class Tool(Protocol):
    """The minimum public contract implemented by every tool."""

    name: str
    description: str
    parameters: JSONSchema

    @property
    def schema(self) -> JSONSchema:
        """Return the descriptive, JSON-compatible tool schema."""

    def validate(self, arguments: str | Mapping[str, Any]) -> Mapping[str, Any]:
        """Parse and validate a JSON argument object."""

    def execute(
        self, arguments: str | Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        """Execute the tool through the environment in ``context``."""


# Both spellings are useful to callers and keep the protocol discoverable.
ToolProtocol = Tool


__all__ = ["JSONSchema", "Tool", "ToolProtocol", "ToolSchema"]
