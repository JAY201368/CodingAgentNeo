"""Tool-result truncation and projection tests."""

from __future__ import annotations

import json

from coding_agent_neo.models import ToolResult, ToolResultStatus
from coding_agent_neo.tools import (
    OutputProjector,
    ToolResultProjection,
    head_tail_truncate,
    project_for_model,
    project_for_persistence,
)


def test_head_tail_projection_reports_original_length_and_preserves_ids() -> None:
    result = ToolResult(
        correlation_id="correlation-1",
        provider_tool_call_id="provider-1",
        status=ToolResultStatus.ERROR,
        text="HEAD-" + ("0123456789" * 8) + "-TAIL",
        metadata={"reason": "command_error"},
        duration_seconds=1.25,
        exit_code=7,
        path="src/a.py",
    )
    projected = project_for_model(result, 70)
    assert isinstance(projected, ToolResultProjection)
    assert projected.truncated is True
    assert projected.original_length == len(result.text)
    assert projected.text.startswith("HEAD")
    assert projected.text.endswith("TAIL")
    assert "original length" in projected.text
    assert projected.correlation_id == "correlation-1"
    assert projected.provider_tool_call_id == "provider-1"
    assert projected.exit_code == 7
    assert projected.path == "src/a.py"
    assert len(projected.text) <= 70


def test_model_and_persistence_views_share_the_same_result_facts() -> None:
    result = ToolResult(
        correlation_id="correlation-2",
        provider_tool_call_id="provider-2",
        text="0123456789abcdef",
        metadata={"source": "fake"},
        truncated=False,
    )
    model = project_for_model(result, 10)
    persisted = project_for_persistence(result, 10)
    assert model.status is persisted.status is ToolResultStatus.SUCCESS
    assert model.correlation_id == persisted.correlation_id == "correlation-2"
    assert model.truncated is persisted.truncated is True
    assert model.original_length == persisted.original_length == len(result.text)
    assert model.metadata["source"] == persisted.metadata["source"] == "fake"
    assert len(model.text) <= 10
    assert len(persisted.text) <= 10
    assert json.loads(json.dumps(model))["status"] == "success"


def test_projection_does_not_retruncate_an_already_bounded_environment_result() -> None:
    result = ToolResult(
        correlation_id="correlation-3",
        text="head ... tail",
        truncated=True,
        original_length=1000,
    )
    projected = OutputProjector(model_limit=100).for_model(result)
    assert projected.text == result.text
    assert projected.truncated is True
    assert projected.original_length == 1000


def test_zero_bound_is_still_bounded_and_direct_truncation_is_json_friendly() -> None:
    sliced = head_tail_truncate("hello", 0)
    assert sliced.text == ""
    assert sliced.truncated is True
    assert sliced.original_length == 5
    projected = project_for_persistence(ToolResult(correlation_id="correlation-4", text="hello"), 0)
    assert json.dumps(projected.to_dict())
