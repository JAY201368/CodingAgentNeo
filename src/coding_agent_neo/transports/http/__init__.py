"""Standalone Agent HTTP/SSE transport binding.

Importing this package requires the optional ``http`` extra.  The core CLI
and in-process adapter intentionally do not import it.
"""

from coding_agent_neo.transports.http.app import (
    build_app,
    build_http_app,
    create_app,
    create_http_app,
)
from coding_agent_neo.transports.http.commands import (
    CommandDecodeError,
    CommandDecoder,
    decode_command,
)
from coding_agent_neo.transports.http.registry import (
    AgentBackendFactory,
    BackendFactory,
    RegistrySnapshot,
    SessionExistsError,
    SessionRegistry,
    TransportSession,
    TransportSessionRegistry,
)
from coding_agent_neo.transports.http.wire import (
    BASE_PATH,
    HEALTH_PATH,
    PROTOCOL_VERSION,
    SESSIONS_PATH,
    SSE_MEDIA_TYPE,
    AcceptedResponse,
    CursorError,
    HealthResponse,
    SessionCreatedResponse,
    SessionStatusResponse,
    encode_keepalive,
    encode_sse,
    error_body,
    event_json,
    event_to_dict,
    parse_cursor,
    select_cursor,
)

__all__ = [
    "AcceptedResponse",
    "AgentBackendFactory",
    "BASE_PATH",
    "BackendFactory",
    "CommandDecodeError",
    "CommandDecoder",
    "CursorError",
    "HEALTH_PATH",
    "HealthResponse",
    "PROTOCOL_VERSION",
    "RegistrySnapshot",
    "SSE_MEDIA_TYPE",
    "SESSIONS_PATH",
    "SessionCreatedResponse",
    "SessionExistsError",
    "SessionRegistry",
    "SessionStatusResponse",
    "TransportSession",
    "TransportSessionRegistry",
    "build_app",
    "build_http_app",
    "create_app",
    "create_http_app",
    "decode_command",
    "encode_keepalive",
    "encode_sse",
    "error_body",
    "event_json",
    "event_to_dict",
    "parse_cursor",
    "select_cursor",
]
