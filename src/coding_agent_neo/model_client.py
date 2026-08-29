"""OpenAI-compatible Chat Completions transport and normalization boundary.

The model client is deliberately small: callers provide standard Chat
Completions messages, the active tool schemas, and request parameters; this
module performs a synchronous request, bounded retry, and conversion to
backend-neutral DTOs.  It never executes tools and it never creates an
internal correlation ID.  The Agent Loop owns those responsibilities.

The official :mod:`openai` client is used when a client is not injected.  Its
internal retries are disabled so the retry count visible to this project is
the complete retry policy.  A fake client or an ``httpx`` transport can be
injected for fully offline tests.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from coding_agent_neo.models import (
    NormalizedAssistantResponse,
    NormalizedToolCall,
    NormalizedUsage,
    ProviderToolCallId,
    _redact_model_text,
)

try:  # pragma: no cover - import availability is covered by packaging checks.
    from openai import (
        APIConnectionError,
        APIError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        OpenAI,
        PermissionDeniedError,
        RateLimitError,
    )
except ImportError:  # pragma: no cover - the project declares openai as a dependency.
    OpenAI = None  # type: ignore[assignment,misc]
    APIConnectionError = APIError = APITimeoutError = APIStatusError = ()  # type: ignore[assignment]
    AuthenticationError = BadRequestError = InternalServerError = ()  # type: ignore[assignment]
    NotFoundError = PermissionDeniedError = RateLimitError = ()  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
_MISSING = object()
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class ModelErrorCategory(StrEnum):
    """Stable top-level categories consumed by the future Agent Loop."""

    RETRYABLE = "retryable"
    FATAL = "fatal"
    CONTEXT_OVERFLOW = "context_overflow"


ErrorCategory = ModelErrorCategory
ModelErrorKind = ModelErrorCategory


class ModelErrorCode(StrEnum):
    """Safe, provider-neutral error codes exposed by the transport boundary."""

    NETWORK = "network_error"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER = "server_error"
    AUTHENTICATION = "authentication_error"
    PERMISSION = "permission_error"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_OVERFLOW = "context_overflow"
    CONFIGURATION = "configuration_error"
    RESPONSE = "response_error"
    RETRY_SLEEP = "retry_sleep_error"
    UNKNOWN = "unknown_error"


class ModelClientError(RuntimeError):
    """A safe model boundary error with no provider message or headers.

    Only stable classification data is retained.  In particular, the
    exception never stores the original OpenAI exception, request, response,
    body, headers, or API key.  This keeps ``str(error)`` and ``as_dict()``
    suitable for events and logs.
    """

    def __init__(
        self,
        category: ModelErrorCategory | str,
        code: ModelErrorCode | str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        try:
            normalized_category = ModelErrorCategory(category)
        except (TypeError, ValueError):
            normalized_category = ModelErrorCategory.FATAL
        try:
            normalized_code = ModelErrorCode(code)
        except (TypeError, ValueError):
            normalized_code = ModelErrorCode.UNKNOWN
        if status_code is not None and (
            isinstance(status_code, bool) or not isinstance(status_code, int)
        ):
            status_code = None
        if attempts < 1:
            attempts = 1
        safe_message = _redact_model_text(message) if isinstance(message, str) else None
        if not safe_message:
            safe_message = _default_error_message(normalized_code)
        self.category = normalized_category
        self.kind = normalized_category
        self.code = normalized_code
        self.status_code = status_code
        self.attempts = attempts
        self.message = safe_message
        super().__init__(self._render())

    def _render(self) -> str:
        status = "" if self.status_code is None else f" status={self.status_code}"
        return f"model {self.category.value}: {self.code.value}{status}"

    @property
    def retryable(self) -> bool:
        """Whether a caller may retry this operation."""

        return self.category is ModelErrorCategory.RETRYABLE

    @property
    def is_retryable(self) -> bool:
        return self.retryable

    @property
    def context_overflow(self) -> bool:
        """Whether the caller should perform its one compaction attempt."""

        return self.category is ModelErrorCategory.CONTEXT_OVERFLOW

    @property
    def is_context_overflow(self) -> bool:
        return self.context_overflow

    @property
    def error_category(self) -> str:
        return self.category.value

    @property
    def error_code(self) -> str:
        return self.code.value

    @property
    def reason(self) -> str:
        """Stable human-readable category message without provider detail."""

        return self.message

    @property
    def details(self) -> Mapping[str, Any]:
        """Safe structured fields retained for protocol/event boundaries."""

        return self.as_dict()

    def as_dict(self) -> dict[str, Any]:
        """Return a safe diagnostic suitable for an event payload."""

        return {
            "category": self.category.value,
            "code": self.code.value,
            "message": self.message,
            "status_code": self.status_code,
            "attempts": self.attempts,
        }

    to_dict = as_dict


ModelError = ModelClientError
ModelRequestError = ModelClientError


def _default_error_message(code: ModelErrorCode) -> str:
    return {
        ModelErrorCode.NETWORK: "model network request failed",
        ModelErrorCode.TIMEOUT: "model request timed out",
        ModelErrorCode.RATE_LIMIT: "model request was rate limited",
        ModelErrorCode.SERVER: "model service returned a retryable server error",
        ModelErrorCode.AUTHENTICATION: "model authentication failed",
        ModelErrorCode.PERMISSION: "model request was not permitted",
        ModelErrorCode.MODEL_NOT_FOUND: "requested model was not found",
        ModelErrorCode.INVALID_REQUEST: "model request was invalid",
        ModelErrorCode.CONTEXT_OVERFLOW: "model context window was exceeded",
        ModelErrorCode.CONFIGURATION: "model client configuration is invalid",
        ModelErrorCode.RESPONSE: "model response was unavailable or invalid",
        ModelErrorCode.RETRY_SLEEP: "model retry delay could not be applied",
        ModelErrorCode.UNKNOWN: "model request failed",
    }[code]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Deterministic bounded exponential backoff settings."""

    max_retries: int = 2
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise TypeError("max_retries must be a non-negative integer")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        for name in ("initial_delay_seconds", "max_delay_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def delay_for_retry(self, retry_index: int) -> float:
        """Return the delay before retry number ``retry_index`` (zero-based)."""

        if retry_index < 0:
            raise ValueError("retry_index must be non-negative")
        return min(
            self.max_delay_seconds,
            self.initial_delay_seconds * (2**retry_index),
        )


RetryConfig = RetryPolicy


@runtime_checkable
class ModelClient(Protocol):
    """The synchronous model interface used by the future Agent Loop."""

    def complete(
        self,
        messages: Iterable[Mapping[str, Any]],
        tools: Iterable[Mapping[str, Any]] | None,
        parameters: Mapping[str, Any] | None = None,
    ) -> NormalizedAssistantResponse:
        """Complete one Chat Completions request."""


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    """Read a known SDK field without stringifying an untrusted object."""

    if isinstance(value, Mapping):
        try:
            return value.get(name, default)
        except Exception:
            return default
    try:
        return getattr(value, name)
    except Exception:
        return default


def _sequence(value: Any) -> list[Any] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return None


def _append_diagnostic(diagnostics: list[str], code: str) -> None:
    if code not in diagnostics:
        diagnostics.append(code)


def _safe_provider_id(value: Any) -> tuple[ProviderToolCallId | None, str | None]:
    if value is _MISSING or value is None:
        return None, "missing_tool_call_id"
    if not isinstance(value, str) or not value or "\x00" in value:
        return None, "invalid_tool_call_id"
    safe_value = _redact_model_text(value)
    try:
        return ProviderToolCallId(safe_value), None
    except (TypeError, ValueError):
        return None, "invalid_tool_call_id"


def _safe_name(value: Any) -> tuple[str | None, str | None]:
    if value is _MISSING or value is None:
        return None, "missing_tool_name"
    if not isinstance(value, str) or not value:
        return None, "invalid_tool_name"
    return _redact_model_text(value), None


def _json_constant(value: str) -> Any:
    raise ValueError(value)


def _arguments_status(value: Any) -> tuple[str, bool, str | None]:
    if value is _MISSING or value is None:
        return "", False, "missing_arguments"
    if not isinstance(value, str):
        return "", False, "invalid_arguments"
    safe_value = _redact_model_text(value)
    try:
        import json

        parsed = json.loads(safe_value, parse_constant=_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return safe_value, False, "invalid_arguments"
    if not isinstance(parsed, Mapping):
        return safe_value, False, "arguments_not_object"
    return safe_value, True, None


def _normalize_tool_call(
    value: Any,
    seen_ids: set[str],
    response_diagnostics: list[str],
) -> NormalizedToolCall:
    call_diagnostics: list[str] = []
    provider_id, provider_diagnostic = _safe_provider_id(_field(value, "id"))
    if provider_diagnostic is not None:
        _append_diagnostic(call_diagnostics, provider_diagnostic)
        _append_diagnostic(response_diagnostics, provider_diagnostic)
    elif provider_id is not None:
        provider_key = str(provider_id)
        if provider_key in seen_ids:
            _append_diagnostic(call_diagnostics, "duplicate_tool_call_id")
            _append_diagnostic(response_diagnostics, "duplicate_tool_call_id")
        seen_ids.add(provider_key)

    function = _field(value, "function")
    if function is _MISSING or function is None:
        function = {}
        _append_diagnostic(call_diagnostics, "missing_function")
        _append_diagnostic(response_diagnostics, "missing_function")
    name, name_diagnostic = _safe_name(_field(function, "name"))
    if name_diagnostic is not None:
        _append_diagnostic(call_diagnostics, name_diagnostic)
        _append_diagnostic(response_diagnostics, name_diagnostic)
    raw_arguments, arguments_valid, arguments_diagnostic = _arguments_status(
        _field(function, "arguments")
    )
    if arguments_diagnostic is not None:
        _append_diagnostic(call_diagnostics, arguments_diagnostic)
        _append_diagnostic(response_diagnostics, arguments_diagnostic)

    return NormalizedToolCall(
        provider_tool_call_id=provider_id,
        name=name,
        raw_arguments=raw_arguments,
        arguments_valid=arguments_valid,
        diagnostics=tuple(call_diagnostics),
    )


def _safe_content(value: Any, diagnostics: list[str]) -> str:
    if value is _MISSING or value is None:
        return ""
    if isinstance(value, str):
        return _redact_model_text(value)
    if isinstance(value, Mapping):
        text = value.get("text", _MISSING)
        if isinstance(text, str):
            return _redact_model_text(text)
        _append_diagnostic(diagnostics, "invalid_content")
        return ""
    parts = _sequence(value)
    if parts is not None:
        rendered: list[str] = []
        for part in parts:
            text = _field(part, "text")
            if isinstance(text, str):
                rendered.append(_redact_model_text(text))
        if rendered:
            return "".join(rendered)
        _append_diagnostic(diagnostics, "invalid_content")
        return ""
    text = _field(value, "text")
    if isinstance(text, str):
        return _redact_model_text(text)
    _append_diagnostic(diagnostics, "invalid_content")
    return ""


def _safe_counter(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalize_usage(value: Any, diagnostics: list[str]) -> NormalizedUsage | None:
    if value is _MISSING or value is None:
        return None
    input_value = _field(value, "input_tokens")
    if input_value is _MISSING:
        input_value = _field(value, "prompt_tokens")
    output_value = _field(value, "output_tokens")
    if output_value is _MISSING:
        output_value = _field(value, "completion_tokens")
    total_value = _field(value, "total_tokens")
    raw_values = (input_value, output_value, total_value)
    counters = tuple(None if item is _MISSING else _safe_counter(item) for item in raw_values)
    if any(item is not _MISSING and counter is None for item, counter in zip(raw_values, counters)):
        _append_diagnostic(diagnostics, "invalid_usage")
    if not any(counter is not None for counter in counters):
        return None
    return NormalizedUsage(
        input_tokens=counters[0],
        output_tokens=counters[1],
        total_tokens=counters[2],
    )


def _normalize_response(response: Any) -> NormalizedAssistantResponse:
    diagnostics: list[str] = []
    usage = _normalize_usage(_field(response, "usage"), diagnostics)
    choices = _sequence(_field(response, "choices"))
    if choices is None or not choices:
        _append_diagnostic(diagnostics, "response_missing")
        return NormalizedAssistantResponse(usage=usage, diagnostics=tuple(diagnostics))

    choice = choices[0]
    message = _field(choice, "message")
    if message is _MISSING or message is None:
        _append_diagnostic(diagnostics, "message_missing")
        message = {}
    text = _safe_content(_field(message, "content"), diagnostics)
    raw_tool_calls = _field(message, "tool_calls")
    tool_calls: list[NormalizedToolCall] = []
    if raw_tool_calls is not _MISSING and raw_tool_calls is not None:
        provider_calls = _sequence(raw_tool_calls)
        if provider_calls is None:
            _append_diagnostic(diagnostics, "invalid_tool_calls")
        else:
            seen_ids: set[str] = set()
            for provider_call in provider_calls:
                tool_calls.append(_normalize_tool_call(provider_call, seen_ids, diagnostics))

    finish_reason = _field(choice, "finish_reason")
    if finish_reason is not _MISSING and finish_reason is not None:
        if not isinstance(finish_reason, str):
            _append_diagnostic(diagnostics, "invalid_finish_reason")
            finish_reason = None
        else:
            finish_reason = _redact_model_text(finish_reason)

    return NormalizedAssistantResponse(
        text=text,
        tool_calls=tuple(tool_calls),
        usage=usage,
        finish_reason=finish_reason,
        diagnostics=tuple(diagnostics),
    )


def _status_code(value: Any) -> int | None:
    status = _field(value, "status_code")
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    response = _field(value, "response")
    status = _field(response, "status_code")
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def _contains_context_signal(value: Any, *, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        lowered = value.casefold()
        return any(
            marker in lowered
            for marker in (
                "context_length_exceeded",
                "context window",
                "maximum context",
                "too many tokens",
                "prompt is too long",
                "input is too long",
                "token limit",
                "input_too_large",
            )
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _contains_context_signal(key, depth=depth + 1):
                return True
            if _contains_context_signal(item, depth=depth + 1):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_context_signal(item, depth=depth + 1) for item in value)
    return False


def _has_context_signal(error: BaseException) -> bool:
    try:
        code = _field(error, "code")
        if isinstance(code, str) and _contains_context_signal(code):
            return True
        body = _field(error, "body")
        if body is not _MISSING and _contains_context_signal(body):
            return True
    except Exception:
        return False
    return False


def _classify_exception(error: BaseException) -> ModelClientError:
    """Map SDK/transport failures to safe stable categories."""

    status = _status_code(error)
    type_name = type(error).__name__.casefold()
    if _has_context_signal(error):
        return ModelClientError(
            ModelErrorCategory.CONTEXT_OVERFLOW,
            ModelErrorCode.CONTEXT_OVERFLOW,
            status_code=status,
        )

    authentication_types = (AuthenticationError,) if isinstance(AuthenticationError, type) else ()
    permission_types = (PermissionDeniedError,) if isinstance(PermissionDeniedError, type) else ()
    not_found_types = (NotFoundError,) if isinstance(NotFoundError, type) else ()
    rate_limit_types = (RateLimitError,) if isinstance(RateLimitError, type) else ()
    connection_types = tuple(
        item for item in (APIConnectionError, APITimeoutError) if isinstance(item, type)
    )
    if authentication_types and isinstance(error, authentication_types):
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.AUTHENTICATION,
            status_code=status or 401,
        )
    if permission_types and isinstance(error, permission_types):
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.PERMISSION,
            status_code=status or 403,
        )
    if not_found_types and isinstance(error, not_found_types):
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.MODEL_NOT_FOUND,
            status_code=status or 404,
        )
    if rate_limit_types and isinstance(error, rate_limit_types):
        return ModelClientError(
            ModelErrorCategory.RETRYABLE,
            ModelErrorCode.RATE_LIMIT,
            status_code=status or 429,
        )
    if connection_types and isinstance(error, connection_types):
        code = ModelErrorCode.TIMEOUT if "timeout" in type_name else ModelErrorCode.NETWORK
        return ModelClientError(ModelErrorCategory.RETRYABLE, code, status_code=status)

    if status == 429 or "ratelimit" in type_name or "rate_limit" in type_name:
        return ModelClientError(
            ModelErrorCategory.RETRYABLE,
            ModelErrorCode.RATE_LIMIT,
            status_code=429 if status is None else status,
        )
    if status in _RETRYABLE_STATUS_CODES:
        code = ModelErrorCode.RATE_LIMIT if status == 429 else ModelErrorCode.SERVER
        return ModelClientError(ModelErrorCategory.RETRYABLE, code, status_code=status)
    if status is not None and status >= 500:
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.SERVER,
            status_code=status,
        )
    if status in {401, 403, 404}:
        code = {
            401: ModelErrorCode.AUTHENTICATION,
            403: ModelErrorCode.PERMISSION,
            404: ModelErrorCode.MODEL_NOT_FOUND,
        }[status]
        return ModelClientError(ModelErrorCategory.FATAL, code, status_code=status)
    if status == 413:
        return ModelClientError(
            ModelErrorCategory.CONTEXT_OVERFLOW,
            ModelErrorCode.CONTEXT_OVERFLOW,
            status_code=status,
        )

    if "authentication" in type_name or type_name in {"unauthorizederror", "autherror"}:
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.AUTHENTICATION,
            status_code=status or 401,
        )
    if "permission" in type_name or type_name in {"forbiddenerror", "permissionerror"}:
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.PERMISSION,
            status_code=status or 403,
        )
    if "notfound" in type_name or "modelnotfound" in type_name:
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.MODEL_NOT_FOUND,
            status_code=status or 404,
        )
    if isinstance(error, (TimeoutError,)) or "timeout" in type_name:
        return ModelClientError(
            ModelErrorCategory.RETRYABLE,
            ModelErrorCode.TIMEOUT,
            status_code=status,
        )
    if isinstance(error, (ConnectionError, OSError)) or any(
        marker in type_name
        for marker in ("connection", "connecterror", "transporterror", "network")
    ):
        return ModelClientError(
            ModelErrorCategory.RETRYABLE,
            ModelErrorCode.NETWORK,
            status_code=status,
        )
    if "badrequest" in type_name or "invalidrequest" in type_name or status in {400, 413}:
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.INVALID_REQUEST,
            status_code=status or 400,
        )
    if isinstance(error, (TypeError, ValueError)):
        return ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.CONFIGURATION,
            status_code=status,
        )
    return ModelClientError(ModelErrorCategory.FATAL, ModelErrorCode.UNKNOWN, status_code=status)


