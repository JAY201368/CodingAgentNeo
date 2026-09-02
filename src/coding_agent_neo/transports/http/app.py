"""Front-end-independent ASGI application for the Agent HTTP/SSE binding.

Only the public ``AgentBackendProvider`` port crosses this module's
composition seam.  The default production provider is supplied by
``http_cli.py``; the transport itself never constructs or adapts a backend.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Iterable, Iterator
from contextlib import asynccontextmanager
from math import isfinite
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from coding_agent_neo.backend import (
    AgentBackendProvider,
    AgentBackendProviderError,
    BackendClosedError,
    CloseSession,
    InvalidSessionHistoryCursorError,
    InvalidSessionHistoryIdError,
    InvalidSessionHistoryLimitError,
    SessionHistoryNotFoundError,
    SessionHistoryUnavailableError,
    SessionResumeUnavailableError,
    TurnInProgressError,
)
from coding_agent_neo.transports.http.commands import CommandDecodeError, decode_command
from coding_agent_neo.transports.http.registry import (
    SessionExistsError,
    TransportSession,
    TransportSessionRegistry,
    _SessionEventPump,
)
from coding_agent_neo.transports.http.security import is_allowed_host, is_allowed_origin
from coding_agent_neo.transports.http.wire import (
    BASE_PATH,
    HEALTH_PATH,
    HISTORY_EVENT_DEFAULT_LIMIT,
    HISTORY_EVENT_MAX_LIMIT,
    HISTORY_LIST_DEFAULT_LIMIT,
    HISTORY_LIST_MAX_LIMIT,
    PROTOCOL_VERSION,
    AcceptedResponse,
    CursorError,
    HealthResponse,
    HistoryCursorError,
    HistoryIdError,
    HistoryLimitError,
    SessionCreatedResponse,
    SessionStatusResponse,
    encode_keepalive,
    encode_sse,
    error_body,
    event_page_to_dict,
    event_to_dict,
    history_page_to_dict,
    parse_history_id,
    parse_history_limit,
    parse_history_list_cursor,
    parse_history_sequence,
    select_cursor,
)

_DEFAULT_KEEPALIVE_SECONDS = 15.0
_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 30.0
_JSON_MEDIA_TYPE = "application/json"


class _UnconfiguredProvider:
    """Safe placeholder used when an embedder forgets to inject a provider."""

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50):
        del cursor, limit
        raise RuntimeError("Agent backend provider is not configured")

    def read_session_events(self, session_id: str, *, since: int = 0, limit: int = 200):
        del session_id, since, limit
        raise RuntimeError("Agent backend provider is not configured")

    def create_session(self, *, resume_session_id: str | None = None):
        del resume_session_id
        raise RuntimeError("Agent backend provider is not configured")


def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message),
        media_type=_JSON_MEDIA_TYPE,
    )


def _safe_error(status_code: int = 500) -> JSONResponse:
    """Return a stable error without exposing internal exception details."""

    return _json_error(
        status_code, "internal_error", "the Agent service could not complete the request"
    )


def _finite_json_response(content: Any, *, status_code: int = 200) -> Response:
    """Return one bounded JSON response without leaking serialization errors."""

    try:
        payload = json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return _safe_error()
    # The provider enforces this bound; retaining the final transport guard
    # keeps a malformed test/embedded provider from emitting an unbounded page.
    if len(payload) > 8 * 1024 * 1024:
        return _safe_error()
    return Response(content=payload, status_code=status_code, media_type=_JSON_MEDIA_TYPE)


def _provider_error(error: AgentBackendProviderError, *, resume: bool = False) -> JSONResponse:
    """Map typed provider errors to the documented stable wire response."""

    if isinstance(error, InvalidSessionHistoryIdError):
        return _json_error(400, "invalid_history_id", "history session ID is invalid")
    if isinstance(error, InvalidSessionHistoryCursorError):
        return _json_error(400, "invalid_history_cursor", "history cursor is invalid")
    if isinstance(error, InvalidSessionHistoryLimitError):
        return _json_error(400, "invalid_history_limit", "history limit is invalid")
    if isinstance(error, SessionHistoryNotFoundError):
        if resume:
            return _json_error(422, "invalid_resume", "session cannot be resumed")
        return _json_error(404, "history_not_found", "session history was not found")
    if isinstance(error, SessionResumeUnavailableError):
        return _json_error(422, "invalid_resume", "session cannot be resumed")
    if isinstance(error, SessionHistoryUnavailableError):
        if resume:
            return _json_error(422, "invalid_resume", "session cannot be resumed")
        return _json_error(422, "history_unavailable", "session history is unavailable")
    return _safe_error()


def _query_values(request: Request, name: str) -> list[str]:
    values = request.query_params.getlist(name)
    return [value for value in values if isinstance(value, str)]


def _history_query(
    request: Request,
    *,
    event: bool,
) -> tuple[str | None, int, int] | tuple[str, int, int]:
    """Decode one strict bounded history query without inspecting paths."""

    allowed = {"since", "limit"} if event else {"cursor", "limit"}
    unknown = set(request.query_params.keys()) - allowed
    if unknown:
        if unknown & {"path", "filename", "session_dir"}:
            raise HistoryIdError
        raise HistoryCursorError

    limit_values = _query_values(request, "limit")
    if len(limit_values) > 1:
        raise HistoryLimitError
    limit = parse_history_limit(
        limit_values[0] if limit_values else None,
        default=HISTORY_EVENT_DEFAULT_LIMIT if event else HISTORY_LIST_DEFAULT_LIMIT,
        maximum=HISTORY_EVENT_MAX_LIMIT if event else HISTORY_LIST_MAX_LIMIT,
    )
    if event:
        since_values = _query_values(request, "since")
        if len(since_values) > 1:
            raise HistoryCursorError
        return None, parse_history_sequence(since_values[0] if since_values else None), limit
    cursor_values = _query_values(request, "cursor")
    if len(cursor_values) > 1:
        raise HistoryCursorError
    return parse_history_list_cursor(cursor_values[0] if cursor_values else None), 0, limit


class _SessionRequestError(ValueError):
    """The POST session body is not one of the documented request shapes."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _SessionRequestError
        result[key] = value
    return result


