"""Configuration loading and validation for the CLI assembly layer."""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_CONFIG_PATH = Path(".coding-agent-neo.toml")
ENV_PREFIX = "CODING_AGENT_NEO_"


class ConfigError(ValueError):
    """A public, credential-free configuration diagnostic."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Fully resolved configuration; ``api_key`` is never serialized or repr'd."""

    model: str = "gpt-4o-mini"
    api_base: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    workspace: Path = Path(".")
    approval_mode: str = "ask"
    max_steps: int = 32
    max_tool_calls: int = 64
    max_wall_seconds: float = 900.0
    command_timeout: float = 120.0
    context_window: int = 128_000
    reserved_output_tokens: int = 4_096
    model_output_limit: int = 12_000
    session_output_limit: int = 50_000
    api_key: str = field(default="", repr=False, compare=False)


_CONFIG_FIELDS = frozenset(item.name for item in fields(AppConfig) if item.name != "api_key")
_INT_FIELDS = frozenset(
    {
        "max_steps",
        "max_tool_calls",
        "context_window",
        "reserved_output_tokens",
        "model_output_limit",
        "session_output_limit",
    }
)
_FLOAT_FIELDS = frozenset({"max_wall_seconds", "command_timeout"})
_PATH_FIELDS = frozenset({"workspace"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        raise ConfigError("configuration file could not be read") from None
    if not isinstance(value, dict):
        raise ConfigError("configuration file must contain a table")
    unknown = set(value) - _CONFIG_FIELDS
    if unknown:
        raise ConfigError("configuration file contains an unknown option")
    return value


def _environment_values(environ: Mapping[str, str]) -> dict[str, str]:
    if f"{ENV_PREFIX}SESSION_DIR" in environ:
        raise ConfigError("configuration contains an unknown option")
    values: dict[str, str] = {}
    for name in _CONFIG_FIELDS:
        key = f"{ENV_PREFIX}{name.upper()}"
        if key in environ:
            values[name] = environ[key]
    return values


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ConfigError(f"{name} must be a positive integer") from None
    if (
        isinstance(value, bool)
        or parsed <= 0
        or (isinstance(value, float) and not value.is_integer())
    ):
        raise ConfigError(f"{name} must be a positive integer")
    return parsed


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ConfigError(f"{name} must be a positive number") from None
    if isinstance(value, bool) or not math.isfinite(parsed) or parsed <= 0:
        raise ConfigError(f"{name} must be a positive number")
    return parsed


def _validated(values: Mapping[str, Any], environ: Mapping[str, str]) -> AppConfig:
    normalized = dict(values)
    for name in _INT_FIELDS:
        normalized[name] = _positive_int(normalized[name], name)
    for name in _FLOAT_FIELDS:
        normalized[name] = _positive_float(normalized[name], name)
    for name in ("model", "api_base", "api_key_env", "approval_mode"):
        value = normalized[name]
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ConfigError(f"{name} must be a non-empty string")
        normalized[name] = value.strip()
    if normalized["approval_mode"] not in {"ask", "auto", "deny"}:
        raise ConfigError("approval_mode must be ask, auto, or deny")
    parsed_url = urlparse(normalized["api_base"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ConfigError("api_base must be an HTTP(S) URL")
    if not _ENV_NAME.fullmatch(normalized["api_key_env"]):
        raise ConfigError("api_key_env must be an environment variable name")
    api_key = environ.get(normalized["api_key_env"], "")
    if not api_key:
        raise ConfigError("API key environment variable is missing or empty")
    if "\x00" in api_key:
        raise ConfigError("API key environment variable is invalid")

    for name in _PATH_FIELDS:
        value = normalized[name]
        if not isinstance(value, (str, os.PathLike)) or isinstance(value, bytes):
            raise ConfigError(f"{name} must be a path")
        path = Path(value).expanduser()
        if "\x00" in os.fspath(path):
            raise ConfigError(f"{name} must be a valid path")
        normalized[name] = path.resolve(strict=False)
    workspace = normalized["workspace"]
    if not workspace.exists() or not workspace.is_dir():
        raise ConfigError("workspace must be an existing directory")
    if normalized["reserved_output_tokens"] >= normalized["context_window"]:
        raise ConfigError("reserved_output_tokens must be smaller than context_window")
    if normalized["session_output_limit"] < 128:
        raise ConfigError("session_output_limit must be at least 128")
    normalized["api_key"] = api_key
    return AppConfig(**normalized)


def load_config(
    cli_values: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> AppConfig:
    """Resolve defaults < TOML < environment < explicit CLI values."""

    env = os.environ if environ is None else environ
    cli = {key: value for key, value in dict(cli_values or {}).items() if value is not None}
    unknown = set(cli) - _CONFIG_FIELDS
    if unknown:
        raise ConfigError("CLI contains an unknown configuration option")
    selected_path = config_path
    if selected_path is None:
        selected_path = env.get(f"{ENV_PREFIX}CONFIG", DEFAULT_CONFIG_PATH)
    path = Path(selected_path).expanduser()
    defaults = {item.name: item.default for item in fields(AppConfig) if item.name != "api_key"}
    values = defaults | _read_toml(path) | _environment_values(env) | cli
    return _validated(values, env)


Config = AppConfig
load_configuration = load_config

__all__ = [
    "AppConfig",
    "Config",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "load_config",
    "load_configuration",
]
