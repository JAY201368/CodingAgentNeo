"""Front-end-independent ASGI application for the Agent HTTP/SSE binding.

Only the public ``AgentBackend`` port crosses this module's composition seam.
The default production factory is supplied by ``http_cli.py``; tests and
other embedders can inject any port-compatible backend factory without making
the transport aware of the Agent Core or a particular frontend.
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
    BackendClosedError,
    CloseSession,
    TurnInProgressError,
)
from coding_agent_neo.backend_factory import AgentBackendFactory
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
    PROTOCOL_VERSION,
    AcceptedResponse,
    CursorError,
    HealthResponse,
    SessionCreatedResponse,
    SessionStatusResponse,
    encode_keepalive,
    encode_sse,
    error_body,
    event_to_dict,
    select_cursor,
)

_DEFAULT_KEEPALIVE_SECONDS = 15.0
_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 30.0
_JSON_MEDIA_TYPE = "application/json"
_CONFIG_MISSING = object()


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
    backend_factory: AgentBackendFactory | None = None,
    *,
    factory: AgentBackendFactory | None = None,
    config: Any = _CONFIG_MISSING,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] | None = None,
    keepalive_seconds: float = _DEFAULT_KEEPALIVE_SECONDS,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
) -> FastAPI:
    """Create the standalone Agent API ASGI app.

    ``backend_factory`` is intentionally required for session creation.  The
    composition root (normally :func:`http_cli.run_server`) supplies the
    shared ``build_agent_backend`` factory; the HTTP package itself never
    imports a concrete backend service or another adapter.
    """

    selected_factory = backend_factory if backend_factory is not None else factory
    if backend_factory is not None and factory is not None:
        raise TypeError("provide backend_factory or factory, not both")
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
    registry_kwargs: dict[str, Any] = {"close_timeout_seconds": close_timeout_seconds}
    if config is not _CONFIG_MISSING:
        registry_kwargs["config"] = config
    registry = TransportSessionRegistry(
        selected_factory if selected_factory is not None else _unconfigured_factory,
        **registry_kwargs,
    )
    # Expose the seam for ASGI lifespan/tests without putting it in any wire
    # response.  It contains only the injected AgentBackend ports.
    app.state.transport_sessions = registry
    app.state.session_registry = registry

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

    @app.post(BASE_PATH + "/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        try:
            body = await _read_json(request, max_bytes=max_body_bytes, allow_empty=True)
            if not isinstance(body, dict) or body:
                raise CommandDecodeError("session body must be empty")
        except CommandDecodeError:
            return _json_error(400, "invalid_session_request", "session request is invalid")
        session = None
        try:
            session = registry.create()
            response = SessionCreatedResponse(
                session.transport_session_id,
                session.state,
                cursor=session.cursor,
            )
            return JSONResponse(response.to_dict(), status_code=201, media_type=_JSON_MEDIA_TYPE)
        except SessionExistsError:
            return _json_error(409, "session_exists", "an active transport session already exists")
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
            response = SessionStatusResponse(session.state, session.cursor, session.closed)
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


def _unconfigured_factory(*_args: Any, **_kwargs: Any):
    raise RuntimeError("Agent backend factory is not configured")


# Names used by embedders in earlier prototypes remain aliases, while the
# canonical public constructor is ``create_app``.
create_http_app = create_app
build_app = create_app
build_http_app = create_app


__all__ = ["BASE_PATH", "build_app", "build_http_app", "create_app", "create_http_app"]