async def _read_session_request(request: Request, *, max_bytes: int) -> str | None:
    """Decode empty/new/resume session bodies with no path-bearing input."""

    body = await request.body()
    if not body:
        return None
    if len(body) > max_bytes:
        raise _SessionRequestError
    try:
        value = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(_SessionRequestError),
        )
    except _SessionRequestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _SessionRequestError from error
    if not isinstance(value, dict):
        raise _SessionRequestError
    if not value:
        return None
    if set(value) != {"resume_session_id"}:
        raise _SessionRequestError
    resume_session_id = value["resume_session_id"]
    try:
        return parse_history_id(resume_session_id)
    except HistoryIdError as error:
        raise error


async def _ensure_empty_history_body(request: Request) -> None:
    """Reject body-bearing finite-history requests before provider access."""

    body = await request.body()
    if body:
        raise _SessionRequestError


async def _read_json(request: Request, *, max_bytes: int, allow_empty: bool = False) -> Any:
    """Read one bounded JSON body and never include its contents in errors."""

    body = await request.body()
    if not body:
        if allow_empty:
            return {}
        raise CommandDecodeError("request body is required")
    if len(body) > max_bytes:
        raise CommandDecodeError("request body is too large")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CommandDecodeError("request body must be valid JSON") from error


def _event_stream(
    session: TransportSession,
    cursor: int,
    *,
    keepalive_seconds: float,
) -> Iterator[str]:
    """Yield SSE frames from a session-owned, reconnectable event pump.

    Disconnecting this generator only removes its subscription.  It never
    sends an Agent command or closes the backend; the single session pump is
    stopped by explicit session/app shutdown.
    """

    subscription = _event_subscription(session, cursor)
    try:
        while True:
            try:
                item = subscription.get(timeout=keepalive_seconds)
            except queue.Empty:
                yield encode_keepalive()
                continue
            if not isinstance(item, tuple) or len(item) != 2 or item[0] != "event":
                return
            _kind, value = item
            try:
                payload = event_to_dict(value)
                sequence = payload.get("sequence")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                    return
                if sequence <= cursor:
                    continue
                frame = encode_sse(value)
            except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
                return
            session.record_event(sequence)
            yield frame
    finally:
        subscription.close()


def _event_subscription(session: Any, cursor: int):
    """Get a subscription from a real session or a private test seam."""

    subscribe = getattr(session, "subscribe_events", None)
    if callable(subscribe):
        return subscribe(cursor)
    # Keep the private helper usable with lightweight port-only test doubles.
    # The helper is attached to the double so reconnects still share one pump.
    pump = getattr(session, "_http_event_pump", None)
    if pump is None:
        pump = _SessionEventPump(session.backend)
        try:
            setattr(session, "_http_event_pump", pump)
        except (AttributeError, TypeError):
            # A slots-only test double can still use one pump for this stream;
            # the production TransportSession always has the lifecycle hook.
            pass
    return pump.subscribe(cursor)


