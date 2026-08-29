"""Offline request and response normalization tests for the model boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from coding_agent_neo.model_client import (
    ModelErrorCategory,
    OpenAICompatibleModelClient,
)


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


def _response(
    *,
    content: Any = "done",
    tool_calls: Any = None,
    usage: Any = None,
    finish_reason: Any = "stop",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    response: dict[str, Any] = {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]
    }
    if usage is not None:
        response["usage"] = usage
    return response


def _call(call_id: Any, name: Any, arguments: Any) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_request_passes_messages_active_tools_and_parameters() -> None:
    fake = FakeClient([_response()])
    messages = [{"role": "user", "content": "inspect"}]
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    adapter = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0)

    result = adapter.complete(messages, tools, {"temperature": 0, "max_tokens": 64})

    request = fake.chat.completions.requests[0]
    assert request["messages"] == messages
    assert request["tools"] == tools
    assert request["model"] == "test-model"
    assert request["temperature"] == 0
    assert request["max_tokens"] == 64
    assert result.text == "done"
    assert result.tool_calls == ()
    assert result.finish_reason == "stop"


def test_httpx_mock_transport_uses_official_openai_boundary_without_network() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "offline"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    adapter = OpenAICompatibleModelClient(
        model="test-model",
        api_key="offline-key",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    result = adapter.complete([{"role": "user", "content": "hello"}], [])

    assert result.text == "offline"
    assert len(seen) == 1


def test_zero_single_and_multiple_tool_calls_keep_provider_order() -> None:
    calls = [
        _call("provider/2", "search", '{"query":"needle"}'),
        _call("provider/1", "read_file", '{"path":"a.py"}'),
        _call("provider/3", "edit_file", '{"path":"a.py","old_text":"x","new_text":"y"}'),
    ]
    fake = FakeClient(
        [
            _response(
                tool_calls=calls,
                usage={"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16},
                finish_reason="tool_calls",
            )
        ]
    )
    result = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0).complete([], [])

    assert [call.provider_tool_call_id for call in result.tool_calls] == [
        "provider/2",
        "provider/1",
        "provider/3",
    ]
    assert [call.name for call in result.tool_calls] == ["search", "read_file", "edit_file"]
    assert [call.raw_arguments for call in result.tool_calls] == [
        '{"query":"needle"}',
        '{"path":"a.py"}',
        '{"path":"a.py","old_text":"x","new_text":"y"}',
    ]
    assert all(call.arguments_valid for call in result.tool_calls)
    assert result.usage is not None
    assert result.usage.input_tokens == 9
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 16
    assert result.finish_reason == "tool_calls"


def test_provider_ids_and_invalid_arguments_become_diagnostics() -> None:
    calls = [
        _call(None, "read_file", '{"path":"a.py"}'),
        _call("same", "search", "not-json"),
        _call("same", "search", "[]"),
        _call("bad\x00id", "search", '{"query":"x"}'),
    ]
    fake = FakeClient([_response(tool_calls=calls)])
    result = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0).complete([], [])

    assert len(result.tool_calls) == 4
    assert result.tool_calls[0].provider_tool_call_id is None
    assert "missing_tool_call_id" in result.tool_calls[0].diagnostics
    assert result.tool_calls[1].arguments_valid is False
    assert "invalid_arguments" in result.tool_calls[1].diagnostics
    assert "duplicate_tool_call_id" in result.tool_calls[2].diagnostics
    assert "arguments_not_object" in result.tool_calls[2].diagnostics
    assert result.tool_calls[3].provider_tool_call_id is None
    assert "invalid_tool_call_id" in result.tool_calls[3].diagnostics
    assert "missing_tool_call_id" in result.diagnostics
    assert "duplicate_tool_call_id" in result.diagnostics
    assert "invalid_arguments" in result.diagnostics


def test_missing_response_and_structured_content_do_not_raise() -> None:
    fake = FakeClient([None])
    result = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0).complete([], [])
    assert result.text == ""
    assert result.tool_calls == ()
    assert "response_missing" in result.diagnostics

    fake = FakeClient([_response(content=[{"type": "text", "text": "part 1"}, {"text": "part 2"}])])
    result = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0).complete([], [])
    assert result.text == "part 1part 2"


def test_invalid_provider_shapes_are_reported_without_unknown_stringification() -> None:
    @dataclass
    class Hostile:
        def __str__(self) -> str:
            raise AssertionError("provider object must not be stringified")

        def __repr__(self) -> str:
            raise AssertionError("provider object must not be represented")

    fake = FakeClient(
        [
            _response(
                tool_calls=[_call(Hostile(), Hostile(), Hostile())],
                usage={"prompt_tokens": "bad"},
                finish_reason=Hostile(),
            )
        ]
    )
    result = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0).complete([], [])
    call = result.tool_calls[0]
    assert call.provider_tool_call_id is None
    assert call.name is None
    assert call.raw_arguments == ""
    assert "invalid_tool_call_id" in call.diagnostics
    assert "invalid_tool_name" in call.diagnostics
    assert "invalid_arguments" in call.diagnostics
    assert "invalid_usage" in result.diagnostics
    assert "invalid_finish_reason" in result.diagnostics


def test_context_overflow_is_not_hidden_as_a_retryable_error() -> None:
    class OverflowError(Exception):
        status_code = 400
        body = {"error": {"code": "context_length_exceeded"}}

    fake = FakeClient([OverflowError()])
    adapter = OpenAICompatibleModelClient(
        fake, model="test-model", max_retries=4, sleep=lambda _: None
    )

    try:
        adapter.complete([], [])
    except Exception as error:
        assert error.category is ModelErrorCategory.CONTEXT_OVERFLOW
        assert error.retryable is False
        assert error.attempts == 1
    else:
        raise AssertionError("context overflow should be raised")
