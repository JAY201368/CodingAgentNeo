"""Loopback Host and Origin checks for the local Agent HTTP service."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

_DEFAULT_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "testserver"})


def _split_host(value: str) -> tuple[str, int | None] | None:
    """Parse a Host header without accepting userinfo or malformed ports."""

    if not isinstance(value, str) or not value or "," in value:
        return None
    if value.endswith(":") and value.count(":") == 1:
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if hostname is None or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path != value and not (value.startswith("[") and parsed.path == value):
        # ``urlsplit`` normally leaves a valid host:port as path; this guard
        # mainly rejects values containing a slash or query delimiter.
        if "/" in value or "?" in value or "#" in value:
            return None
    if port is not None and not 0 <= port <= 65535:
        return None
    return hostname.casefold().rstrip("."), port


def is_allowed_host(value: str | None, allowed_hosts: Iterable[str] | None = None) -> bool:
    """Accept loopback hostnames (and the TestClient sentinel) only."""

    if value is None:
        return False
    parsed = _split_host(value)
    if parsed is None:
        return False
    hostname, _port = parsed
    hosts = (
        frozenset(host.casefold().rstrip(".") for host in allowed_hosts)
        if allowed_hosts is not None
        else _DEFAULT_LOCAL_HOSTS
    )
    return hostname in hosts


def is_allowed_origin(value: str | None, allowed_origins: Iterable[str] | None = None) -> bool:
    """Accept absent or local browser origins without enabling wildcard CORS."""

    if value is None:
        return True
    if not isinstance(value, str) or not value or value == "null":
        return False
    if value.endswith(":") and value.count(":") == 1:
        return False
    if allowed_origins is not None:
        return value in frozenset(allowed_origins)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    if port is not None and not 0 <= port <= 65535:
        return False
    return hostname.casefold().rstrip(".") in {"127.0.0.1", "localhost", "testserver"}


__all__ = ["is_allowed_host", "is_allowed_origin"]
