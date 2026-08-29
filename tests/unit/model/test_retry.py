"""Bounded retry and error classification tests."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from coding_agent_neo.model_client import (
    ModelClientError,
    ModelErrorCategory,
    ModelErrorCode,
    OpenAICompatibleModelClient,
    RetryPolicy,
)


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def create(self, **_: Any) -> Any:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


def _ok() -> dict[str, Any]:
    return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConnectionError(), ModelErrorCode.NETWORK),
        (TimeoutError(), ModelErrorCode.TIMEOUT),
        (type("RateLimitError", (Exception,), {"status_code": 429})(), ModelErrorCode.RATE_LIMIT),
        (type("ServerError", (Exception,), {"status_code": 500})(), ModelErrorCode.SERVER),
        (type("BadGateway", (Exception,), {"status_code": 502})(), ModelErrorCode.SERVER),
        (type("Unavailable", (Exception,), {"status_code": 503})(), ModelErrorCode.SERVER),
        (type("GatewayTimeout", (Exception,), {"status_code": 504})(), ModelErrorCode.SERVER),
    ],
)
def test_transient_network_rate_limit_and_selected_5xx_retry(
    error: Exception, code: ModelErrorCode
) -> None:
    fake = FakeClient([error, _ok()])
    delays: list[float] = []
    adapter = OpenAICompatibleModelClient(
        fake,
        model="test-model",
        max_retries=2,
        initial_delay_seconds=0.25,
        max_delay_seconds=1,
        sleep=delays.append,
    )

    result = adapter.complete([], [])

    assert result.text == "ok"
    assert fake.chat.completions.calls == 2
    assert delays == [0.25]
    assert code in {
        ModelErrorCode.NETWORK,
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.RATE_LIMIT,
        ModelErrorCode.SERVER,
    }


def test_retry_count_and_exponential_delays_are_bounded() -> None:
    errors = [
        type("ServerError", (Exception,), {"status_code": 500})(),
        type("ServerError", (Exception,), {"status_code": 500})(),
        type("ServerError", (Exception,), {"status_code": 500})(),
        type("ServerError", (Exception,), {"status_code": 500})(),
    ]
    fake = FakeClient(errors)
    delays: list[float] = []
    adapter = OpenAICompatibleModelClient(
        fake,
        model="test-model",
        max_retries=3,
        initial_delay_seconds=0.1,
        max_delay_seconds=0.15,
        sleep=delays.append,
    )

    with pytest.raises(ModelClientError) as raised:
        adapter.complete([], [])

    assert raised.value.category is ModelErrorCategory.RETRYABLE
    assert raised.value.code is ModelErrorCode.SERVER
    assert raised.value.attempts == 4
    assert fake.chat.completions.calls == 4
    assert delays == [0.1, 0.15, 0.15]


@pytest.mark.parametrize(
    "error",
    [
        type("AuthenticationError", (Exception,), {"status_code": 401})(),
        type("PermissionDeniedError", (Exception,), {"status_code": 403})(),
        type("NotFoundError", (Exception,), {"status_code": 404})(),
        type("InvalidRequestError", (Exception,), {"status_code": 400})(),
        type("UnselectedServerError", (Exception,), {"status_code": 501})(),
    ],
)
def test_auth_permission_model_and_configuration_errors_do_not_retry(error: Exception) -> None:
    fake = FakeClient([error, _ok()])
    delays: list[float] = []
    adapter = OpenAICompatibleModelClient(
        fake, model="test-model", max_retries=4, sleep=delays.append
    )

    with pytest.raises(ModelClientError) as raised:
        adapter.complete([], [])

    assert raised.value.category is ModelErrorCategory.FATAL
    assert fake.chat.completions.calls == 1
    assert delays == []


def test_model_error_is_safe_and_preserves_no_provider_exception() -> None:
    error = ModelClientError(
        ModelErrorCategory.FATAL,
        ModelErrorCode.INVALID_REQUEST,
        'api_key="SECRET" Authorization: Bearer TOPSECRET',
        status_code=400,
    )
    assert "SECRET" not in str(error)
    assert "TOPSECRET" not in str(error)
    assert "headers" not in str(error).casefold()
    assert error.as_dict()["status_code"] == 400


def test_retry_policy_rejects_unbounded_or_invalid_values() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=-1)
    with pytest.raises(ValueError):
        RetryPolicy(initial_delay_seconds=2, max_delay_seconds=1)
    assert (
        RetryPolicy(max_retries=2, initial_delay_seconds=0.1, max_delay_seconds=0.3).max_attempts
        == 3
    )


def test_retry_logging_contains_only_stable_classification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class NetworkFailure(Exception):
        def __str__(self) -> str:
            return "Authorization: Bearer TOPSECRET"

    fake = FakeClient([NetworkFailure(), _ok()])
    with caplog.at_level(logging.INFO):
        OpenAICompatibleModelClient(
            fake, model="test-model", max_retries=1, sleep=lambda _: None
        ).complete([], [])
    assert "TOPSECRET" not in caplog.text
    assert "retryable" in caplog.text