class OpenAICompatibleModelClient:
    """Synchronous OpenAI-compatible Chat Completions adapter."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Any | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        max_attempts: int | None = None,
        retry_policy: RetryPolicy | None = None,
        initial_delay_seconds: float = 0.5,
        max_delay_seconds: float = 30.0,
        backoff_factor: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
        retry_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if client is not None and transport is not None:
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if max_attempts is not None:
            if (
                isinstance(max_attempts, bool)
                or not isinstance(max_attempts, int)
                or max_attempts < 1
            ):
                raise ModelClientError(
                    ModelErrorCategory.FATAL,
                    ModelErrorCode.CONFIGURATION,
                )
            max_retries = max_attempts - 1
        if backoff_factor is not None:
            initial_delay_seconds = backoff_factor
        try:
            policy = retry_policy or RetryPolicy(
                max_retries=max_retries,
                initial_delay_seconds=initial_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
        except (TypeError, ValueError):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            ) from None
        if not callable(sleep) or not callable(clock):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if retry_observer is not None and not callable(retry_observer):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )

        self.model = model
        self.retry_policy = policy
        self._sleep = sleep
        self._clock = clock
        self._logger = logger or LOGGER
        self._retry_observer = retry_observer
        self._transport = None
        self._client = client

        if self._client is None and transport is not None:
            has_chat = _field(transport, "chat") is not _MISSING
            if has_chat or callable(transport):
                self._transport = transport
            else:
                # ``httpx.MockTransport`` (and equivalent HTTP transports)
                # expose ``handle_request`` rather than the OpenAI client's
                # ``chat`` surface.  Wrap that transport in an httpx client
                # so tests can exercise the official SDK without a network.
                http_client = transport
                if _field(transport, "handle_request") is not _MISSING:
                    try:
                        import httpx

                        http_client = httpx.Client(transport=transport)
                    except Exception:
                        raise ModelClientError(
                            ModelErrorCategory.FATAL,
                            ModelErrorCode.CONFIGURATION,
                        ) from None
                self._client = self._build_openai_client(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                    http_client=http_client,
                )
        elif self._client is None:
            self._client = self._build_openai_client(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                http_client=None,
            )

    @staticmethod
    def _build_openai_client(
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: float | None,
        http_client: Any | None,
    ) -> Any:
        if OpenAI is None:
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        kwargs: dict[str, Any] = {"max_retries": 0}
        if api_key is not None:
            if not isinstance(api_key, str) or not api_key:
                raise ModelClientError(
                    ModelErrorCategory.FATAL,
                    ModelErrorCode.CONFIGURATION,
                )
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        if timeout is not None:
            kwargs["timeout"] = timeout
        if http_client is not None:
            kwargs["http_client"] = http_client
        try:
            return OpenAI(**kwargs)
        except Exception:
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            ) from None

    @property
    def client(self) -> Any:
        """The injected or constructed transport client."""

        return self._client if self._client is not None else self._transport

    def _build_request(
        self,
        messages: Iterable[Mapping[str, Any]],
        tools: Iterable[Mapping[str, Any]] | None,
        parameters: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(messages, (str, bytes, bytearray)):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if isinstance(tools, (str, bytes, bytearray)):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        if parameters is not None and not isinstance(parameters, Mapping):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        try:
            request = dict(parameters or {})
            request["messages"] = list(messages)
            request["tools"] = [] if tools is None else list(tools)
        except Exception:
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            ) from None
        if "model" not in request:
            if self.model is None:
                raise ModelClientError(
                    ModelErrorCategory.FATAL,
                    ModelErrorCode.CONFIGURATION,
                )
            request["model"] = self.model
        model = request.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        return request

    def _send(self, request: Mapping[str, Any]) -> Any:
        if self._transport is not None:
            transport = self._transport
            if callable(transport):
                return transport(request)
            chat = _field(transport, "chat")
            completions = _field(chat, "completions")
            create = _field(completions, "create")
            if callable(create):
                return create(**request)
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        chat = _field(self._client, "chat")
        completions = _field(chat, "completions")
        create = _field(completions, "create")
        if not callable(create):
            raise ModelClientError(
                ModelErrorCategory.FATAL,
                ModelErrorCode.CONFIGURATION,
            )
        return create(**request)

    def _log_retry(self, error: ModelClientError, attempt: int, delay: float) -> None:
        # Only our own stable values are logged; provider exception text and
        # request headers are intentionally unavailable here.
        self._logger.info(
            "model request retry category=%s code=%s status=%s attempt=%d delay=%.6f",
            error.category.value,
            error.code.value,
            error.status_code,
            attempt,
            delay,
        )
        if self._retry_observer is not None:
            self._retry_observer(
                {
                    "reason": error.code.value,
                    "category": error.category.value,
                    "status_code": error.status_code,
                    "attempt": attempt,
                    "max_attempts": self.retry_policy.max_attempts,
                    "delay_seconds": delay,
                }
            )

    def complete(
        self,
        messages: Iterable[Mapping[str, Any]],
        tools: Iterable[Mapping[str, Any]] | None,
        parameters: Mapping[str, Any] | None = None,
    ) -> NormalizedAssistantResponse:
        """Call Chat Completions with bounded retries and safe normalization."""

        request = self._build_request(messages, tools, parameters)
        attempts = 0
        for retry_index in range(self.retry_policy.max_attempts):
            attempts += 1
            try:
                response = self._send(request)
            except ModelClientError as error:
                if error.category is ModelErrorCategory.RETRYABLE:
                    classified = ModelClientError(
                        error.category,
                        error.code,
                        status_code=error.status_code,
                        attempts=attempts,
                    )
                else:
                    raise error
            except Exception as exc:
                classified = _classify_exception(exc)
                classified = ModelClientError(
                    classified.category,
                    classified.code,
                    status_code=classified.status_code,
                    attempts=attempts,
                )
            else:
                try:
                    return _normalize_response(response)
                except Exception:
                    # A malformed SDK object is a safe protocol diagnostic,
                    # never an opportunity to stringify provider internals.
                    return NormalizedAssistantResponse(diagnostics=("response_error",))

            if (
                classified.category is not ModelErrorCategory.RETRYABLE
                or retry_index >= self.retry_policy.max_retries
            ):
                raise classified
            delay = self.retry_policy.delay_for_retry(retry_index)
            self._log_retry(classified, attempts, delay)
            try:
                self._clock()
                self._sleep(delay)
            except Exception:
                raise ModelClientError(
                    ModelErrorCategory.FATAL,
                    ModelErrorCode.RETRY_SLEEP,
                    attempts=attempts,
                ) from None
        raise ModelClientError(
            ModelErrorCategory.FATAL,
            ModelErrorCode.UNKNOWN,
            attempts=attempts,
        )


# Naming aliases keep the narrow public adapter discoverable without adding
# another implementation or protocol boundary.
OpenAIModelClient = OpenAICompatibleModelClient
ChatCompletionsModelClient = OpenAICompatibleModelClient
OpenAICompatibleClient = OpenAICompatibleModelClient
OpenAICompatibleChatCompletionsClient = OpenAICompatibleModelClient
OpenAIChatCompletionsClient = OpenAICompatibleModelClient

# The normalizer remains a pure implementation detail for the adapter, but a
# public alias is convenient for boundary-focused tests and integrations.
normalize_response = _normalize_response


__all__ = [
    "ChatCompletionsModelClient",
    "ErrorCategory",
    "ModelClient",
    "ModelClientError",
    "ModelError",
    "ModelErrorCategory",
    "ModelErrorCode",
    "ModelErrorKind",
    "ModelRequestError",
    "OpenAICompatibleClient",
    "OpenAICompatibleChatCompletionsClient",
    "OpenAICompatibleModelClient",
    "OpenAIChatCompletionsClient",
    "OpenAIModelClient",
    "NormalizedAssistantResponse",
    "NormalizedToolCall",
    "NormalizedUsage",
    "normalize_response",
    "RetryConfig",
    "RetryPolicy",
]
