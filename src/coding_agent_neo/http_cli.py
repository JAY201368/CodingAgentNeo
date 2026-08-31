"""Composition root for the standalone Agent HTTP/SSE service."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from coding_agent_neo import __version__
from coding_agent_neo.assembly import build_agent_backend
from coding_agent_neo.config import ConfigError, load_config

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_CONFIG_OPTIONS = (
    "model",
    "api_base",
    "api_key_env",
    "workspace",
    "session_dir",
    "approval_mode",
    "max_steps",
    "max_tool_calls",
    "max_wall_seconds",
    "command_timeout",
    "context_window",
    "reserved_output_tokens",
    "model_output_limit",
    "session_output_limit",
)


def _port(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 0 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the HTTP-only launcher parser without loading configuration."""

    parser = argparse.ArgumentParser(
        prog="coding-agent-neo-http",
        description=(
            "Run the local, frontend-independent CodingAgentNeo Agent HTTP/SSE API "
            f"on {DEFAULT_HOST}."
        ),
        epilog=(
            "Only 127.0.0.1 is supported; the service exposes /api/v1 and no "
            "static Web resources. API keys are read from configuration environment variables."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT)
    parser.add_argument("--model")
    parser.add_argument("--api-base")
    parser.add_argument("--api-key-env")
    parser.add_argument("--workspace")
    parser.add_argument("--session-dir")
    parser.add_argument("--approval-mode", choices=("ask", "auto", "deny"))
    parser.add_argument("--yolo", action="store_true", help="Alias for --approval-mode auto.")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-wall-seconds", type=float)
    parser.add_argument("--command-timeout", type=float)
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--reserved-output-tokens", type=int)
    parser.add_argument("--model-output-limit", type=int)
    parser.add_argument("--session-output-limit", type=int)
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="warning",
    )
    return parser


def _config_values(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        name: getattr(args, name) for name in _CONFIG_OPTIONS if getattr(args, name) is not None
    }
    if args.yolo:
        if values.get("approval_mode") not in {None, "auto"}:
            raise ConfigError("--yolo conflicts with --approval-mode")
        values["approval_mode"] = "auto"
    return values


def run_server(
    config: Any,
    *,
    port: int = DEFAULT_PORT,
    backend_factory=build_agent_backend,
    log_level: str = "warning",
) -> None:
    """Build the Agent HTTP app and hand it to uvicorn on loopback only."""

    try:
        import uvicorn
    except ImportError as error:  # pragma: no cover - guarded by the extra
        raise RuntimeError("HTTP dependencies are not installed; use the http extra") from error

    from coding_agent_neo.transports.http import create_app

    def configured_factory(*, interactive: bool):
        return backend_factory(config, interactive=interactive)

    app = create_app(backend_factory=configured_factory)
    uvicorn.run(
        app,
        host=DEFAULT_HOST,
        port=port,
        log_level=log_level,
        access_log=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse configuration and run only the Agent HTTP service."""

    args = build_parser().parse_args(argv)
    try:
        config = load_config(_config_values(args), config_path=args.config)
        run_server(config, port=args.port, log_level=args.log_level)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except RuntimeError as error:
        # Dependency/startup text is intentionally fixed and credential-free.
        if "HTTP dependencies" in str(error):
            print("startup failure: HTTP dependencies are not installed", file=sys.stderr)
        else:
            print("startup failure: RuntimeError", file=sys.stderr)
        return 1
    except Exception:
        print("startup failure: HTTP service could not start", file=sys.stderr)
        return 1
    return 0


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "build_parser", "main", "run_server"]
