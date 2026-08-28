"""Foundation CLI tests."""

import pytest

from coding_agent_neo.cli import build_parser


def test_parser_exposes_architecture_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--task",
            "inspect",
            "--model",
            "model-name",
            "--approval-mode",
            "ask",
            "--max-steps",
            "3",
            "--max-wall-seconds",
            "10",
        ]
    )

    assert args.task == "inspect"
    assert args.model == "model-name"
    assert args.approval_mode == "ask"
    assert args.max_steps == 3
    assert args.max_wall_seconds == 10


def test_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])

    assert exc_info.value.code == 0
    assert "not implemented" in capsys.readouterr().out.lower()
