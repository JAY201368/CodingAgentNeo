"""Composition root for the same-origin Web demonstration server.

The Agent HTTP/SSE application intentionally has no knowledge of Vue, Vite, or
static files.  This module is the only place where that frontend-independent
ASGI app is composed with the already-built ``web/dist`` directory.

The launcher does not build the Web project and does not package a copy of its
output.  A source checkout uses ``web/dist`` by default; an installed package
can point at a build with ``--dist-dir``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from coding_agent_neo.assembly import build_agent_backend
from coding_agent_neo.config import ConfigError, load_config
from coding_agent_neo.http_cli import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    _config_values,
)
from coding_agent_neo.http_cli import (
    build_parser as build_http_parser,
)

DEFAULT_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
_API_PATH = "/api/v1"
_CONFIG_MISSING = object()


class WebAssetsError(RuntimeError):
    """The requested static Web build is not a safe, usable directory."""


def resolve_dist_dir(dist_dir: str | Path | None = None) -> Path:
    """Validate and return a Web build directory before app side effects.

    The launcher requires the Vite entrypoint as a small, deterministic
    deployment contract.  Error text deliberately contains no resolved path:
    callers may pass a private workspace path and startup diagnostics must not
    echo it.
    """

    try:
        candidate = DEFAULT_DIST_DIR if dist_dir is None else Path(dist_dir)
        resolved = candidate.expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise WebAssetsError("Web build is unavailable; run npm --prefix web run build first")
        index = resolved / "index.html"
        if not index.is_file():
            raise WebAssetsError("Web build is incomplete; run npm --prefix web run build first")
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        if isinstance(error, WebAssetsError):
            raise
        raise WebAssetsError(
            "Web build could not be inspected; run npm --prefix web run build first"
        ) from None
    return resolved


def _is_api_path(path: str) -> bool:
    """Match only the versioned API prefix, not similarly named SPA routes."""

    return path == _API_PATH or path.startswith(f"{_API_PATH}/")


def _looks_like_asset(path: str) -> bool:
    """Keep missing file requests as 404 while routing extensionless paths to SPA."""

    final_component = path.rsplit("/", 1)[-1]
    return "." in final_component


class SPAStaticFiles:
    """Serve a Vite build and fall back to ``index.html`` for SPA routes.

    Starlette's ``html=True`` serves an index for directories but does not
    implement client-side route fallback for an arbitrary extensionless URL.
    This small wrapper keeps that behavior explicit and delegates all path
    safety and file handling to ``StaticFiles``.
    """

    def __init__(self, directory: Path) -> None:
        try:
            from starlette.staticfiles import StaticFiles
        except ImportError as error:  # pragma: no cover - guarded by the http extra
            raise RuntimeError("HTTP dependencies are not installed; use the http extra") from error
        self._static = StaticFiles(directory=directory, html=True, check_dir=True)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        from starlette.exceptions import HTTPException
        from starlette.responses import PlainTextResponse

        if scope.get("type") != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        path = str(scope.get("path") or "/")
        method = scope.get("method")
        try:
            await self._static(scope, receive, send)
        except HTTPException as error:
            if (
                error.status_code == 404
                and method in {"GET", "HEAD"}
                and not _looks_like_asset(path)
            ):
                # ``get_response`` accepts a path relative to the static
                # root.  Calling it directly avoids changing the request
                # URL seen by FileResponse and keeps HEAD semantics intact.
                response = await self._static.get_response("index.html", scope)
                await response(scope, receive, send)
                return
            response = PlainTextResponse(error.detail, status_code=error.status_code)
            await response(scope, receive, send)


class WebCompositionApp:
    """Route API requests to the generic app and everything else to Web."""

    def __init__(
        self,
        api_app: Any,
        static_app: SPAStaticFiles,
        *,
        allowed_hosts: Iterable[str] | None = None,
        allowed_origins: Iterable[str] | None = None,
    ) -> None:
        self.api_app = api_app
        self.static_app = static_app
        self._allowed_hosts = None if allowed_hosts is None else tuple(allowed_hosts)
        self._allowed_origins = None if allowed_origins is None else tuple(allowed_origins)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            # Forward lifespan so the generic HTTP app retains ownership of
            # its registry and idempotent backend shutdown.
            await self.api_app(scope, receive, send)
            return
        if scope_type == "http" and _is_api_path(str(scope.get("path") or "")):
            await self.api_app(scope, receive, send)
            return
        if scope_type == "http" and not self._request_is_local(scope):
            # Let the generic app produce its stable invalid_host/origin
            # response.  This keeps the same security behavior for static
            # requests without duplicating its error mapping here.
            await self.api_app(scope, receive, send)
            return
        await self.static_app(scope, receive, send)

    def _request_is_local(self, scope: dict[str, Any]) -> bool:
        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        from coding_agent_neo.transports.http.security import is_allowed_host, is_allowed_origin

        return is_allowed_host(headers.get("host"), self._allowed_hosts) and is_allowed_origin(
            headers.get("origin"), self._allowed_origins
        )


def create_app(
    dist_dir: str | Path | None = None,
    *,
    backend_factory=build_agent_backend,
    config: Any = _CONFIG_MISSING,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] | None = None,
    keepalive_seconds: float = 15.0,
    max_body_bytes: int = 2 * 1024 * 1024,
    close_timeout_seconds: float = 30.0,
) -> WebCompositionApp:
    """Build the same-origin API + static ASGI application.

    Static assets are validated before importing or constructing the generic
    API application.  Backend creation itself remains request-scoped inside
    the API registry, exactly as it is for ``coding-agent-neo-http``.
    """

    directory = resolve_dist_dir(dist_dir)
    normalized_allowed_hosts = None if allowed_hosts is None else tuple(allowed_hosts)
    normalized_allowed_origins = None if allowed_origins is None else tuple(allowed_origins)
    try:
        from coding_agent_neo.transports.http import create_app as create_http_app
    except ImportError as error:  # pragma: no cover - guarded by the http extra
        raise RuntimeError("HTTP dependencies are not installed; use the http extra") from error

    api_app = create_http_app(
        backend_factory=backend_factory,
        **({"config": config} if config is not _CONFIG_MISSING else {}),
        allowed_hosts=normalized_allowed_hosts,
        allowed_origins=normalized_allowed_origins,
        keepalive_seconds=keepalive_seconds,
        max_body_bytes=max_body_bytes,
        close_timeout_seconds=close_timeout_seconds,
    )
    return WebCompositionApp(
        api_app,
        SPAStaticFiles(directory),
        allowed_hosts=normalized_allowed_hosts,
        allowed_origins=normalized_allowed_origins,
    )


# Composition roots often use one of these names; all point to the same
# explicit API/static assembly and do not alter the transport contract.
build_app = create_app
build_web_app = create_app


def build_parser() -> argparse.ArgumentParser:
    """Build the Web launcher parser while sharing Agent config options."""

    parser = build_http_parser()
    parser.prog = "coding-agent-neo-web"
    parser.description = (
        "Run the local CodingAgentNeo Web app and frontend-independent Agent API "
        f"on {DEFAULT_HOST}."
    )
    parser.epilog = (
        "Only 127.0.0.1 is supported. The launcher serves /api/v1 and the "
        "already-built Web assets; API keys are read from configuration environment variables."
    )
    parser.add_argument(
        "--dist-dir",
        metavar="PATH",
        help="Vite build directory (default: the checkout's web/dist).",
    )
    return parser


def run_server(
    config: Any,
    *,
    dist_dir: str | Path | None = None,
    port: int = DEFAULT_PORT,
    backend_factory=build_agent_backend,
    log_level: str = "warning",
) -> None:
    """Run the composed app on loopback only after validating Web assets."""

    # Do this before importing uvicorn or constructing the API registry.  In
    # particular, a missing build must not create a backend/session or start a
    # listener that cannot serve the promised product.
    directory = resolve_dist_dir(dist_dir)
    try:
        import uvicorn
    except ImportError as error:  # pragma: no cover - guarded by the http extra
        raise RuntimeError("HTTP dependencies are not installed; use the http extra") from error

    app = create_app(directory, backend_factory=backend_factory, config=config)
    uvicorn.run(
        app,
        host=DEFAULT_HOST,
        port=port,
        log_level=log_level,
        access_log=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse configuration and run the composed Web/API service."""

    args = build_parser().parse_args(argv)
    try:
        # Validate the build before reading configuration or constructing any
        # API/application objects.  A failed Web launch is therefore a safe,
        # deterministic startup error even when the Agent config is invalid.
        resolve_dist_dir(args.dist_dir)
        config = load_config(_config_values(args), config_path=args.config)
        run_server(config, dist_dir=args.dist_dir, port=args.port, log_level=args.log_level)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except WebAssetsError as error:
        print(f"startup failure: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except RuntimeError as error:
        if "HTTP dependencies" in str(error):
            print("startup failure: HTTP dependencies are not installed", file=sys.stderr)
        else:
            print("startup failure: Web service could not start", file=sys.stderr)
        return 1
    except Exception:
        print("startup failure: Web service could not start", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DIST_DIR",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "SPAStaticFiles",
    "WebAssetsError",
    "WebCompositionApp",
    "build_app",
    "build_parser",
    "build_web_app",
    "create_app",
    "main",
    "resolve_dist_dir",
    "run_server",
]
