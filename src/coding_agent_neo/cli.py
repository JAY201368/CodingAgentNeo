"""Command-line placeholder for the CodingAgentNeo project.

Only argument discovery is available in the foundation milestone. The options
listed here are the public CLI surface reserved by the architecture; execution
is intentionally implemented by later tasks.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from coding_agent_neo import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the public command-line parser without starting an agent."""

    parser = argparse.ArgumentParser(
        prog="coding-agent-neo",
        description=(
            "CodingAgentNeo foundation CLI. Agent execution is not implemented "
            "in this milestone; use --help to inspect the reserved interface."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--task",
        help="Task text for a future non-interactive run (not implemented yet).",
    )
    parser.add_argument(
        "--model",
        help="Model name (reserved; execution is not implemented yet).",
    )
    parser.add_argument(
        "--api-base",
        help="OpenAI-compatible API base URL (reserved; no request is made).",
    )
    parser.add_argument(
        "--api-key-env",
        help="Environment variable containing an API key (the key is never an argument).",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace path for a future run (reserved; not accessed by this scaffold).",
    )
    parser.add_argument(
        "--session-dir",
        help="Session output directory for a future run (reserved; not written here).",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION",
        help="Resume a session in a future implementation (not implemented yet).",
    )
    parser.add_argument(
        "--approval-mode",
        choices=("ask", "auto", "deny"),
        help="Tool approval mode for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Alias for automatic approval in a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Maximum model steps for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        help="Maximum tool calls for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--max-wall-seconds",
        type=float,
        help="Maximum wall-clock seconds for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        help="Per-command timeout for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        help="Model context window for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--reserved-output-tokens",
        type=int,
        help="Output tokens reserved for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--model-output-limit",
        type=int,
        help="Model-visible tool output limit for a future run (reserved; not implemented yet).",
    )
    parser.add_argument(
        "--session-output-limit",
        type=int,
        help="Persisted tool output limit for a future run (reserved; not implemented yet).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI options and report the current scaffold status."""

    build_parser().parse_args(argv)
    print("CodingAgentNeo foundation scaffold: agent execution is not implemented yet.")
    return 0
