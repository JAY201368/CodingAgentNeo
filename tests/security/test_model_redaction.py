"""Credential and unknown-provider-object redaction tests."""

from __future__ import annotations

from typing import Any

from coding_agent_neo.model_client import OpenAICompatibleModelClient


class FakeCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response

    def create(self, **_: Any) -> Any:
        return self.response


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(response)


def test_response_text_and_tool_arguments_redact_credentials() -> None:
    sentinel = "MODEL_CLIENT_SECRET_SENTINEL"
    response = {
        "choices": [
            {
                "message": {
                    "content": f'api_key="{sentinel}" Authorization: Bearer {sentinel}',
                    "tool_calls": [
                        {
                            "id": "call-safe",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"a.py","api_key":"' + sentinel + '"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    client = OpenAICompatibleModelClient(FakeClient(response), model="test-model", max_retries=0)

    normalized = client.complete([], [])
    rendered = str(normalized.to_dict())

    assert sentinel not in rendered
    assert "<redacted>" in rendered


def test_provider_error_body_headers_are_not_retained_or_stringified() -> None:
    sentinel = "AUTH_HEADER_SECRET_SENTINEL"

    class HostileError(Exception):
        status_code = 500
        body = {
            "error": {
                "message": sentinel,
                "headers": {"Authorization": f"Bearer {sentinel}"},
            }
        }

        def __str__(self) -> str:
            raise AssertionError("provider exception must not be stringified")

        def __repr__(self) -> str:
            raise AssertionError("provider exception must not be represented")

    class FailingCompletions:
        def create(self, **_: Any) -> Any:
            raise HostileError()

    fake = type("Fake", (), {})()
    fake.chat = type("Chat", (), {})()
    fake.chat.completions = FailingCompletions()
    client = OpenAICompatibleModelClient(fake, model="test-model", max_retries=0)

    try:
        client.complete([], [])
    except Exception as error:
        rendered = str(error) + str(error.as_dict())
        assert sentinel not in rendered
        assert "Authorization" not in rendered
        assert "headers" not in rendered.casefold()
    else:
        raise AssertionError("failing transport should produce a safe model error")