def _session_or_error(
    registry: TransportSessionRegistry,
    transport_session_id: str,
) -> tuple[TransportSession | None, JSONResponse | None]:
    session = registry.get(transport_session_id)
    if session is None:
        return None, _json_error(404, "session_not_found", "transport session was not found")
    if session.closed:
        return None, _json_error(410, "session_closed", "transport session is closed")
    return session, None


def create_app(
    provider: AgentBackendProvider | None = None,
    *,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] | None = None,
    keepalive_seconds: float = _DEFAULT_KEEPALIVE_SECONDS,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
) -> FastAPI:
    """Create the standalone Agent API ASGI app.

    The application accepts one workspace-scoped ``AgentBackendProvider``.
    Omitting it leaves a safe placeholder that reports an unconfigured
    provider when a session or history operation is requested.
    """

    if provider is None:
        selected_provider: AgentBackendProvider = _UnconfiguredProvider()  # type: ignore[assignment]
    elif isinstance(provider, AgentBackendProvider):
        selected_provider = provider
    else:
        raise TypeError("provider must implement AgentBackendProvider")
    if (
        isinstance(keepalive_seconds, bool)
        or not isinstance(keepalive_seconds, (int, float))
        or not isfinite(keepalive_seconds)
        or keepalive_seconds <= 0
    ):
        raise ValueError("keepalive_seconds must be a positive number")
    if (
        isinstance(max_body_bytes, bool)
        or not isinstance(max_body_bytes, int)
        or max_body_bytes <= 0
    ):
        raise ValueError("max_body_bytes must be a positive integer")

    # Keep imports and routes limited to the Agent API; Swagger/OpenAPI would
    # be an unrelated product surface for this standalone transport command.
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        registry.close_all()

    app = FastAPI(
        title="CodingAgentNeo Agent API",
        version=str(PROTOCOL_VERSION),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    registry = TransportSessionRegistry(
        selected_provider,
        close_timeout_seconds=close_timeout_seconds,
    )
    # Expose the seam for ASGI lifespan/tests without putting it in any wire
    # response.  It contains only the injected AgentBackend ports.
    app.state.transport_sessions = registry
    app.state.session_registry = registry
    app.state.agent_backend_provider = selected_provider
    app.state.backend_provider = selected_provider

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if not is_allowed_host(request.headers.get("host"), allowed_hosts):
            return _json_error(400, "invalid_host", "request host is not allowed")
        if not is_allowed_origin(request.headers.get("origin"), allowed_origins):
            return _json_error(400, "invalid_origin", "request origin is not allowed")
        try:
            return await call_next(request)
        except Exception:
            return _safe_error()

    @app.get(HEALTH_PATH)
    async def health() -> JSONResponse:
        return JSONResponse(HealthResponse().to_dict(), media_type=_JSON_MEDIA_TYPE)

    @app.get(BASE_PATH + "/session-history")
    async def session_history(request: Request) -> Response:
        try:
            cursor, _since, limit = _history_query(request, event=False)
        except HistoryIdError:
            return _json_error(400, "invalid_history_id", "history session ID is invalid")
        except HistoryCursorError:
            return _json_error(400, "invalid_history_cursor", "history cursor is invalid")
        except HistoryLimitError:
            return _json_error(400, "invalid_history_limit", "history limit is invalid")
        try:
            await _ensure_empty_history_body(request)
        except _SessionRequestError:
            return _json_error(400, "invalid_session_request", "session request is invalid")
        try:
            page = selected_provider.list_sessions(cursor=cursor, limit=limit)
            return _finite_json_response(history_page_to_dict(page))
        except AgentBackendProviderError as error:
            return _provider_error(error)
        except Exception:
            return _safe_error()

    @app.get(BASE_PATH + "/session-history/{session_id:path}/events")
    async def session_history_events(request: Request, session_id: str) -> Response:
        try:
            selected_id = parse_history_id(session_id)
        except HistoryIdError:
            return _json_error(400, "invalid_history_id", "history session ID is invalid")
        try:
            _unused_cursor, since, limit = _history_query(request, event=True)
        except HistoryIdError:
            return _json_error(400, "invalid_history_id", "history session ID is invalid")
        except HistoryCursorError:
            return _json_error(400, "invalid_history_cursor", "history cursor is invalid")
        except HistoryLimitError:
            return _json_error(400, "invalid_history_limit", "history limit is invalid")
        try:
            await _ensure_empty_history_body(request)
        except _SessionRequestError:
            return _json_error(400, "invalid_session_request", "session request is invalid")
        try:
            page = selected_provider.read_session_events(selected_id, since=since, limit=limit)
            return _finite_json_response(event_page_to_dict(page))
        except AgentBackendProviderError as error:
            return _provider_error(error)
        except Exception:
            return _safe_error()

    @app.post(BASE_PATH + "/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        try:
            resume_session_id = await _read_session_request(request, max_bytes=max_body_bytes)
        except HistoryIdError:
            return _json_error(400, "invalid_history_id", "history session ID is invalid")
        except _SessionRequestError:
            return _json_error(400, "invalid_session_request", "session request is invalid")
        session = None
        try:
            session = registry.create(resume_session_id=resume_session_id)
            response = SessionCreatedResponse(
                session.transport_session_id,
                session.state,
                cursor=session.cursor,
                approval_mode=session.approval_mode,
            )
            return JSONResponse(response.to_dict(), status_code=201, media_type=_JSON_MEDIA_TYPE)
        except SessionExistsError:
            return _json_error(409, "session_exists", "an active transport session already exists")
        except AgentBackendProviderError as error:
            return _provider_error(error, resume=resume_session_id is not None)
        except Exception:
            if session is not None:
                registry.close_session(
                    session.transport_session_id,
                    reason="session_start_failed",
                )
            return _safe_error()

    @app.get(BASE_PATH + "/sessions/{transport_session_id}")
    async def session_status(transport_session_id: str):
        session, error = _session_or_error(registry, transport_session_id)
        if error is not None:
            return error
        assert session is not None
        try:
            response = SessionStatusResponse(
                session.state, session.cursor, session.closed, session.approval_mode
            )
            return JSONResponse(response.to_dict(), media_type=_JSON_MEDIA_TYPE)
        except Exception:
            return _safe_error()

    @app.get(BASE_PATH + "/sessions/{transport_session_id}/events")
    async def events(request: Request, transport_session_id: str):
        try:
            cursor = select_cursor(
                request.query_params.get("since"),
                request.headers.get("last-event-id"),
            )
        except CursorError:
            return _json_error(400, "invalid_cursor", "event cursor is invalid")
        session, error = _session_or_error(registry, transport_session_id)
        if error is not None:
            return error
        assert session is not None
        return StreamingResponse(
            _event_stream(session, cursor, keepalive_seconds=float(keepalive_seconds)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(BASE_PATH + "/sessions/{transport_session_id}/commands")
    async def command(request: Request, transport_session_id: str) -> JSONResponse:
        session, error = _session_or_error(registry, transport_session_id)
        if error is not None:
            return error
        assert session is not None
        try:
            body = await _read_json(request, max_bytes=max_body_bytes)
            decoded = decode_command(body)
        except CommandDecodeError:
            return _json_error(400, "invalid_command", "command is invalid")
        try:
            session.backend.send(decoded)
        except TurnInProgressError:
            return _json_error(409, "turn_in_progress", "a turn is already running")
        except BackendClosedError:
            registry.close_session(
                transport_session_id,
                reason="backend_closed",
                send_command=False,
            )
            return _json_error(410, "session_closed", "transport session is closed")
        except (TypeError, ValueError):
            return _json_error(400, "invalid_command", "command is invalid")
        except Exception:
            return _safe_error()
        if isinstance(decoded, CloseSession):
            # The command has already been delivered exactly once.  Mark the
            # transport closed and perform the separate bounded cleanup.
            registry.close_session(
                transport_session_id,
                reason=decoded.reason,
                send_command=False,
                wait=False,
            )
        return JSONResponse(
            AcceptedResponse().to_dict(), status_code=202, media_type=_JSON_MEDIA_TYPE
        )

    @app.delete(BASE_PATH + "/sessions/{transport_session_id}", status_code=204)
    async def delete_session(transport_session_id: str) -> Response:
        if not registry.close_session(transport_session_id, reason="session_deleted"):
            return _json_error(404, "session_not_found", "transport session was not found")
        return Response(status_code=204)

    return app


# Names used by embedders in earlier prototypes remain aliases, while the
# canonical public constructor is ``create_app``.
create_http_app = create_app
build_app = create_app
build_http_app = create_app


__all__ = ["BASE_PATH", "build_app", "build_http_app", "create_app", "create_http_app"]
