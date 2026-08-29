from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_neo.config import ConfigError, load_config


def test_precedence_cli_environment_toml_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        'model = "toml-model"\nmax_steps = 9\napproval_mode = "deny"\n', encoding="utf-8"
    )
    config = load_config(
        {"model": "cli-model", "workspace": tmp_path},
        environ={"CODING_AGENT_NEO_MAX_STEPS": "7", "OPENAI_API_KEY": "sentinel"},
        config_path=config_path,
    )

    assert config.model == "cli-model"
    assert config.max_steps == 7
    assert config.approval_mode == "deny"
    assert config.max_tool_calls == 64
    assert config.api_key == "sentinel"
    assert "sentinel" not in repr(config)


def test_key_is_looked_up_by_name_and_errors_are_redacted(tmp_path: Path) -> None:
    secret = "super-secret-value"
    with pytest.raises(ConfigError) as exc_info:
        load_config(
            {"workspace": tmp_path, "api_key_env": secret},
            environ={secret: ""},
            config_path=tmp_path / "missing.toml",
        )

    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"max_steps": 0}, "positive integer"),
        ({"api_base": "file:///tmp/key"}, "HTTP"),
        ({"reserved_output_tokens": 100, "context_window": 100}, "smaller"),
    ],
)
def test_invalid_config_fails(values: dict[str, object], message: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(
            {"workspace": tmp_path, **values},
            environ={"OPENAI_API_KEY": "placeholder"},
            config_path=tmp_path / "missing.toml",
        )
